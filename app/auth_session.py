"""Authenticate the legacy user_id cookie before any route can consume it."""
import hashlib
import hmac
import os
import re
import secrets
import time

SESSION_MAX_AGE = 60 * 60 * 24 * 30
_secret = os.environ.get('SESSION_SECRET', '')
if not _secret:
    if os.environ.get('RENDER') or os.environ.get('INSIGHTA_ENV') in {'production', 'staging'}:
        raise RuntimeError('SESSION_SECRET must be configured for hosted deployments.')
    _secret = secrets.token_urlsafe(48)  # Local development only; restart signs out.
if len(_secret) < 32:
    raise RuntimeError('SESSION_SECRET must contain at least 32 characters.')


def sign_session(user_id, *, now=None):
    user_id = int(user_id)
    if not 0 < user_id <= 2**63-1:
        raise ValueError('Invalid user ID')
    payload = f'v1.{user_id}.{int(time.time() if now is None else now)}'
    digest = hmac.new(_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}.{digest}'


def verify_session(value, *, now=None):
    if not isinstance(value, str) or len(value) > 150:
        return None
    if not re.fullmatch(r'v1\.[1-9][0-9]{0,18}\.[0-9]{1,12}\.[0-9a-f]{64}', value):
        return None
    payload, signature = value.rsplit('.', 1)
    expected = hmac.new(_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    _, user_id, issued = payload.split('.')
    age = int(time.time() if now is None else now)-int(issued)
    return user_id if 0 <= age < SESSION_MAX_AGE and int(user_id) <= 2**63-1 else None


class SignedSessionMiddleware:
    """Only verified identities reach legacy Cookie dependencies/request.cookies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in {'http', 'websocket'}:
            from starlette.requests import HTTPConnection
            connection = HTTPConnection(scope)
            identity = verify_session(connection.cookies.get('user_id'))
            if identity and scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
                origin = connection.headers.get('origin')
                # Browser writes must originate from this deployment. CLI/provider
                # calls without browser Origin retain their existing authentication.
                from urllib.parse import urlsplit
                allowed = {connection.url.netloc}
                configured = urlsplit(os.environ.get('BASE_URL', '')).netloc
                if configured:
                    allowed.add(configured)
                if origin and (urlsplit(origin).scheme not in {'http', 'https'} or urlsplit(origin).netloc not in allowed):
                    from starlette.responses import JSONResponse
                    return await JSONResponse({'detail':'Cross-site account changes are not allowed.'}, status_code=403)(scope, receive, send)
            headers = [(k,v) for k,v in scope['headers'] if k.lower() != b'cookie']
            # Preserve all other cookies (OAuth state, role, return URL).
            cookies = {k:v for k,v in connection.cookies.items() if k != 'user_id'}
            from http.cookies import SimpleCookie, CookieError
            clean = SimpleCookie()
            for name, value in cookies.items():
                try:
                    clean[name] = value
                except CookieError:
                    continue
            if identity:
                clean['user_id'] = identity
            if clean:
                headers.append((b'cookie', clean.output(header='', sep=';').strip().encode('latin-1')))
            scope = dict(scope, headers=headers)
        await self.app(scope, receive, send)
