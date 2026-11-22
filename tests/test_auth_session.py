import os
import unittest
from unittest.mock import patch

os.environ.setdefault('DATABASE_URL','sqlite:////private/tmp/insighta_auth_release_test.db')
os.environ.setdefault('INSIGHTA_AUTO_SCHEMA_BOOTSTRAP','1')
from fastapi.testclient import TestClient
from api import main
from app.auth_session import sign_session, verify_session, SESSION_MAX_AGE
from app.models import User


class AuthSessionTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine);main.Base.metadata.create_all(main.engine)
        self.db=main.SessionLocal();self.addCleanup(self.db.close)
        self.user=User(email='session@example.invalid',password=main.pwd_context.hash('local-test-only'))
        self.db.add(self.user);self.db.commit()
        self.client=TestClient(main.app,base_url='https://testserver');self.addCleanup(self.client.close)

    def test_tampered_legacy_expired_and_future_cookies_never_authenticate(self):
        valid=sign_session(self.user.id)
        values=[str(self.user.id),valid[:-1]+('0' if valid[-1]!='0' else '1'),
                sign_session(self.user.id,now=1),sign_session(self.user.id,now=9999999999),
                'not-an-id','9'*1000]
        for value in values:
            self.client.cookies.set('user_id',value)
            with self.subTest(value=value[:12]):
                self.assertEqual(self.client.get('/api/wallet').status_code,401)
                self.assertEqual(self.client.post('/api/withdraw').status_code,401)
                self.assertEqual(self.client.get('/publisher/wallet',follow_redirects=False).status_code,303)

    def test_real_login_issues_secure_cookie_and_logout_clears_access(self):
        with patch.object(main,'check_rate_limit',return_value=True):
            result=self.client.post('/login',data={'email':self.user.email,'password':'local-test-only','role':'researcher'},follow_redirects=False)
        self.assertEqual(result.status_code,303)
        cookie=self.client.cookies.get('user_id')
        self.assertEqual(verify_session(cookie),str(self.user.id))
        header=next(v for v in result.headers.get_list('set-cookie') if v.startswith('user_id='))
        for flag in ['HttpOnly','Secure','SameSite=lax']:
            self.assertIn(flag,header)
        response=self.client.get('/api/wallet');self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response.headers['cache-control'])
        self.client.get('/logout',follow_redirects=False)
        self.assertEqual(self.client.get('/api/wallet').status_code,401)

    def test_expiry_and_other_cookies_are_preserved(self):
        token=sign_session(self.user.id,now=100)
        self.assertEqual(verify_session(token,now=100),str(self.user.id))
        self.assertIsNone(verify_session(token,now=100+SESSION_MAX_AGE))
        self.client.cookies.set('oauth_state','preserved')
        self.client.cookies.set('user_id',sign_session(self.user.id))
        self.assertEqual(self.client.get('/api/wallet').status_code,200)
        self.assertEqual(self.client.cookies.get('oauth_state'),'preserved')

    def test_cross_site_writes_are_refused_before_mutation(self):
        self.client.cookies.set('user_id',sign_session(self.user.id))
        result=self.client.post('/api/wallet/billing',json={'name':'Injected'},headers={'origin':'https://other.invalid'})
        self.assertEqual(result.status_code,403)
        result=self.client.post('/api/wallet/billing',json={'name':'Owner'},headers={'origin':'https://testserver'})
        self.assertEqual(result.status_code,200,result.text)
