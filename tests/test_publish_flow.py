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
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            first = self.submit(incentive_type='raffle')
            with main.SessionLocal() as db:
                survey = db.get(Survey, first.json()['survey_id'])
                survey.status = 'published'
                survey.payment_status = 'paid'
                db.commit()
            second = self.submit(title='Changed by a retry')
        self.assertEqual(first.json()['survey_id'], second.json()['survey_id'])
        with main.SessionLocal() as db:
            survey = db.query(Survey).one()
            self.assertEqual(survey.title, self.data['title'])
            self.assertEqual(survey.status, 'published')
            self.assertEqual(survey.payment_status, 'paid')

    def test_token_is_scoped_to_owner_and_cannot_update_another_users_draft(self):
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
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

    def admin_submit(self, **overrides):
        with patch.dict(os.environ, {'ADMIN_KEY': 'publish-test-admin-key'}):
            return self.client.post('/admin/publish', data={
                **self.data, 'admin_key': 'publish-test-admin-key',
                'per_person_gross': '', 'admin_display_reward_amount': '25',
                **overrides,
            }, headers={'Accept': 'application/json'}, follow_redirects=False)

    def test_admin_demo_publish_without_verification_fields(self):
        with patch.object(main.stripe.checkout.Session, 'create') as checkout:
            result = self.admin_submit()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['redirect_url'].startswith('/admin/publish?'))
        checkout.assert_not_called()
        with main.SessionLocal() as db:
            survey = db.get(Survey, result.json()['survey_id'])
            self.assertEqual(survey.status, 'published')
            self.assertEqual(survey.payment_status, 'admin_demo')
            self.assertEqual(survey.admin_display_reward_amount, 25)
            self.assertEqual(survey.required_verification_tier, main.TIER_BASIC)
            self.assertIsNone(survey.required_email_domains)

    def test_admin_publish_preserves_email_and_identity_requirements(self):
        result = self.admin_submit(required_email_domains='@BU.edu, gmail.com',
                                   required_verification_tier=main.TIER_IDENTITY)
        self.assertEqual(result.status_code, 200, result.text)
        with main.SessionLocal() as db:
            survey = db.get(Survey, result.json()['survey_id'])
            self.assertEqual(survey.required_email_domains, 'bu.edu,gmail.com')
            self.assertEqual(survey.required_verification_tier, main.TIER_IDENTITY)

    def test_admin_demo_retry_reuses_listing_and_returns_to_admin(self):
        first = self.admin_submit()
        retried = self.admin_submit(title='Changed by a retry')
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(first.json(), retried.json())
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 1)
            self.assertEqual(db.query(Survey).one().title, self.data['title'])

    def test_admin_stripe_publish_creates_checkout_for_admin_listing(self):
        checkout = SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=checkout,
        ) as create:
            result = self.admin_submit(admin_payment_mode='stripe', per_person_gross='5')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['redirect_url'], checkout.url)
        self.assertEqual(create.call_args.kwargs['metadata']['source'], 'admin_publish_stripe')
        with main.SessionLocal() as db:
            survey = db.get(Survey, result.json()['survey_id'])
            self.assertEqual(survey.status, 'draft')
            self.assertEqual(survey.payment_status, 'unpaid')
            self.assertIsNone(survey.admin_display_reward_amount)

    def test_builtin_publish_keeps_existing_questions_and_html_redirect(self):
        created = self.client.post('/surveys/create-builtin', follow_redirects=False)
        survey_id = int(created.headers['location'].split('/')[2])
        question = self.client.post(f'/surveys/{survey_id}/questions', json={
            'question_text': 'Favorite color?', 'question_type': 'text', 'is_required': True,
        })
        self.assertEqual(question.status_code, 200)
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            result = self.client.post('/publish', data={
                **self.data, 'existing_survey_id': survey_id, 'form_url': '__builtin__', 'incentive_type': 'raffle',
            }, follow_redirects=False)
        self.assertEqual(result.status_code, 303)
        self.assertEqual(result.headers['location'], 'https://checkout.stripe.com/mock')
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 1)
            self.assertEqual(db.get(Survey, survey_id).status, 'draft')
            self.assertEqual(db.get(Survey, survey_id).pricing_snapshot['study_type'], 'survey')
        loaded = self.client.get(f'/surveys/{survey_id}/questions')
        self.assertEqual(loaded.json()[0]['question_text'], 'Favorite color?')

    def test_publish_page_has_token_and_database_has_unique_index(self):
        page = self.client.get('/publish')
        self.assertEqual(page.status_code, 200)
        self.assertRegex(page.text, r'name="publish_token" value="[A-Za-z0-9_-]{32}"')
        indexes = inspect(main.engine).get_indexes('surveys')
        self.assertTrue(any(i['name'] == 'ix_surveys_publish_token' and i['unique'] for i in indexes))

    def test_price_api_and_survey_checkout_use_identical_quote(self):
        payload = dict(study_type='survey', incentive_type='cash', per_person='40',
                       target_responses=20, urgency_level='within_1_week')
        quote = self.client.post('/api/calculate-price', json=payload).json()
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ) as create:
            result = self.submit(per_person_gross='40', urgency_level='within_1_week', total_budget='0.01')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], quote['total_cents'])
        with main.SessionLocal() as db:
            survey = db.get(Survey, result.json()['survey_id'])
            self.assertEqual(survey.pricing_snapshot, quote)
            self.assertEqual(survey.reward_amount, 40)
            self.assertEqual(survey.total_budget, 1056)

    def test_interview_and_in_person_use_checkout_and_full_study_reward(self):
        for index, (format_, kind, total) in enumerate([('online', 'interview', 110000), ('in_person', 'in_person', 121000)]):
            with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
                main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id=f'cs_{index}', url='https://checkout.stripe.com/mock')
            ) as create:
                result = self.client.post('/publish_interview', data={
                    **self.data, 'publish_token': f'interview-test-token-{index}',
                    'interview_format': format_, 'per_person_gross': '40',
                    'urgency_level': 'within_1_week', 'session_count': '3',
                    'required_occupation': ['Student', 'Engineer'], 'interview_location': 'Room 2',
                }, headers={'Accept': 'application/json'}, follow_redirects=False)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], total)
            with main.SessionLocal() as db:
                survey = db.get(Survey, result.json()['survey_id'])
                self.assertEqual(survey.pricing_snapshot['study_type'], kind)
                self.assertEqual(survey.reward_amount, 40)
                self.assertEqual(survey.session_count, 3)
                self.assertEqual(survey.status, 'draft')
                self.assertEqual(survey.required_occupation, 'Student,Engineer')

    def test_raffle_and_volunteer_require_paid_checkout(self):
        for incentive in ['raffle', 'volunteer']:
            with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
                main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
            ) as create:
                result = self.submit(incentive_type=incentive, publish_token=f'{incentive}-test-token-123456')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(create.call_args.kwargs['line_items'][0]['price_data']['unit_amount'], 10000)
            with main.SessionLocal() as db:
                survey = db.get(Survey, result.json()['survey_id'])
                self.assertEqual(survey.reward_amount, 0)
                self.assertEqual(survey.payment_status, 'unpaid')

    def test_public_publish_cannot_enable_admin_demo_with_query_parameters(self):
        with patch.object(main.stripe.checkout.Session, 'create') as checkout:
            result = self.client.post('/publish?admin_publish=true', data=self.data,
                                      headers={'Accept': 'application/json'}, follow_redirects=False)
        self.assertEqual(result.status_code, 403)
        checkout.assert_not_called()
        with main.SessionLocal() as db:
            self.assertEqual(db.query(Survey).count(), 0)

    def test_missing_stripe_does_not_publish_or_mark_paid(self):
        with patch.object(main.stripe, 'api_key', None):
            result = self.submit()
        self.assertEqual(result.status_code, 503)
        with main.SessionLocal() as db:
            survey = db.query(Survey).one()
            self.assertEqual(survey.status, 'draft')
            self.assertEqual(survey.payment_status, 'unpaid')
            survey_id = survey.id
        self.client.post(f'/surveys/{survey_id}/close', follow_redirects=False)
        self.assertEqual(self.client.post(f'/surveys/{survey_id}/reopen', follow_redirects=False).status_code, 400)

    def test_success_redirect_cannot_publish_and_webhook_checks_funding(self):
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            result = self.submit()
        survey_id = result.json()['survey_id']
        success = self.client.get(f'/payment/success?survey_id={survey_id}')
        self.assertIn('Confirming payment', success.text)
        with main.SessionLocal() as db:
            quote = db.get(Survey, survey_id).pricing_snapshot
            self.assertEqual(db.get(Survey, survey_id).status, 'draft')
        obj = dict(id='cs_mock', metadata={'survey_id': str(survey_id)}, payment_status='paid',
                   amount_total=quote['total_cents'], currency='usd')
        for changes in [{'payment_status': 'unpaid'}, {'id': 'cs_other'}, {'amount_total': 1}, {'currency': 'eur'}, {}]:
            event = {'type': 'checkout.session.completed', 'data': {'object': {**obj, **changes}}}
            with patch.object(main.stripe.Webhook, 'construct_event', return_value=event):
                self.assertEqual(self.client.post('/webhook/stripe', content=b'mocked').status_code, 200)
            with main.SessionLocal() as db:
                survey = db.get(Survey, survey_id)
                self.assertEqual(survey.status, 'draft' if changes else 'published')
                self.assertEqual(survey.pricing_snapshot, quote)
        self.client.post(f'/surveys/{survey_id}/close', follow_redirects=False)
        with patch.object(main.stripe.Webhook, 'construct_event', return_value=event):
            self.client.post('/webhook/stripe', content=b'mocked')
        with main.SessionLocal() as db:
            self.assertEqual(db.get(Survey, survey_id).status, 'closed')
        self.client.cookies.set('user_id', str(self.other_id))
        self.assertEqual(self.client.get(f'/payment/success?survey_id={survey_id}').status_code, 404)

    def test_quote_is_protected_from_builder_edits(self):
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            result = self.submit()
        path = f"/surveys/{result.json()['survey_id']}/update-info"
        self.assertEqual(self.client.post(path, json={'reward_amount': 1}).status_code, 400)
        self.assertEqual(self.client.post(path, json={'title': 'Updated title'}).status_code, 200)

    def test_quoted_metadata_edit_neither_reprices_nor_charges_remaining_places(self):
        with patch.object(main.stripe, 'api_key', 'sk_mock'), patch.object(
            main.stripe.checkout.Session, 'create', return_value=SimpleNamespace(id='cs_mock', url='https://checkout.stripe.com/mock')
        ):
            result = self.submit()
        survey_id = result.json()['survey_id']
        with main.SessionLocal() as db:
            survey = db.get(Survey, survey_id)
            snapshot = survey.pricing_snapshot
            survey.status = 'published'
            survey.payment_status = 'paid'
            db.commit()
        with patch.object(main.stripe.checkout.Session, 'create') as checkout:
            result = self.client.post(f'/publisher/edit/{survey_id}', data={
                **self.data, 'title': 'New description title', 'reward_amount': '5',
                'additional_needed': '20', 'urgency_level': 'flexible',
            }, follow_redirects=False)
        self.assertEqual(result.status_code, 303, result.text)
        checkout.assert_not_called()
        with main.SessionLocal() as db:
            self.assertEqual(db.get(Survey, survey_id).pricing_snapshot, snapshot)
            self.assertEqual(db.get(Survey, survey_id).title, 'New description title')
        self.client.post(f'/surveys/{survey_id}/close', follow_redirects=False)
        retried = self.submit(per_person_gross='100')
        self.assertEqual(retried.status_code, 200, retried.text)
        with main.SessionLocal() as db:
            self.assertEqual(db.get(Survey, survey_id).status, 'closed')
            self.assertEqual(db.get(Survey, survey_id).pricing_snapshot, snapshot)
        page = self.client.get(f'/publisher/edit/{survey_id}')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Create a new listing for a new order.', page.text)

    def test_legacy_published_study_retains_its_price_and_no_snapshot(self):
        with main.SessionLocal() as db:
            survey = Survey(publisher_id=self.user_id, title='Legacy', description='Old', form_url='__builtin__',
                            task_type='survey', category='research', estimated_time=5, reward_amount=8.5,
                            per_person_gross=10, total_budget=200, commission_rate=0.15,
                            target_responses=20, status='published', payment_status='paid')
            db.add(survey)
            db.commit()
            survey_id = survey.id
        result = self.submit(existing_survey_id=survey_id, per_person_gross='100')
        self.assertEqual(result.status_code, 200)
        with main.SessionLocal() as db:
            survey = db.get(Survey, survey_id)
            self.assertEqual(survey.total_budget, 200)
            self.assertEqual(survey.reward_amount, 8.5)
            self.assertIsNone(survey.pricing_snapshot)

    def test_pricing_public_page_and_invalid_quote(self):
        self.client.cookies.clear()
        response = self.client.get('/pricing')
        self.assertEqual(response.status_code, 200)
        self.assertIn('$15.00', response.text)
        self.assertNotIn('noindex', response.headers.get('x-robots-tag', ''))
        self.assertIn('/pricing', self.client.get('/sitemap.xml').text)
        self.client.cookies.set('user_id', str(self.user_id))
        for payload in [[], {'per_person': 'NaN'}, {'per_person': '5', 'target_responses': -1}]:
            self.assertEqual(self.client.post('/api/calculate-price', json=payload).status_code, 400)

    def test_staging_public_pages_disable_search_indexing(self):
        with patch.dict(os.environ, {'INSIGHTA_ENV': 'staging'}):
            page = self.client.get('/pricing')
            self.assertTrue(page.headers.get('x-robots-tag', '').startswith('noindex'))
            self.assertEqual(self.client.get('/robots.txt').text, 'User-agent: *\nDisallow: /\n')

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
