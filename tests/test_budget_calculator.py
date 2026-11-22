import os
import unittest
from datetime import datetime,timedelta
os.environ['DATABASE_URL']='sqlite:////private/tmp/insighta_budget_calculator_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP']='1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER']='0'
from fastapi.testclient import TestClient
from api import main
from app.models import User,Survey
from app.wallet_models import WalletLedger,WalletCoupon,WalletPromoGrant
from app.budget_calculator import estimate_budget
from app.pricing import calculate_quote

class BudgetCalculatorTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine);main.Base.metadata.create_all(main.engine)
        self.db=main.SessionLocal();self.addCleanup(self.db.close)
        self.user=User(email='calculator@example.invalid',password='x');self.db.add(self.user);self.db.commit()
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.client.cookies.set('user_id',main.sign_session(self.user.id))
        self.body={'budget':'500','target_responses':'20','study_type':'survey','audience':'broad'}

    def estimate(self,**overrides):return estimate_budget(self.db,self.user.id,{**self.body,**overrides})

    def test_survey_floor_is_added_to_full_participant_reward(self):
        result=self.estimate();q=result['quote']
        self.assertEqual(q['reward_per_person_cents'],2000)
        self.assertEqual(q['recruitment_fee_per_person_cents'],500)
        self.assertEqual(q['total_cents'],50000)
        self.assertEqual(result['suggested_topup_cents'],50000)
        self.assertTrue(q['minimum_applied'])

    def test_percentage_fee_and_type_specific_minimums_use_publish_quote(self):
        for kind in ['survey','interview','in_person']:
            for budget,count in [('1200',20),('2000.17',31),('25.71',1),('500.01',20)]:
                result=self.estimate(study_type=kind,budget=budget,target_responses=str(count))
                q=result['quote']
                expected=calculate_quote(kind,'cash',f"{q['reward_per_person_cents']/100:.2f}",count,'flexible')
                self.assertEqual(q,expected)
                if result['status']=='below_minimum':continue
                self.assertLessEqual(q['total_cents'],int(round(float(budget)*100)))
                next_quote=calculate_quote(kind,'cash',f"{(q['reward_per_person_cents']+1)/100:.2f}",count,'flexible')
                self.assertGreater(next_quote['total_cents'],int(round(float(budget)*100)))
        self.assertEqual(self.estimate(study_type='interview')['quote']['minimum_fee_cents'],1000)
        self.assertEqual(self.estimate(study_type='in_person')['quote']['minimum_fee_cents'],1500)

    def test_listing_planner_includes_selected_urgency(self):
        result=self.estimate(urgency_level='within_1_week')
        q=result['quote']
        self.assertEqual(q['urgency_level'],'within_1_week')
        self.assertGreater(q['urgency_amount_cents'],0)
        self.assertLessEqual(q['total_cents'],50000)
        self.assertEqual(q,calculate_quote('survey','cash',f"{q['reward_per_person_cents']/100:.2f}",20,'within_1_week'))
        self.assertEqual(self.client.post('/api/wallet/budget-estimate',json={**self.body,'urgency_level':'invalid'}).status_code,400)

    def test_below_minimum_never_recommends_a_topup(self):
        result=self.estimate(budget='50')
        self.assertEqual(result['status'],'below_minimum')
        self.assertEqual(result['minimum_budget_cents'],10020)
        self.assertIsNone(result['suggested_topup_cents'])

    def test_niche_reference_never_invents_surcharge_or_recommends_deposit(self):
        broad=self.estimate()
        for audience in ['targeted','specialized']:
            result=self.estimate(audience=audience)
            self.assertEqual(result['quote'],broad['quote'])
            self.assertEqual(result['status'],'manual_quote_required')
            self.assertIsNone(result['suggested_topup_cents'])
            self.assertEqual(result['baseline_topup_cents'],50000)

    def test_existing_funding_and_eligible_promotion_reduce_gap_without_mutation(self):
        self.db.add(WalletLedger(user_id=self.user.id,delta_cents=20000,kind='topup',reference='fixture',description='Test fixture'))
        c=WalletCoupon(code='BUDGETTEST',amount_cents=20000,expires_at=datetime.utcnow()+timedelta(days=1),max_redemptions=1)
        self.db.add(c);self.db.flush()
        g=WalletPromoGrant(user_id=self.user.id,coupon_id=c.id,amount_cents=20000,remaining_cents=20000,expires_at=c.expires_at)
        self.db.add(g);self.db.commit()
        result=self.estimate()
        self.assertEqual(result['funding']['promo_applied_cents'],10000)
        self.assertEqual(result['suggested_topup_cents'],20000)
        self.db.refresh(g);self.assertEqual(g.remaining_cents,20000)
        self.assertEqual(self.db.query(WalletLedger).count(),1)
        self.assertEqual(self.db.query(Survey).count(),0)

    def test_sufficient_cash_and_stripe_minimum_topup(self):
        entry=WalletLedger(user_id=self.user.id,delta_cents=49999,kind='topup',reference='fixture',description='Test fixture')
        self.db.add(entry);self.db.commit()
        self.assertEqual(self.estimate()['suggested_topup_cents'],100)
        self.db.add(WalletLedger(user_id=self.user.id,delta_cents=100,kind='topup',reference='fixture2',description='Test fixture'));self.db.commit()
        self.assertEqual(self.estimate()['suggested_topup_cents'],0)

    def test_api_validation_authentication_and_layout(self):
        path='/api/wallet/budget-estimate'
        self.assertEqual(self.client.post(path,json=self.body).status_code,200)
        for invalid in [{'budget':'-1'},{'budget':'NaN'},{'target_responses':'1.5'},{'audience':[]},{'study_type':'niche'}]:
            self.assertEqual(self.client.post(path,json={**self.body,**invalid}).status_code,400)
        self.assertEqual(self.client.post(path,content='{broken').status_code,400)
        page=self.client.get('/publisher/wallet').text
        self.assertNotIn('Promotional credit history',page)
        self.assertIn('Payments &amp; refunds',page)
        self.assertLess(page.index('id="budgetCalculatorTitle"'),page.index('<h2>Cash balance'))
        self.assertLess(page.index('Payments &amp; refunds'),page.index('<h2>Cash balance'))
        self.client.cookies.clear();self.assertEqual(self.client.post(path,json=self.body).status_code,401)

if __name__=='__main__':unittest.main()
