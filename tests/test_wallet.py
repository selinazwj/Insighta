import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
os.environ['DATABASE_URL']='sqlite:////private/tmp/insighta_wallet_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP']='1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER']='0'
from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import main
from app.models import User, Survey, StudySettlement
from app.wallet_models import *
from app import wallet
from app.pricing import calculate_quote
from app.settlements import scan_settlements, process_refund
from app.invoices import render_invoice

class WalletTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine)
        main.Base.metadata.create_all(main.engine)
        self.db=main.SessionLocal();self.addCleanup(self.db.close)
        self.user=User(email='wallet@example.invalid',password='x')
        self.db.add(self.user);self.db.commit()
        self.uid=self.user.id
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.client.cookies.set('user_id',str(self.uid))
        key=patch.object(main.stripe,'api_key','sk_test_mock');key.start();self.addCleanup(key.stop)

    def fund(self,amount=10000,tag='one'):
        row=WalletTopUp(user_id=self.uid,token=tag,amount_cents=amount,stripe_session_id='cs_'+tag)
        self.db.add(row);self.db.commit()
        session=dict(id='cs_'+tag,payment_status='paid',currency='usd',amount_total=amount,payment_intent='pi_'+tag,
                     metadata={'wallet_topup_id':str(row.id),'user_id':str(self.uid)})
        wallet.fulfill_topup(self.db,session);self.db.commit()
        return session

    def study(self,count=2):
        q=calculate_quote('survey','cash','10',count,'within_1_week')
        s=Survey(publisher_id=self.uid,title='Wallet study',description='test',form_url='https://example.invalid',
                 category='research',task_type='survey',estimated_time=10,reward_amount=10,target_responses=count,
                 status='draft',payment_status='unpaid',pricing_snapshot=q,refund_destination='wallet')
        self.db.add(s);self.db.commit();return s

    def coupon(self,amount=1000,expired=False,limit=1):
        c=WalletCoupon(code='TESTCODE',amount_cents=amount,expires_at=datetime.utcnow()+timedelta(days=-1 if expired else 1),max_redemptions=limit)
        self.db.add(c);self.db.commit();return c

    def settle(self,s,destination='wallet'):
        s.status='closed';s.closed_at=datetime.utcnow();s.close_reason='completed';s.refund_destination=destination
        self.db.commit();scan_settlements(self.db,lambda *_:True)
        return self.db.query(StudySettlement).filter_by(survey_id=s.id).one()

    def test_seeded_staging_researcher_can_login_and_open_wallet(self):
        from scripts.start_staging import seed_researcher_account
        env={'INSIGHTA_ENV':'staging','DATABASE_URL':'postgresql://test:test@localhost/insighta_pricing_staging','RENDER_EXTERNAL_URL':'https://test.onrender.com','STAGING_RESEARCHER_EMAIL':'seed-test@insighta.invalid','STAGING_RESEARCHER_PASSWORD_HASH':main.pwd_context.hash('Local-seed-test-only!')}
        seed_researcher_account(self.db,env)
        self.client.cookies.clear()
        login=self.client.post('/login',data={'email':env['STAGING_RESEARCHER_EMAIL'],'password':'Local-seed-test-only!','role':'researcher','next':'/publisher/wallet'},follow_redirects=False)
        self.assertEqual(login.status_code,303)
        self.assertEqual(login.headers['location'],'/publisher/wallet')
        page=self.client.get('/publisher/wallet')
        self.assertEqual(page.status_code,200)
        self.assertIn('Researcher Preview',page.text)
        self.assertEqual(self.client.get('/api/wallet').json()['cash_cents'],0)

    def test_staging_login_explains_test_account_and_hides_unconfigured_oauth(self):
        with patch.dict(os.environ,{'INSIGHTA_ENV':'staging'}):
            page=self.client.get('/login?role=researcher&next=/publisher/wallet')
            self.assertEqual(page.status_code,200)
            self.assertIn('separate researcher test account',page.text)
            self.assertNotIn('href="/auth/google"',page.text)
            self.assertNotIn('href="/auth/linkedin"',page.text)
        with patch.dict(os.environ,{'INSIGHTA_ENV':'production'}):
            self.assertIn('href="/auth/google"',self.client.get('/login').text)

    def test_wallet_page_redirects_missing_invalid_and_stale_sessions(self):
        self.client.cookies.clear()
        for cookie in [None, 'not-an-id', '999999999', '9999999999999999999999999999999999']:
            self.client.cookies.clear()
            if cookie: self.client.cookies.set('user_id',cookie)
            page=self.client.get('/publisher/wallet',follow_redirects=False)
            self.assertEqual(page.status_code,303)
            self.assertEqual(page.headers['location'],'/login?role=researcher&next=%2Fpublisher%2Fwallet')
            self.assertEqual(self.db.query(WalletLedger).count(),0)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/wallet').status_code,401)
        self.assertEqual(self.client.post('/api/wallet/topups',json={}).status_code,401)
        login=self.client.get('/publisher/wallet')
        self.assertEqual(login.status_code,200)
        self.assertIn('/login',str(login.url))

    def test_dashboard_wallet_entry_and_login_return(self):
        dashboard=self.client.get('/publisher')
        self.assertEqual(dashboard.status_code,200)
        self.assertIn('href="/publisher/wallet"',dashboard.text)
        self.assertIn('/static/researcher_workspace.css',dashboard.text)
        page=self.client.get('/publisher/wallet')
        self.assertIn('aria-current="page"',page.text)
        self.assertIn('/static/researcher_workspace.css',page.text)
        self.assertNotIn('class="public-nav"',page.text)
        self.user.password=main.pwd_context.hash('Wallet-login-test!')
        for key,value in dict(first_name='Wallet',last_name='Tester',username='Wallet Tester',birth_year='1995',birth_month='1',education_level='Graduate',field='Psychology',status='Graduate student',state='Massachusetts',language='English',participation_format='Any format').items():
            setattr(self.user,key,value)
        self.db.commit();self.client.cookies.clear()
        logged_in=self.client.post('/login',data={'email':self.user.email,'password':'Wallet-login-test!','role':'researcher','next':'/publisher/wallet'},follow_redirects=False)
        self.assertEqual(logged_in.status_code,303)
        self.assertEqual(logged_in.headers['location'],'/publisher/wallet')
        self.assertEqual(self.client.get('/publisher/wallet').status_code,200)

    def test_signed_topup_is_exactly_once_and_receipt_is_immutable(self):
        session=self.fund();wallet.fulfill_topup(self.db,session);self.db.commit()
        self.assertEqual(wallet.cash_balance(self.db,self.uid),10000)
        self.assertEqual(self.db.query(WalletFundingLot).count(),1)
        invoice=self.db.query(BillingInvoice).one()
        self.assertTrue(render_invoice(invoice).startswith(b'%PDF'))
        invoice.number='changed'
        with self.assertRaises(ValueError):self.db.commit()
        self.db.rollback()

    def test_topup_mismatch_never_credits(self):
        self.db.add(WalletTopUp(user_id=self.uid,token='bad',amount_cents=100,stripe_session_id='cs_bad'));self.db.commit()
        row=self.db.query(WalletTopUp).one()
        base=dict(id='cs_bad',payment_status='paid',currency='usd',amount_total=100,payment_intent='pi_bad',metadata={'wallet_topup_id':str(row.id),'user_id':str(self.uid)})
        for field,value in [('amount_total',101),('currency','eur'),('id','cs_other'),('payment_status','unpaid'),('payment_intent','invalid')]:
            with self.assertRaises(HTTPException):wallet.fulfill_topup(self.db,{**base,field:value})
            self.db.rollback()
        self.assertEqual(wallet.cash_balance(self.db,self.uid),0)

    def test_coupon_expiry_duplicate_and_limit(self):
        c=self.coupon();wallet.redeem_coupon(self.db,self.uid,c.code);self.db.commit()
        with self.assertRaises(HTTPException):wallet.redeem_coupon(self.db,self.uid,c.code)
        self.db.rollback();c=self.db.get(WalletCoupon,c.id);c.expires_at=datetime.utcnow()-timedelta(seconds=1)
        self.db.query(WalletPromoGrant).one().expires_at=c.expires_at
        self.db.commit()
        self.assertEqual(wallet.summary(self.db,self.uid)['promo_cents'],0)
        with self.assertRaises(HTTPException):wallet.redeem_coupon(self.db,self.uid,c.code)

    def test_promo_only_offsets_base_fee_and_duplicate_payment_is_safe(self):
        self.fund();c=self.coupon(100000);wallet.redeem_coupon(self.db,self.uid,c.code);self.db.commit()
        s=self.study();p=wallet.pay_study(self.db,s.id,self.uid);self.db.commit()
        self.assertEqual(p.promo_cents,1000)
        self.assertEqual(p.cash_cents,s.pricing_snapshot['total_cents']-1000)
        balance=wallet.cash_balance(self.db,self.uid)
        self.assertEqual(wallet.pay_study(self.db,s.id,self.uid).id,p.id)
        self.db.commit();self.assertEqual(wallet.cash_balance(self.db,self.uid),balance)
        self.assertEqual(self.db.query(WalletPayment).count(),1)

    def test_insufficient_cash_never_spends_coupon(self):
        c=self.coupon();g=wallet.redeem_coupon(self.db,self.uid,c.code);self.db.commit();s=self.study()
        with self.assertRaises(HTTPException):wallet.pay_study(self.db,s.id,self.uid)
        self.db.rollback();self.assertEqual(self.db.get(WalletPromoGrant,g.id).remaining_cents,1000)
        self.assertEqual(self.db.query(WalletPayment).count(),0)

    def test_wallet_refund_restores_cash_once_but_burns_promo(self):
        self.fund();c=self.coupon();wallet.redeem_coupon(self.db,self.uid,c.code);self.db.commit();s=self.study()
        p=wallet.pay_study(self.db,s.id,self.uid);self.db.commit();cash=p.cash_cents
        row=self.settle(s)
        self.assertEqual(row.refund_amount_cents,cash)
        with patch.object(main.stripe.Refund,'create') as create:
            process_refund(self.db,row.id);process_refund(self.db,row.id)
        create.assert_not_called()
        self.assertEqual(wallet.cash_balance(self.db,self.uid),10000)
        self.assertEqual(wallet.summary(self.db,self.uid)['promo_cents'],0)
        self.assertEqual(row.status,'wallet_refunded')

    def test_card_order_refunds_to_wallet_without_stripe_refund(self):
        s=self.study();s.status='published';s.payment_status='paid';s.funding_source='stripe';s.stripe_funding_payment_intent_id='pi_direct';self.db.commit()
        row=self.settle(s)
        with patch.object(main.stripe.Refund,'create') as create:process_refund(self.db,row.id)
        create.assert_not_called();self.assertEqual(wallet.cash_balance(self.db,self.uid),s.pricing_snapshot['total_cents'])
        self.assertEqual(self.db.query(WalletFundingLot).one().payment_intent_id,'pi_direct')

    def test_split_original_card_refund_recovers_unknown_outcome(self):
        self.fund(1000,'one');self.fund(5000,'two');s=self.study();wallet.pay_study(self.db,s.id,self.uid);self.db.commit()
        row=self.settle(s,'card')
        empty=SimpleNamespace(auto_paging_iter=lambda:iter([]))
        with patch.object(main.stripe.Refund,'list',return_value=empty),patch.object(main.stripe.Refund,'create',side_effect=TimeoutError('unknown')):
            process_refund(self.db,row.id)
        parts=self.db.query(WalletRefundPart).order_by(WalletRefundPart.id).all()
        self.assertEqual(len(parts),2);ids=[p.id for p in parts]
        remote=[dict(id='re_'+str(p.id),amount=p.amount_cents,currency='usd',payment_intent=p.payment_intent_id,status='succeeded',metadata={'insighta_wallet_part_id':str(p.id)}) for p in parts]
        with patch.object(main.stripe.Refund,'list',return_value=SimpleNamespace(auto_paging_iter=lambda:iter(remote))),patch.object(main.stripe.Refund,'create') as create:
            process_refund(self.db,row.id,now=datetime.utcnow()+timedelta(days=2))
        create.assert_not_called();self.assertEqual(row.status,'refunded')
        self.assertEqual([p.id for p in self.db.query(WalletRefundPart).order_by(WalletRefundPart.id)],ids)
        self.assertEqual(sum(p.amount_cents for p in parts),row.refund_amount_cents)
        self.assertEqual(wallet.cash_balance(self.db,self.uid),6000-s.pricing_snapshot['total_cents'])

    def test_concurrent_studies_cannot_overspend(self):
        self.fund(2000);a=self.study(1);b=self.study(1);ids=[a.id,b.id]
        def pay(sid):
            with main.SessionLocal() as db:
                try:wallet.pay_study(db,sid,self.uid);db.commit();return True
                except HTTPException:db.rollback();return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(pay,ids))
        self.assertEqual(sum(results),1);self.db.expire_all();self.assertGreaterEqual(wallet.cash_balance(self.db,self.uid),0)

    def test_invoice_ownership_and_billing_snapshot(self):
        self.client.post('/api/wallet/billing',json={'name':'张三 Research','address':'北京 100000'})
        self.fund();invoice=self.db.query(BillingInvoice).one()
        self.assertEqual(invoice.snapshot['customer'],'张三 Research')
        self.client.post('/api/wallet/billing',json={'name':'Changed'})
        self.db.refresh(invoice);self.assertEqual(invoice.snapshot['customer'],'张三 Research')
        self.assertEqual(self.client.get(f'/api/wallet/invoices/{invoice.id}.pdf').status_code,200)
        other=User(email='other@example.invalid',password='x');self.db.add(other);self.db.commit()
        self.client.cookies.set('user_id',str(other.id))
        self.assertEqual(self.client.get(f'/api/wallet/invoices/{invoice.id}.pdf').status_code,404)

    def test_json_validation_and_unsigned_return_never_credit(self):
        for url in ['/api/wallet/topups','/api/wallet/redeem','/api/wallet/billing','/admin/api/wallet/coupons']:
            self.assertEqual(self.client.post(url,content='{broken',headers={'Content-Type':'application/json'}).status_code,400)
        self.assertEqual(self.client.get('/publisher/wallet?topup=processing').status_code,200)
        self.assertEqual(wallet.cash_balance(self.db,self.uid),0)

    def test_topup_checkout_retry_reuses_order_without_credit(self):
        checkout=SimpleNamespace(id='cs_new',url='https://checkout.stripe.com/test')
        body={'amount':'20.00','token':'test_token_123456789'}
        with patch.object(main.stripe.checkout.Session,'create',return_value=checkout) as create:
            a=self.client.post('/api/wallet/topups',json=body)
            b=self.client.post('/api/wallet/topups',json=body)
        self.assertEqual(a.status_code,200);self.assertEqual(a.json(),b.json());self.assertEqual(create.call_count,1)
        self.assertEqual(wallet.cash_balance(self.db,self.uid),0)
        self.assertEqual(self.client.post('/api/wallet/topups',json={**body,'amount':'21'}).status_code,409)

    def test_refund_destination_is_owner_only_and_freezes(self):
        self.fund();s=self.study();wallet.pay_study(self.db,s.id,self.uid);self.db.commit()
        self.assertEqual(self.client.post(f'/api/surveys/{s.id}/refund-destination',json={'destination':'card'}).status_code,200)
        row=self.settle(s,'card')
        self.assertEqual(self.client.post(f'/api/surveys/{s.id}/refund-destination',json={'destination':'wallet'}).status_code,409)

    def test_concurrent_coupon_limit(self):
        c=self.coupon();other=User(email='coupon@example.invalid',password='x');self.db.add(other);self.db.commit()
        def redeem(uid):
            with main.SessionLocal() as db:
                try:wallet.redeem_coupon(db,uid,'TESTCODE');db.commit();return True
                except HTTPException:db.rollback();return False
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(redeem,[self.uid,other.id]))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.db.query(WalletPromoGrant).count(),1)

    def test_wallet_publish_endpoint_requires_no_stripe_key(self):
        self.fund()
        data=dict(title='Endpoint study',description='Wallet endpoint test',form_url='https://example.invalid/form',
                  category='research',estimated_time='5',target_responses='2',per_person_gross='10',
                  incentive_type='cash',publish_token='wallet_publish_token_123456789',payment_method='wallet',use_promo='1',refund_destination='wallet')
        with patch.object(main.stripe,'api_key',None),patch.object(main.stripe.checkout.Session,'create') as create:
            first=self.client.post('/publish',data=data,headers={'Accept':'application/json'})
            second=self.client.post('/publish',data=data,headers={'Accept':'application/json'})
        self.assertEqual(first.status_code,200,first.text);self.assertEqual(second.status_code,200,second.text)
        create.assert_not_called();self.assertEqual(self.db.query(WalletPayment).count(),1)
        study=self.db.query(Survey).one();self.assertEqual(study.payment_status,'paid');self.assertEqual(study.funding_source,'wallet')

    def test_topup_signed_webhook_routes_and_duplicate(self):
        row=WalletTopUp(user_id=self.uid,token='webhook',amount_cents=100,stripe_session_id='cs_webhook');self.db.add(row);self.db.commit()
        event={'type':'checkout.session.completed','data':{'object':dict(id='cs_webhook',payment_status='paid',currency='usd',amount_total=100,payment_intent='pi_webhook',metadata={'wallet_topup_id':str(row.id),'user_id':str(self.uid)})}}
        with patch.dict(os.environ,{'STRIPE_WEBHOOK_SECRET':'whsec_mock'}),patch.object(main.stripe.Webhook,'construct_event',return_value=event):
            for _ in range(2):self.assertEqual(self.client.post('/webhook/stripe',content=b'mock').status_code,200)
        self.assertEqual(wallet.cash_balance(self.db,self.uid),100)

    def test_partial_closeout_and_cancellation_never_refund_promo(self):
        from app.settlements import refund_cents
        self.fund();c=self.coupon();wallet.redeem_coupon(self.db,self.uid,c.code);self.db.commit()
        s=self.study();p=wallet.pay_study(self.db,s.id,self.uid);self.db.commit();q=s.pricing_snapshot
        gross=refund_cents(q,1)
        self.assertEqual(wallet.settlement_cash_refund(self.db,s,gross,1,'completed'),gross-500)
        canceled=refund_cents(q,1,'researcher_cancelled')
        self.assertEqual(wallet.settlement_cash_refund(self.db,s,canceled,1,'researcher_cancelled'),canceled)

    def test_admin_coupon_auth_and_whole_limit(self):
        body=dict(admin_key='wallet-admin-test',code='ADMINTEST',amount='20',expires_at='2099-01-01T00:00:00Z',max_redemptions='2')
        with patch.dict(os.environ,{'ADMIN_KEY':'wallet-admin-test'}):
            self.assertEqual(self.client.post('/admin/api/wallet/coupons',json={**body,'admin_key':'wrong'}).status_code,403)
            for invalid in [1.5,True,'1.0']:
                self.assertEqual(self.client.post('/admin/api/wallet/coupons',json={**body,'max_redemptions':invalid}).status_code,400)
            self.assertEqual(self.client.post('/admin/api/wallet/coupons',json=body).status_code,200)
            self.assertEqual(self.client.post('/admin/api/wallet/coupons',json=body).status_code,409)
            self.assertEqual(self.client.get('/admin/wallet?admin_key=wallet-admin-test').status_code,200)

    def test_dispute_blocks_spend(self):
        self.fund();wallet.freeze_wallet_for_payment_event(self.db,{'type':'charge.dispute.created','data':{'object':{'payment_intent':'pi_one'}}});self.db.commit()
        s=self.study()
        with self.assertRaises(HTTPException):wallet.pay_study(self.db,s.id,self.uid)

    def test_stale_quote_and_paid_card_cannot_double_charge(self):
        self.fund();s=self.study()
        with self.assertRaises(HTTPException):wallet.pay_study(self.db,s.id,self.uid,expected_snapshot={})
        self.db.rollback();s.stripe_payment_intent_id='cs_old';self.db.commit()
        with patch.object(main.stripe.checkout.Session,'retrieve',return_value={'payment_status':'paid'}):
            with self.assertRaises(HTTPException):wallet.pay_study(self.db,s.id,self.uid)
        self.assertEqual(wallet.cash_balance(self.db,self.uid),10000)

if __name__=='__main__':unittest.main()
