"""Opt-in Google Calendar authorization and retryable booking synchronization."""

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_

from app.booking_calendar import booked_events
from app.database import get_db
from app.jobs.service import enqueue_job
from app.models import GoogleCalendarConnection, GoogleCalendarEvent, Response, Survey

SCOPE = 'https://www.googleapis.com/auth/calendar.events.owned'
JOB_TYPE = 'google_calendar_sync'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
EVENTS_URL = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
LIVE_TYPES = {'interview', 'online_interview', 'online-interview', 'remote_interview', 'in_person', 'in-person', 'in_person_study'}


def configuration():
    return (
        os.environ.get('GOOGLE_CALENDAR_CLIENT_ID', ''),
        os.environ.get('GOOGLE_CALENDAR_CLIENT_SECRET', ''),
        os.environ.get('GOOGLE_CALENDAR_TOKEN_KEY', ''),
    )


def cipher():
    key = configuration()[2]
    if not key:
        raise ValueError('Google Calendar token encryption is not configured')
    return Fernet(key.encode())


def calendar_status(db, user_id):
    configured = all(configuration())
    connection = db.get(GoogleCalendarConnection, user_id) if configured else None
    return {
        'configured': configured,
        'connected': bool(connection and not connection.needs_reconnect),
        'email': connection.google_email if connection else '',
        'reconnect': bool(connection and connection.needs_reconnect),
    }


def queue_calendar_sync(db, response):
    survey = db.get(Survey, response.survey_id)
    if not survey or survey.task_type not in LIVE_TYPES or not all(configuration()):
        return
    for user_id in {response.participant_id, survey.publisher_id}:
        connection = db.get(GoogleCalendarConnection, user_id)
        if connection and not connection.needs_reconnect:
            enqueue_job(db, job_type=JOB_TYPE, payload={'user_id': user_id, 'response_id': response.id})


def access_token(db, connection, client):
    encryption = cipher()
    if connection.expires_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60):
        return encryption.decrypt(connection.access_token_encrypted.encode()).decode()
    client_id, client_secret, _ = configuration()
    result = client.post(TOKEN_URL, data={
        'client_id': client_id, 'client_secret': client_secret,
        'refresh_token': encryption.decrypt(connection.refresh_token_encrypted.encode()).decode(),
        'grant_type': 'refresh_token',
    })
    if result.status_code in {400, 401}:
        connection.needs_reconnect = True
        db.commit()
        raise ValueError('Google Calendar authorization expired. Reconnect your calendar.')
    result.raise_for_status()
    token = result.json()
    connection.access_token_encrypted = encryption.encrypt(token['access_token'].encode()).decode()
    connection.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(token.get('expires_in', 3600)))
    if token.get('refresh_token'):
        connection.refresh_token_encrypted = encryption.encrypt(token['refresh_token'].encode()).decode()
    db.commit()
    return token['access_token']


def sync_booking(db, job):
    payload = job.payload_json or {}
    connection = db.get(GoogleCalendarConnection, int(payload['user_id']))
    response = db.get(Response, int(payload['response_id']))
    survey = db.get(Survey, response.survey_id) if response else None
    if not connection or connection.needs_reconnect or not survey:
        return
    if connection.user_id not in {response.participant_id, survey.publisher_id} or survey.task_type not in LIVE_TYPES:
        return
    try:
        labels = json.loads(response.booking_slot or '[]')
    except ValueError:
        labels = [response.booking_slot] if response.booking_slot else []
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list):
        raise ValueError('Booking session data is invalid')
    events = booked_events(survey.availability_slots, labels)
    if response.status in {'cancelled', 'canceled', 'skipped'}:
        events = []
    elif labels and (not events or any(not start.tzinfo for _, start, _ in events)):
        raise ValueError('Booking needs a complete date and time zone before automatic calendar sync.')
    rows = db.query(GoogleCalendarEvent).filter_by(user_id=connection.user_id, response_id=response.id).all()
    rows = {row.session_index: row for row in rows}
    location = (survey.interview_location or '').strip() or (survey.form_url if survey.form_url != '__builtin__' else '')
    with httpx.Client(timeout=15) as client:
        token = access_token(db, connection, client)
        headers = {'Authorization': f'Bearer {token}'}
        for index, (label, start, end) in enumerate(events):
            row = rows.pop(index, None)
            identity = hashlib.sha256(f'insighta:{connection.google_subject}:{connection.user_id}:{response.id}:{index}'.encode()).hexdigest()
            event_id = row.event_id if row and row.google_subject == connection.google_subject else identity
            body = {
                'summary': survey.title,
                'location': location,
                'description': f'{label}\n{location}\n{os.environ.get("BASE_URL", "https://insightaco.org").rstrip("/")}/my-studies',
                'start': {'dateTime': start.isoformat()}, 'end': {'dateTime': end.isoformat()},
                'extendedProperties': {'private': {'insightaBookingId': str(response.id)}},
            }
            result = client.put(f'{EVENTS_URL}/{event_id}', headers=headers, json=body)
            if result.status_code == 404:
                result = client.post(EVENTS_URL, headers=headers, json={**body, 'id': event_id})
                if result.status_code == 409:
                    result = client.put(f'{EVENTS_URL}/{event_id}', headers=headers, json=body)
            if result.status_code == 401:
                connection.needs_reconnect = True
                db.commit()
            result.raise_for_status()
            if not row:
                row = GoogleCalendarEvent(user_id=connection.user_id, response_id=response.id, session_index=index)
                db.add(row)
            row.event_id, row.google_subject = event_id, connection.google_subject
            db.commit()
        for row in rows.values():
            if row.google_subject == connection.google_subject:
                result = client.delete(f'{EVENTS_URL}/{row.event_id}', headers=headers)
                if result.status_code not in {404, 410}:
                    result.raise_for_status()
            db.delete(row)
            db.commit()


def register_calendar_routes(app, get_current_user):
    def home(request):
        target = request.query_params.get('next')
        return target if target in {'/my-studies', '/publisher/schedule'} else '/my-studies'

    def callback_url():
        return os.environ.get('BASE_URL', 'https://insightaco.org').rstrip('/') + '/auth/google/calendar/callback'

    def cookie_options(request):
        return dict(httponly=True, secure=request.url.scheme == 'https', samesite='lax', path='/auth/google/calendar')

    def csrf(request):
        origin = request.headers.get('origin')
        expected = urlsplit(str(request.base_url))
        if not origin or urlsplit(origin).netloc != expected.netloc or urlsplit(origin).scheme != expected.scheme:
            raise HTTPException(403, 'Request must come from this website')

    @app.get('/auth/google/calendar/connect')
    def connect(request: Request, current_user=Depends(get_current_user)):
        if not all(configuration()):
            raise HTTPException(503, 'Google Calendar connection is not configured yet')
        nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        try:
            state = cipher().encrypt(json.dumps({'uid': current_user.id, 'nonce': nonce, 'verifier': verifier, 'next': home(request)}).encode()).decode()
        except ValueError:
            raise HTTPException(503, 'Google Calendar encryption configuration is invalid')
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        result = RedirectResponse(AUTH_URL + '?' + urlencode({
            'client_id': configuration()[0], 'redirect_uri': callback_url(), 'response_type': 'code',
            'scope': f'openid email {SCOPE}', 'state': state, 'access_type': 'offline', 'prompt': 'consent',
            'code_challenge': challenge, 'code_challenge_method': 'S256',
        }), status_code=303)
        result.set_cookie('calendar_oauth_nonce', nonce, max_age=600, **cookie_options(request))
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.get('/auth/google/calendar/callback')
    def callback(request: Request, code: str = '', state: str = '', error: str = '', current_user=Depends(get_current_user), db=Depends(get_db)):
        try:
            data = json.loads(cipher().decrypt(state.encode(), ttl=600))
            nonce = request.cookies.get('calendar_oauth_nonce', '')
            if data['uid'] != current_user.id or not nonce or not hmac.compare_digest(data['nonce'], nonce):
                raise ValueError('Invalid authorization state')
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Calendar authorization expired. Please connect again.')
        target = data['next'] if data.get('next') in {'/my-studies', '/publisher/schedule'} else '/my-studies'
        status = 'cancelled' if error else 'connected'
        if not error:
            if not code:
                raise HTTPException(400, 'Missing authorization code')
            with httpx.Client(timeout=15) as client:
                result = client.post(TOKEN_URL, data={
                    'client_id': configuration()[0], 'client_secret': configuration()[1],
                    'code': code, 'code_verifier': data['verifier'],
                    'redirect_uri': callback_url(), 'grant_type': 'authorization_code',
                })
                if not result.is_success:
                    raise HTTPException(502, 'Google Calendar authorization failed. Please try again.')
                tokens = result.json()
                if SCOPE not in tokens.get('scope', '').split() or not tokens.get('access_token') or not tokens.get('refresh_token'):
                    raise HTTPException(400, 'Calendar permission was not granted. Please connect again and allow calendar access.')
                identity = client.get('https://www.googleapis.com/oauth2/v2/userinfo', headers={'Authorization': f'Bearer {tokens["access_token"]}'})
                if not identity.is_success or not identity.json().get('id') or not identity.json().get('email'):
                    raise HTTPException(502, 'Could not verify the connected Google account')
                account = identity.json()
            connection = db.get(GoogleCalendarConnection, current_user.id)
            if connection and connection.google_subject != account['id']:
                raise HTTPException(409, 'Disconnect your current Google Calendar before choosing a different account.')
            if not connection:
                connection = GoogleCalendarConnection(user_id=current_user.id)
                db.add(connection)
            encryption = cipher()
            connection.google_subject, connection.google_email = account['id'], account['email']
            connection.refresh_token_encrypted = encryption.encrypt(tokens['refresh_token'].encode()).decode()
            connection.access_token_encrypted = encryption.encrypt(tokens['access_token'].encode()).decode()
            connection.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(tokens.get('expires_in', 3600)))
            connection.needs_reconnect = False
            db.flush()
            bookings = db.query(Response).join(Survey, Survey.id == Response.survey_id).filter(
                or_(Response.participant_id == current_user.id, Survey.publisher_id == current_user.id),
                Response.status == 'scheduled', Response.booking_slot.isnot(None),
            ).all()
            for booking in bookings:
                enqueue_job(db, job_type=JOB_TYPE, payload={'user_id': current_user.id, 'response_id': booking.id})
            db.commit()
        result = RedirectResponse(f'{target}?calendar={status}', status_code=303)
        result.delete_cookie('calendar_oauth_nonce', path='/auth/google/calendar')
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.post('/auth/google/calendar/disconnect')
    def disconnect(request: Request, current_user=Depends(get_current_user), db=Depends(get_db)):
        csrf(request)
        connection = db.get(GoogleCalendarConnection, current_user.id)
        if connection:
            with httpx.Client(timeout=15) as client:
                result = client.post('https://oauth2.googleapis.com/revoke', data={'token': cipher().decrypt(connection.refresh_token_encrypted.encode()).decode()})
                if result.status_code not in {200, 400}:
                    raise HTTPException(502, 'Google Calendar could not be disconnected. Please try again.')
            db.delete(connection)
            db.commit()
        return RedirectResponse(home(request) + '?calendar=disconnected', status_code=303)
