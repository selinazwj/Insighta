import os
import json
import time
import hmac
import hashlib
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:////private/tmp/insighta_niche_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP'] = '1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER'] = '0'
from fastapi.testclient import TestClient
from api import main
from app.models import User, Survey
from app.niche_models import NicheRequest, NicheQuote
from app.wallet_models import BillingInvoice
from app.settlements import refund_cents


class NicheFlowTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine)
        main.Base.metadata.create_all(main.engine)
        self.db = main.SessionLocal()
        self.addCleanup(self.db.close)
        owner = User(email='niche-owner@example.invalid', password='x')
        other = User(email='niche-other@example.invalid', password='x')
        self.db.add_all([owner, other]); self.db.commit()
        self.owner, self.other = owner.id, other.id
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.client.cookies.set('user_id', main.sign_session(self.owner))
        self.auth = patch.dict(os.environ, {'ADMIN_KEY':'niche-unit-admin'})
        self.auth.start(); self.addCleanup(self.auth.stop)
        self.data = dict(title='Specialist recruitment', description='Research for specialist users.',
            form_url='https://example.invalid/study', task_type='survey', category='research', estimated_time='5',
            target_responses='20', per_person_gross='0', urgency_level='within_1_week', incentive_type='cash',
            niche_recruitment='1', niche_details='US-based clinicians with specialist software experience.',
            publish_token='niche-test-submission-123456')

    def submit(self, **updates):
        response = self.client.post('/publish', data={**self.data, **updates}, headers={'Accept':'application/json'})
        self.assertEqual(response.status_code, 200, response.text)
        self.study_id = response.json()['survey_id']
        self.row = self.db.query(NicheRequest).filter_by(survey_id=self.study_id).one()
        return response

    def offer(self, **updates):
        response = self.client.post(f'/admin/niche/{self.row.id}/quotes', data={
            'admin_key':'niche-unit-admin', 'reward':'40', 'fee':'15', 'valid_days':'7', 'notes':'Specialist audience.', **updates}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.db.expire_all()
        self.quote = self.db.get(NicheQuote, self.db.get(NicheRequest, self.row.id).current_quote_id)
        return self.quote

    def checkout(self, **updates):
        return self.client.post(f'/publisher/study/{self.study_id}/quote/checkout', data={
            'quote_id':str(self.quote.id), 'refund_destination':'wallet', 'accept_quote':'1', **updates}, follow_redirects=False)

    def provider_session(self, **updates):
        return {'id':'cs_niche', 'object':'checkout.session', 'url':'https://checkout.stripe.com/niche-test', **updates}

    def start_checkout(self):
        with patch.object(main.stripe, 'api_key', 'sk_unit'), patch.object(main.stripe.checkout.Session, 'create', return_value=self.provider_session()) as create:
            response = self.checkout()
            self.assertEqual(response.status_code,303,response.text)
            response = self.checkout()
            self.assertEqual(response.status_code,303,response.text)
            self.assertEqual(create.call_count,1)
        self.db.expire_all()

    def event(self, **updates):
        return self.provider_session(payment_status='paid', amount_total=self.quote.snapshot['total_cents'],
            currency='usd', payment_intent='pi_niche', metadata={'survey_id':str(self.study_id),
                'publisher_id':str(self.owner), 'niche_quote_id':str(self.quote.id)}, **updates)

    def webhook(self, checkout):
        secret='whsec_niche_unit_only'; timestamp=str(int(time.time()))
        body=json.dumps({'id':'evt_niche', 'object':'event', 'api_version':'2025-03-31.basil',
            'type':'checkout.session.completed','data':{'object':checkout}}).encode()
        sig=hmac.new(secret.encode(),timestamp.encode()+b'.'+body,hashlib.sha256).hexdigest()
        with patch.object(main,'STRIPE_WEBHOOK_SECRET',secret):
            response=self.client.post('/webhook/stripe',content=body,headers={'stripe-signature':f't={timestamp},v1={sig}'})
        self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all()

    def test_request_is_private_idempotent_and_cannot_bypass_quote(self):
        with patch.object(main.stripe.checkout.Session,'create') as create:
            first=self.submit(); self.submit(); self.submit(niche_recruitment='0',per_person_gross='1')
            create.assert_not_called()
        self.assertEqual(self.db.query(NicheRequest).count(),1)
        study=self.db.get(Survey,self.study_id)
        self.assertEqual(study.status,'draft');self.assertIsNone(study.pricing_snapshot)
        self.assertEqual(study.payment_status,'quote_requested')
        self.assertIn('/quote',first.json()['redirect_url'])
        page=self.client.get(first.json()['redirect_url'])
        self.assertIn('Awaiting quote',page.text);self.assertIn('mailto:vfsa@bu.edu',page.text)
        self.assertIn('Listing%20%23'+str(self.study_id),page.text)
        self.assertIn('Awaiting quote',self.client.get('/publisher').text)
        self.assertEqual(self.client.post(f'/surveys/{self.study_id}/publish').status_code,400)
        self.assertEqual(self.client.post(f'/surveys/{self.study_id}/update-info',json={'title':'Changed'}).status_code,409)
        self.assertEqual(self.client.post(f'/surveys/{self.study_id}/close').status_code,409)
        self.assertEqual(self.client.post(f'/publisher/delete/{self.study_id}').status_code,409)
        self.assertEqual(self.client.post(f'/admin/listings/{self.study_id}/edit',data={'admin_key':'niche-unit-admin'}).status_code,409)

    def test_admin_authorization_and_owner_bound_payment_page(self):
        self.submit()
        self.assertEqual(self.client.get('/admin/niche').status_code,403)
        self.assertEqual(self.client.post(f'/admin/niche/{self.row.id}/quotes',data={'admin_key':'wrong'}).status_code,403)
        self.assertEqual(self.client.get('/admin/niche?admin_key=niche-unit-admin').status_code,200)
        self.client.cookies.set('user_id',main.sign_session(self.other))
        self.assertEqual(self.client.get(f'/publisher/study/{self.study_id}/quote').status_code,404)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(f'/publisher/study/{self.study_id}/quote',follow_redirects=False).status_code,303)

    def test_manual_quote_snapshot_floor_and_urgency(self):
        self.submit();q=self.offer()
        self.assertEqual(q.snapshot['total_cents'],121000)
        self.assertEqual(q.snapshot['recruitment_fee_per_person_cents'],1500)
        self.assertEqual(refund_cents(q.snapshot,19),6050)
        self.assertEqual(refund_cents(q.snapshot,19,'researcher_cancelled'),4400)
        bad=self.client.post(f'/admin/niche/{self.row.id}/quotes',data={'admin_key':'niche-unit-admin','reward':'40','fee':'4'})
        self.assertEqual(bad.status_code,400)
        self.db.expire_all();self.assertEqual(self.db.get(NicheRequest,self.row.id).current_quote_id,q.id)

    def test_acceptance_expiry_and_stale_offers(self):
        self.submit();old=self.offer();new=self.offer(fee='20')
        self.assertEqual(self.checkout(quote_id=str(old.id)).status_code,409)
        self.assertEqual(self.checkout(accept_quote='0').status_code,400)
        new.expires_at=datetime.utcnow()-timedelta(seconds=1);self.db.commit()
        self.assertEqual(self.checkout().status_code,409)
        self.assertIn('Quote expired',self.client.get(f'/publisher/study/{self.study_id}/quote').text)

    def test_signed_payment_publishes_once_invoice_and_no_reopen(self):
        self.submit();self.offer();self.start_checkout()
        self.assertEqual(self.db.get(Survey,self.study_id).status,'draft')
        event=self.event();self.webhook(event);self.webhook(event)
        study=self.db.get(Survey,self.study_id)
        self.assertEqual(study.status,'published');self.assertEqual(study.pricing_snapshot,self.quote.snapshot)
        self.assertEqual(study.stripe_funding_payment_intent_id,'pi_niche')
        self.assertEqual(self.db.query(BillingInvoice).count(),1)
        study.status='closed';self.db.commit();self.webhook(event)
        self.assertEqual(self.db.get(Survey,self.study_id).status,'closed')
        self.assertEqual(self.client.post(f'/surveys/{self.study_id}/publish').status_code,409)

    def test_wrong_amount_currency_owner_or_session_never_publishes(self):
        self.submit();self.offer();self.start_checkout()
        for key,value in [('amount_total',1),('currency','eur'),('id','cs_wrong'),('payment_status','unpaid')]:
            event=self.event();event[key]=value;self.webhook(event)
        for meta in [{'survey_id':str(self.study_id)}, {'survey_id':str(self.study_id),'publisher_id':str(self.other),'niche_quote_id':str(self.quote.id)}]:
            event=self.event();event['metadata']=meta;self.webhook(event)
        self.assertEqual(self.db.get(Survey,self.study_id).status,'draft')
        self.assertEqual(self.db.query(BillingInvoice).count(),0)

    def test_requote_expires_old_checkout_and_old_event_is_ignored(self):
        self.submit();old=self.offer();self.start_checkout();old_event=self.event()
        with patch.object(main.stripe.checkout.Session,'retrieve',return_value={'status':'open','payment_status':'unpaid'}), patch.object(main.stripe.checkout.Session,'expire',return_value={'status':'expired'}) as expire:
            self.offer(fee='18');expire.assert_called_once_with('cs_niche')
        self.webhook(old_event)
        self.assertEqual(self.db.get(Survey,self.study_id).status,'draft')
        self.assertEqual(self.db.get(NicheQuote,old.id).status,'superseded')

    def test_unknown_checkout_result_has_stable_retry_and_blocks_requote(self):
        self.submit();self.offer()
        with patch.object(main.stripe,'api_key','sk_unit'), patch.object(main.stripe.checkout.Session,'create',side_effect=[main.stripe.error.APIConnectionError('provider private message'),self.provider_session()]) as create:
            first=self.checkout();self.assertEqual(first.status_code,502)
            self.assertNotIn('provider private message',first.text)
            blocked=self.client.post(f'/admin/niche/{self.row.id}/quotes',data={'admin_key':'niche-unit-admin','reward':'40','fee':'18'})
            self.assertEqual(blocked.status_code,409)
            self.assertEqual(self.checkout().status_code,303)
            self.assertEqual(create.call_args_list[0].kwargs,create.call_args_list[1].kwargs)

    def test_completed_checkout_blocks_repricing_until_confirmation(self):
        self.submit();self.offer();self.start_checkout()
        with patch.object(main.stripe.checkout.Session,'retrieve',return_value={'status':'complete','payment_status':'paid'}), patch.object(main.stripe.checkout.Session,'expire') as expire:
            response=self.client.post(f'/admin/niche/{self.row.id}/quotes',data={'admin_key':'niche-unit-admin','reward':'40','fee':'18'})
            self.assertEqual(response.status_code,409);expire.assert_not_called()

    def test_paid_webhook_recovers_a_lost_checkout_creation_response(self):
        self.submit();self.offer()
        with patch.object(main.stripe,'api_key','sk_unit'), patch.object(main.stripe.checkout.Session,'create',side_effect=main.stripe.error.APIConnectionError('lost response')):
            self.assertEqual(self.checkout().status_code,502)
        event=self.event()
        with patch.object(main.stripe.checkout.Session,'retrieve',return_value=event):
            self.webhook(event);self.webhook(event)
        self.assertEqual(self.db.get(Survey,self.study_id).status,'published')
        self.assertEqual(self.db.query(BillingInvoice).count(),1)

    def test_remote_and_in_person_requests_use_same_flow(self):
        for index,mode in enumerate(['video','in_person']):
            data={**self.data,'publish_token':f'niche-interview-token-{index}-123456','interview_format':mode,'scheduling_link':'https://example.invalid/booking'}
            response=self.client.post('/publish_interview',data=data,follow_redirects=False)
            self.assertEqual(response.status_code,303,response.text)
            self.assertIn('/quote',response.headers['location'])


if __name__ == '__main__':
    unittest.main()
