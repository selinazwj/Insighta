import json
import os
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from urllib.parse import parse_qs, urlsplit

os.environ['DATABASE_URL'] = 'sqlite:////private/tmp/insighta_booking_calendar_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP'] = '1'

from fastapi.testclient import TestClient
from api import main
from app.booking_calendar import booked_events, calendar_document
from app.models import Response, Survey, User, GoogleCalendarConnection, GoogleCalendarEvent, BackgroundJob
from app import google_calendar as calendar_oauth


class BookingCalendarTest(unittest.TestCase):
    def setUp(self):
        calendar_env = patch.dict(os.environ, {
            'GOOGLE_CALENDAR_CLIENT_ID': 'calendar-client',
            'GOOGLE_CALENDAR_CLIENT_SECRET': 'calendar-secret',
            'GOOGLE_CALENDAR_TOKEN_KEY': Fernet.generate_key().decode(),
            'BASE_URL': 'http://testserver',
        })
        calendar_env.start()
        self.addCleanup(calendar_env.stop)
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        self.db = main.SessionLocal()
        self.addCleanup(self.db.close)
        self.publisher = User(email='researcher@example.com', password='x')
        self.participant = User(email='participant@example.com', password='x')
        self.other = User(email='other@example.com', password='x')
        self.db.add_all([self.publisher, self.participant, self.other])
        self.db.flush()
        self.slots = [
            dict(date='2026-10-24', start='10:00', end='10:30', label='Sat, Oct 24, 10:00 - 10:30', timezone='America/New_York'),
            dict(date='2026-11-07', start='23:30', end='00:30', label='Sat, Nov 7, 23:30 - 00:30', timezone='America/New_York'),
        ]
        self.survey = Survey(publisher_id=self.publisher.id, title='Study, with; details', description='Test', category='research', form_url='https://example.com/meeting', task_type='interview', target_participation_format='Video interview', status='published', availability_slots=json.dumps(self.slots), reward_amount=10, target_responses=5, estimated_time=30)
        self.db.add(self.survey)
        self.db.flush()
        self.booking = Response(survey_id=self.survey.id, participant_id=self.participant.id, status='scheduled', booking_slot=json.dumps([slot['label'] for slot in self.slots]))
        self.db.add(self.booking)
        self.db.commit()
        self.path = f'/bookings/{self.booking.id}/calendar.ics'
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        self.db.close()

    def test_export_permissions_login_and_multiple_sessions(self):
        response = self.client.get(self.path, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn('next=', response.headers['location'])
        self.client.cookies.set('user_id', str(self.other.id))
        self.assertEqual(self.client.get(self.path).status_code, 404)
        for user in [self.participant, self.publisher]:
            self.client.cookies.set('user_id', str(user.id))
            response = self.client.get(self.path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment;', response.headers['content-disposition'])
            self.assertEqual(response.text.count('BEGIN:VEVENT'), 2)
            self.assertIn('DTSTART:20261024T140000Z', response.text)
            self.assertIn('DTEND:20261108T053000Z', response.text)
            self.assertIn(r'SUMMARY:Study\, with\; details', response.text)
            self.assertIn('https://example.com/meeting', response.text)

    def test_both_confirmation_emails_include_calendar(self):
        with patch.object(main, 'send_email', return_value=(True, None)) as send:
            self.assertEqual(main._send_booking_confirmation_emails(self.db, self.survey, self.participant, self.booking.booking_slot), 2)
        for call in send.call_args_list:
            self.assertIn('Add to your calendar', call.args[2])
            self.assertIn(self.path, call.args[2])
            self.assertEqual(call.args[2].count('Add to Google Calendar'), 2)
            self.assertIn('calendar.google.com/calendar/render?', call.args[2])
            self.assertIn('&amp;dates=', call.args[2])
        self.assertEqual(main._booking_calendar_path(self.survey, self.booking), self.path)

    def test_booking_pages_and_in_person_location(self):
        self.survey.share_slug = 'calendar-test'
        self.survey.task_type = 'in_person'
        self.survey.target_participation_format = 'In person'
        self.survey.interview_location = 'Room 210, Research Center'
        self.db.commit()
        self.client.cookies.set('user_id', str(self.participant.id))
        studies = self.client.get('/my-studies')
        self.assertEqual(studies.status_code, 200)
        self.assertIn(self.path, studies.text)
        self.assertIn('Add to your calendar', studies.text)
        self.assertIn('google_calendar_links', studies.text)
        listing = self.client.get('/r/calendar-test')
        self.assertEqual(listing.status_code, 200)
        self.assertIn(f'href="{self.path}"', listing.text)
        self.assertEqual(listing.text.count('Add to Google Calendar'), 2)
        self.assertIn(r'LOCATION:Room 210\, Research Center', self.client.get(self.path).text)

    def test_google_calendar_links_preserve_sessions_and_details(self):
        self.survey.title = 'Interview & research / 研究'
        links = main._booking_google_calendar_links(self.survey, self.booking)
        self.assertEqual(len(links), 2)
        first = parse_qs(urlsplit(links[0]['url']).query)
        second = parse_qs(urlsplit(links[1]['url']).query)
        self.assertEqual(first['action'], ['TEMPLATE'])
        self.assertEqual(first['text'], [self.survey.title])
        self.assertEqual(first['dates'], ['20261024T140000Z/20261024T143000Z'])
        self.assertEqual(second['dates'], ['20261108T043000Z/20261108T053000Z'])
        self.assertEqual(first['location'], ['https://example.com/meeting'])
        self.assertIn(self.slots[0]['label'], first['details'][0])
        self.assertIn('Session 2', links[1]['label'])
        self.survey.availability_slots = '[]'
        self.assertEqual(main._booking_google_calendar_links(self.survey, self.booking), [])

    def mock_google(self):
        self.google_requests = []
        self.remote_events = {}
        def handler(request):
            self.google_requests.append(request)
            if request.url.path == '/token':
                return httpx.Response(200, json={'access_token': 'access-test', 'refresh_token': 'refresh-test', 'expires_in': 3600, 'scope': calendar_oauth.SCOPE})
            if request.url.path.endswith('/userinfo'):
                return httpx.Response(200, json={'id': 'google-123', 'email': 'connected@example.com'})
            if request.url.path == '/revoke':
                return httpx.Response(200)
            if request.method == 'POST':
                body = json.loads(request.content)
                self.remote_events[body['id']] = body
                return httpx.Response(200, json={'id': body['id']})
            event_id = request.url.path.rsplit('/', 1)[-1]
            if event_id not in self.remote_events:
                return httpx.Response(404)
            if request.method == 'DELETE':
                del self.remote_events[event_id]
                return httpx.Response(204)
            self.remote_events[event_id] = json.loads(request.content)
            return httpx.Response(200, json={'id': event_id})
        actual_client = httpx.Client
        return patch.object(calendar_oauth.httpx, 'Client', side_effect=lambda **kwargs: actual_client(transport=httpx.MockTransport(handler), **kwargs))

    def authorize_calendar(self):
        self.client.cookies.set('user_id', str(self.participant.id))
        start = self.client.get('/auth/google/calendar/connect', follow_redirects=False)
        self.assertEqual(start.status_code, 303)
        params = parse_qs(urlsplit(start.headers['location']).query)
        self.assertEqual(params['code_challenge_method'], ['S256'])
        self.assertIn(calendar_oauth.SCOPE, params['scope'][0])
        callback = self.client.get('/auth/google/calendar/callback', params={'state': params['state'][0], 'code': 'test-code'}, follow_redirects=False)
        self.assertEqual(callback.status_code, 303)
        self.assertIn('calendar=connected', callback.headers['location'])

    def test_oauth_encryption_backfill_and_replay_rejection(self):
        with self.mock_google():
            self.authorize_calendar()
        self.db.expire_all()
        connection = self.db.get(GoogleCalendarConnection, self.participant.id)
        self.assertNotIn('refresh-test', connection.refresh_token_encrypted)
        self.assertEqual(calendar_oauth.cipher().decrypt(connection.refresh_token_encrypted.encode()), b'refresh-test')
        self.assertEqual(self.db.query(BackgroundJob).filter_by(job_type=calendar_oauth.JOB_TYPE).count(), 1)
        self.assertEqual(self.client.get('/auth/google/calendar/callback?state=invalid&code=x').status_code, 400)
        page = self.client.get('/my-studies')
        self.assertIn('connected@example.com', page.text)
        self.assertIn('Disconnect Google Calendar', page.text)

    def test_oauth_user_binding_and_cancel(self):
        self.client.cookies.set('user_id', str(self.participant.id))
        start = self.client.get('/auth/google/calendar/connect', follow_redirects=False)
        state = parse_qs(urlsplit(start.headers['location']).query)['state'][0]
        self.client.cookies.set('user_id', str(self.other.id))
        self.assertEqual(self.client.get('/auth/google/calendar/callback', params={'state': state, 'code': 'x'}).status_code, 400)
        self.client.cookies.set('user_id', str(self.participant.id))
        cancelled = self.client.get('/auth/google/calendar/callback', params={'state': state, 'error': 'access_denied'}, follow_redirects=False)
        self.assertIn('calendar=cancelled', cancelled.headers['location'])
        self.assertEqual(self.client.get('/auth/google/calendar/callback', params={'state': state, 'code': 'x'}).status_code, 400)

    def test_sync_refresh_idempotency_reschedule_and_disconnect(self):
        with self.mock_google():
            self.authorize_calendar()
            job = SimpleNamespace(payload_json={'user_id': self.participant.id, 'response_id': self.booking.id})
            self.db.expire_all()
            connection = self.db.get(GoogleCalendarConnection, self.participant.id)
            connection.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=5)
            self.db.commit()
            calendar_oauth.sync_booking(self.db, job)
            self.assertEqual(len(self.remote_events), 2)
            self.assertEqual(self.db.query(GoogleCalendarEvent).count(), 2)
            calendar_oauth.sync_booking(self.db, job)
            self.assertEqual(len(self.remote_events), 2)
            self.slots[0]['start'] = '11:00'
            self.slots[0]['end'] = '11:30'
            self.survey.availability_slots = json.dumps(self.slots)
            self.booking.booking_slot = self.slots[0]['label']
            self.db.commit()
            calendar_oauth.sync_booking(self.db, job)
            self.assertEqual(len(self.remote_events), 1)
            self.assertIn('15:00:00+00:00', list(self.remote_events.values())[0]['start']['dateTime'])
            blocked = self.client.post('/auth/google/calendar/disconnect', headers={'origin': 'https://evil.example'}, follow_redirects=False)
            self.assertEqual(blocked.status_code, 403)
            disconnected = self.client.post('/auth/google/calendar/disconnect', headers={'origin': 'http://testserver'}, follow_redirects=False)
            self.assertEqual(disconnected.status_code, 303)
            self.db.expire_all()
            self.assertIsNone(self.db.get(GoogleCalendarConnection, self.participant.id))
            self.assertEqual(len(self.remote_events), 1)

    def test_synced_booking_does_not_block_study_deletion(self):
        with self.mock_google():
            self.authorize_calendar()
            calendar_oauth.sync_booking(self.db, SimpleNamespace(payload_json={'user_id': self.participant.id, 'response_id': self.booking.id}))
        self.assertEqual(self.db.query(GoogleCalendarEvent).count(), 2)
        main._delete_survey_tree(self.db, self.survey)
        self.assertEqual(self.db.query(GoogleCalendarEvent).count(), 0)
        self.assertEqual(self.db.query(Response).count(), 0)

    def test_bad_or_ambiguous_slots_do_not_guess_dates(self):
        self.assertEqual(booked_events('bad json', ['anything']), [])
        self.assertEqual(booked_events(json.dumps(self.slots * 2), [self.slots[0]['label']]), [])
        self.assertEqual(booked_events(json.dumps(self.slots), ['unknown']), [])
        self.survey.availability_slots = '[]'
        self.db.commit()
        self.client.cookies.set('user_id', str(self.participant.id))
        self.assertEqual(self.client.get(self.path).status_code, 409)

    def test_legacy_local_times_and_unicode_folding(self):
        slot = dict(self.slots[0])
        del slot['timezone']
        events = booked_events(json.dumps([slot]), [slot['label']])
        document = calendar_document(1, '研究' * 60 + '\nEND:VEVENT', events, 'Room 2', 'https://example.com')
        self.assertIn('DTSTART:20261024T100000\r\n', document)
        self.assertIn('Time zone was not recorded', document.replace('\r\n ', ''))
        self.assertEqual(document.count('\r\nEND:VEVENT\r\n'), 1)
        self.assertTrue(all(len(line.encode()) <= 75 for line in document.split('\r\n')))


if __name__ == '__main__':
    unittest.main()
