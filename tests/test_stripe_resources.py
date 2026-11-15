import os,json,time,hmac,hashlib,unittest
from unittest.mock import patch
from types import SimpleNamespace
os.environ['DATABASE_URL']='sqlite:////private/tmp/insighta_stripe_resources_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP']='1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER']='0'
from fastapi.testclient import TestClient
from api import main
from app.models import User
from app.wallet_models import WalletTopUp,WalletLedger,BillingInvoice
from app.wallet import cash_balance,apply_part
from app.settlements import _funding_intent,_apply_refund
from app.stripe_data import plain_stripe_data

class StripeResourcesTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine);main.Base.metadata.create_all(main.engine)
        self.db=main.SessionLocal();self.addCleanup(self.db.close)
        self.user=User(email='typed-sdk@example.invalid',password='x');self.db.add(self.user);self.db.commit()
        self.client=TestClient(main.app);self.addCleanup(self.client.close)

    def test_real_signed_sdk_event_credits_exactly_once(self):
        row=WalletTopUp(user_id=self.user.id,token='typed-event',amount_cents=1000,stripe_session_id='cs_typed');self.db.add(row);self.db.commit()
        payload={'id':'evt_typed','object':'event','api_version':'2025-03-31.basil','type':'checkout.session.completed','data':{'object':{'id':'cs_typed','object':'checkout.session','payment_status':'paid','currency':'usd','amount_total':1000,'payment_intent':'pi_typed','metadata':{'wallet_topup_id':str(row.id),'user_id':str(self.user.id)}}}}
        actual=main.stripe.Event.construct_from(payload,None)
        with self.assertRaises(AttributeError):actual['data']['object'].get('id')
        raw=json.dumps(payload).encode();timestamp=str(int(time.time()));secret='whsec_unit_fixture_only'
        signature=hmac.new(secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        with patch.object(main,'STRIPE_WEBHOOK_SECRET',secret):
            for _ in range(2):
                response=self.client.post('/webhook/stripe',content=raw,headers={'stripe-signature':f't={timestamp},v1={signature}'})
                self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(cash_balance(self.db,self.user.id),1000)
        self.assertEqual(self.db.query(WalletLedger).count(),1)
        self.assertEqual(self.db.query(BillingInvoice).count(),1)

    def test_typed_checkout_and_nested_metadata_reconcile_funding(self):
        session=main.stripe.checkout.Session.construct_from({'id':'cs_original','object':'checkout.session','payment_status':'paid','amount_total':500,'currency':'usd','metadata':{'survey_id':'5'},'payment_intent':'pi_original'},None)
        study=SimpleNamespace(id=5,stripe_funding_payment_intent_id=None,stripe_payment_intent_id='cs_original')
        settlement=SimpleNamespace(pricing_snapshot={'total_cents':500},currency='usd')
        with patch.object(main.stripe.checkout.Session,'retrieve',return_value=session):
            self.assertEqual(_funding_intent(study,settlement),'pi_original')
        self.assertEqual(plain_stripe_data(session)['metadata'],{'survey_id':'5'})

    def test_typed_refunds_reconcile_parent_and_wallet_allocation(self):
        refund=main.stripe.Refund.construct_from({'object':'refund','id':'re_typed','amount':500,'currency':'usd','payment_intent':'pi_original','status':'succeeded','metadata':{'insighta_wallet_part_id':'1'}},None)
        parent=SimpleNamespace(refund_amount_cents=500,currency='usd',payment_intent_id='pi_original')
        _apply_refund(parent,refund);self.assertEqual(parent.status,'refunded')
        part=SimpleNamespace(amount_cents=500,payment_intent_id='pi_original')
        apply_part(part,refund);self.assertEqual(part.status,'refunded')
        self.assertEqual(part.stripe_refund_id,'re_typed')

if __name__=='__main__':unittest.main()
