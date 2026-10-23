import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ['DATABASE_URL'] = 'sqlite:////private/tmp/insighta_publish_flow_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP'] = '1'

from fastapi.testclient import TestClient
from sqlalchemy import inspect
from api import main
from app.models import Survey, User


class PublishFlowTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        with main.SessionLocal() as db:
            self.password = 'test-password'
            user = User(email='researcher@example.com', username='Researcher', password=main.pwd_context.hash(self.password))
            other = User(email='other@example.com', username='Other', password=main.pwd_context.hash(self.password))
            db.add_all([user, other])
            db.commit()
            self.user_id, self.other_id = user.id, other.id
        self.client = TestClient(main.app)
        self.client.cookies.set('user_id', str(self.user_id))
        self.addCleanup(self.client.close)
        self.data = dict(title='Color preferences', description='A study of favorite colors.',
                         form_url='https://example.com/survey', category='research', estimated_time='5',
                         target_responses='20', per_person_gross='5', incentive_type='cash',
                         publish_token='publish-test-token-123456789',
                         target_niche_requirements='Has used a budgeting app for six months')

    def submit(self, **overrides):
        return self.client.post('/publish', data={**self.data, **overrides}, headers={'Accept': 'application/json'}, follow_redirects=False)

    def test_checkout_failure_retry_reuses_draft_and_payment_key(self):
        checkout = SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create',
            side_effect=[main.stripe.error.APIConnectionError('private provider diagnostic'), checkout],
        ) as create:
            failed = self.submit()
            self.assertEqual(failed.status_code, 502)
            self.assertNotIn('private provider diagnostic', failed.text)
            survey_id = failed.json()['survey_id']
            retried = self.submit()
            self.assertEqual(retried.status_code, 200)
            self.assertEqual(retried.json()['survey_id'], survey_id)
            self.assertEqual(retried.json()['redirect_url'], checkout.url)
            self.assertEqual(create.call_args_list[0].kwargs['idempotency_key'], create.call_args_list[1].kwargs['idempotency_key'])
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 1)
            survey = db.get(Survey, survey_id)
            self.assertEqual(survey.status, 'draft')
            self.assertEqual(survey.target_niche_requirements, self.data['target_niche_requirements'])

    def test_simultaneous_submissions_create_only_one_listing(self):
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.submit(), range(2)))
        self.assertTrue(all(r.status_code in {200, 409} for r in results), [r.text for r in results])
        self.assertEqual(len({r.json()['survey_id'] for r in results}), 1)
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 1)

    def test_published_retry_does_not_change_listing_or_payment(self):
        with patch.object(main.stripe, 'api_key', None):
            first = self.submit(incentive_type='raffle')
            second = self.submit(title='Changed by a retry')
        self.assertEqual(first.json()['survey_id'], second.json()['survey_id'])
        with main.SessionLocal() as db:
            survey = db.query(Survey).one()
            self.assertEqual(survey.title, self.data['title'])
            self.assertEqual(survey.status, 'published')
            self.assertEqual(survey.payment_status, 'paid')

    def test_token_is_scoped_to_owner_and_cannot_update_another_users_draft(self):
        with patch.object(main.stripe, 'api_key', None):
            first = self.submit(incentive_type='raffle')
            self.client.cookies.set('user_id', str(self.other_id))
            forbidden = self.submit(existing_survey_id=first.json()['survey_id'])
            self.assertEqual(forbidden.status_code, 404)
            second = self.submit(incentive_type='raffle')
            self.assertNotEqual(first.json()['survey_id'], second.json()['survey_id'])

    def test_invalid_participant_count_creates_no_draft(self):
        self.assertEqual(self.submit(target_responses='0').status_code, 400)
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 0)

    def test_builtin_publish_keeps_existing_questions_and_html_redirect(self):
        created = self.client.post('/surveys/create-builtin', follow_redirects=False)
        survey_id = int(created.headers['location'].split('/')[2])
        question = self.client.post(f'/surveys/{survey_id}/questions', json={
            'question_text': 'Favorite color?', 'question_type': 'text', 'is_required': True,
        })
        self.assertEqual(question.status_code, 200)
        with patch.object(main.stripe, 'api_key', None):
            result = self.client.post('/publish', data={
                **self.data, 'existing_survey_id': survey_id, 'form_url': '__builtin__', 'incentive_type': 'raffle',
            }, follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers['location'], '/publisher')
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 1)
            self.assertEqual(db.get(Survey, survey_id).status, 'published')
        loaded = self.client.get(f'/surveys/{survey_id}/questions')
        self.assertEqual(loaded.json()[0]['question_text'], 'Favorite color?')

    def test_publish_page_has_token_and_database_has_unique_index(self):
        page = self.client.get('/publish')
        self.assertEqual(page.status_code, 200)
        self.assertRegex(page.text, r'name="publish_token" value="[A-Za-z0-9_-]{32}"')
        indexes = inspect(main.engine).get_indexes('surveys')
        self.assertTrue(any(i['name'] == 'ix_surveys_publish_token' and i['unique'] for i in indexes))

    def test_ai_fallback_does_not_return_provider_errors(self):
        with patch.object(main, '_anthropic_ai_fill', new=AsyncMock(side_effect=RuntimeError('secret billing error'))):
            result = self.client.post('/api/ai-fill', json={'prompt': 'Favorite colors, 20 responses'})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('secret billing error', result.text)
        self.assertEqual(result.json()['target_responses'], 20)
        self.assertIn('review', result.json()['warning'])

    def test_login_with_email_or_unique_username(self):
        for identifier in ['researcher@example.com', ' Researcher ']:
            with self.subTest(identifier=identifier), patch.object(main, 'check_rate_limit', return_value=True):
                result = self.client.post('/login', data={'email': identifier, 'password': self.password}, follow_redirects=False)
                self.assertEqual(result.status_code, 303)
                self.assertIn('user_id=', result.headers.get('set-cookie', ''))

    def test_ambiguous_username_and_blocked_account_cannot_log_in(self):
        with main.SessionLocal() as db:
            db.get(User, self.other_id).username = 'researcher'
            db.commit()
        with patch.object(main, 'check_rate_limit', return_value=True):
            result = self.client.post('/login', data={'email': 'Researcher', 'password': self.password})
            self.assertIn('Invalid email / username or password', result.text)
        with main.SessionLocal() as db:
            db.get(User, self.other_id).username = 'Other'
            db.commit()
        with patch.object(main, 'check_rate_limit', return_value=True), patch.object(main, '_is_email_blocked', side_effect=lambda db, email: email == 'researcher@example.com'):
            result = self.client.post('/login', data={'email': 'Researcher', 'password': self.password})
            self.assertNotEqual(result.status_code, 303)

    def test_mobile_researcher_keeps_workspace_and_profile_role(self):
        self.client.headers['User-Agent'] = 'Mozilla/5.0 (iPhone; Mobile)'
        workspace = self.client.get('/publisher', follow_redirects=False)
        self.assertEqual(workspace.status_code, 200)
        self.assertEqual(self.client.cookies.get('participant_app'), '0')
        self.assertIn('Researcher <strong>profile</strong>', self.client.get('/profile').text)
        self.client.get('/participant')
        self.assertEqual(self.client.cookies.get('participant_app'), '1')
        self.assertIn('<title>Insighta - Profile</title>', self.client.get('/profile').text)

    def test_mobile_workspace_login_preserves_researcher_intent(self):
        self.client.cookies.clear()
        self.client.headers['User-Agent'] = 'Mozilla/5.0 (iPhone; Mobile)'
        result = self.client.get('/publisher', follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers['location'], '/login?role=researcher')
        login = self.client.get(result.headers['location'])
        self.assertIn('Continue to your researcher workspace.', login.text)
        self.assertEqual(self.client.cookies.get('participant_app'), '0')


if __name__ == '__main__':
    unittest.main()
