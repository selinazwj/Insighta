import os
import unittest
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace

os.environ['DATABASE_URL']='sqlite:////private/tmp/insighta_withdrawals_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP']='1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER']='0'
from fastapi.testclient import TestClient
from fastapi import HTTPException
from api import main
from app.models import User, Survey, Response, Feedback
from app.withdrawals import ParticipantWithdrawal, _find_transfer
from app.payouts import reject_response_payout, return_response_to_review


class WithdrawalsTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine);main.Base.metadata.create_all(main.engine)
        self.db=main.SessionLocal();self.addCleanup(self.db.close)
        self.user=User(email='withdraw-test@example.invalid',password='x',pending_earnings=19.99,total_withdrawn=0,
                       stripe_account_id='acct_withdraw',stripe_onboarding_complete='true')
        self.db.add(self.user);self.db.flush();self.uid=self.user.id
        self.study=Survey(publisher_id=self.uid,title='Withdrawal test',description='Test only',form_url='https://example.invalid',
            category='research',estimated_time=5,reward_amount=19.99,target_responses=1,status='published')
        self.db.add(self.study);self.db.flush()
        self.response=Response(participant_id=self.uid,survey_id=self.study.id,status='completed',payout_status='approved',payout_amount=19.99)
        self.db.add(self.response);self.db.commit()
        self.client=TestClient(main.app);self.addCleanup(self.client.close);self.client.cookies.set('user_id',str(self.uid))
        key=patch.object(main.stripe,'api_key','sk_unit');key.start();self.addCleanup(key.stop)

    def transfer(self, **params):
        return main.stripe.Transfer.construct_from({'id':'tr_withdraw_1','object':'transfer','amount':1999,'currency':'usd',
            'destination':'acct_withdraw','metadata':{'insighta_withdrawal_id':'1','participant_id':str(self.uid)},
            'reversed':False,'amount_reversed':0,**params},None)

    def post(self):
        return self.client.post('/api/withdraw')

    def test_exact_cents_paid_once_and_status_is_owner_scoped(self):
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer()) as create:
            response=self.post();self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['amount'],19.99)
            self.assertEqual(create.call_args.kwargs['amount'],1999)
            self.assertEqual(create.call_args.kwargs['idempotency_key'],'participant-withdrawal:1:v1')
            self.assertEqual(self.post().status_code,400);self.assertEqual(create.call_count,1)
        self.db.expire_all();self.assertEqual(self.db.get(User,self.uid).pending_earnings,0)
        self.assertEqual(self.db.get(User,self.uid).total_withdrawn,19.99)
        self.assertEqual(self.db.get(Response,self.response.id).payout_status,'paid')
        self.assertEqual(self.client.get('/api/withdrawal/status').json()['withdrawal']['status'],'paid')
        other=User(email='withdraw-other@example.invalid',password='x');self.db.add(other);self.db.commit()
        self.client.cookies.set('user_id',str(other.id))
        self.assertIsNone(self.client.get('/api/withdrawal/status').json()['withdrawal'])

    def test_lost_response_reconciles_original_transfer_without_spending_new_earnings(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=main.stripe.error.APIConnectionError('private diagnostic')):
            failed=self.post();self.assertEqual(failed.status_code,503);self.assertNotIn('private diagnostic',failed.text)
        self.db.expire_all()
        for action in (reject_response_payout,return_response_to_review):
            with self.assertRaises(HTTPException) as error:action(self.db,self.db.get(Response,self.response.id))
            self.assertEqual(error.exception.status_code,409);self.db.rollback()
        self.user=self.db.get(User,self.uid);self.user.pending_earnings=24.99
        new=Response(participant_id=self.uid,survey_id=self.study.id,status='completed',payout_status='approved',payout_amount=5)
        self.db.add(new);self.db.commit();new_id=new.id
        with patch.object(main.stripe.Transfer,'list',return_value={'data':[self.transfer().to_dict()],'has_more':False}),patch.object(main.stripe.Transfer,'create') as create:
            result=self.post();self.assertEqual(result.status_code,200,result.text);create.assert_not_called()
        self.db.expire_all();self.assertAlmostEqual(self.db.get(User,self.uid).pending_earnings,5)
        self.assertEqual(self.db.get(Response,new_id).payout_status,'approved')

    def test_feedback_bonus_is_reserved_with_study_earnings(self):
        self.user.pending_earnings=24.99;self.db.commit()
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer(amount=2499)) as create:
            result=self.post();self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(create.call_args.kwargs['amount'],2499)
        self.db.expire_all();row=self.db.query(ParticipantWithdrawal).one()
        self.assertEqual(row.other_earnings_cents,500)
        self.assertEqual(row.response_amounts,{str(self.response.id):1999})
        self.assertEqual(self.db.get(User,self.uid).pending_earnings,0)

    def test_bonus_only_balance_does_not_mark_unrelated_responses_paid(self):
        self.response.payout_status='awaiting_review';self.user.pending_earnings=5;self.db.commit()
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer(amount=500)):
            result=self.post();self.assertEqual(result.status_code,200,result.text)
        self.db.expire_all();row=self.db.query(ParticipantWithdrawal).one()
        self.assertEqual(row.other_earnings_cents,500);self.assertEqual(row.response_amounts,{})
        self.assertEqual(self.db.get(Response,self.response.id).payout_status,'awaiting_review')

    def test_provider_retry_uses_unchanged_amount_destination_and_key(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=[main.stripe.error.APIConnectionError('timeout'),self.transfer()]) as create,patch.object(main.stripe.Transfer,'list',return_value={'data':[],'has_more':False}):
            self.assertEqual(self.post().status_code,503)
            result=self.post();self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(create.call_args_list[0].kwargs,create.call_args_list[1].kwargs)
        self.assertEqual(self.db.query(ParticipantWithdrawal).count(),1)

    def test_older_unknown_outcome_requires_manual_review_and_never_rotates_key(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=main.stripe.error.APIConnectionError('timeout')):
            self.assertEqual(self.post().status_code,503)
        row=self.db.query(ParticipantWithdrawal).one();row.created_at=datetime.utcnow()-timedelta(days=2);self.db.commit()
        with patch.object(main.stripe.Transfer,'list',return_value={'data':[],'has_more':False}),patch.object(main.stripe.Transfer,'create') as create:
            self.assertEqual(self.post().status_code,409);self.assertEqual(self.post().status_code,409);create.assert_not_called()
        self.db.expire_all();self.assertEqual(self.db.get(ParticipantWithdrawal,row.id).status,'manual_review')
        self.assertEqual(self.db.get(User,self.uid).pending_earnings,19.99)

    def test_wrong_provider_transfer_never_debits_earnings(self):
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer(amount=100)):
            self.assertEqual(self.post().status_code,409)
        self.db.expire_all();self.assertEqual(self.db.get(User,self.uid).pending_earnings,19.99)
        self.assertEqual(self.db.get(Response,self.response.id).payout_status,'approved')

    def test_definitive_transfer_decline_preserves_earnings_and_releases_reservation(self):
        error=main.stripe.error.InvalidRequestError('private provider details',param='balance',http_status=400)
        with patch.object(main.stripe.Transfer,'create',side_effect=error):
            result=self.post();self.assertEqual(result.status_code,409)
            self.assertNotIn('private provider details',result.text)
        self.db.expire_all();row=self.db.query(ParticipantWithdrawal).one()
        self.assertEqual(row.status,'failed');self.assertIsNone(row.active_key)
        self.assertEqual(self.db.get(User,self.uid).pending_earnings,19.99)

    def test_support_can_reconcile_existing_transfer_without_sending_money(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=main.stripe.error.APIConnectionError('timeout')):
            self.assertEqual(self.post().status_code,503)
        row=self.db.query(ParticipantWithdrawal).one();row.status='manual_review';self.db.commit()
        self.assertEqual(self.client.post('/admin/withdrawals/1/reconcile').status_code,403)
        with patch.dict(os.environ,{'ADMIN_KEY':'withdraw-unit-admin'}),patch.object(main.stripe.Transfer,'list',return_value={'data':[self.transfer().to_dict()],'has_more':False}),patch.object(main.stripe.Transfer,'create') as create:
            response=self.client.post('/admin/withdrawals/1/reconcile',headers={'x-admin-key':'withdraw-unit-admin'})
            self.assertEqual(response.status_code,200,response.text);create.assert_not_called()
        self.db.expire_all();self.assertEqual(self.db.get(User,self.uid).pending_earnings,0)

    def test_reserved_rewards_cannot_be_rejected_or_reset_during_unknown_outcome(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=main.stripe.error.APIConnectionError('timeout')):
            self.assertEqual(self.post().status_code,503)
        for action in (reject_response_payout,return_response_to_review):
            with self.assertRaises(HTTPException) as caught:
                action(self.db,self.response)
            self.assertEqual(caught.exception.status_code,409);self.db.rollback()

    def test_late_approved_rewards_are_not_consumed_by_an_earlier_transfer(self):
        with patch.object(main.stripe.Transfer,'create',side_effect=main.stripe.error.APIConnectionError('timeout')):
            self.assertEqual(self.post().status_code,503)
        self.db.expire_all()
        later=Response(survey_id=self.study.id,participant_id=self.uid,status='completed',payout_status='approved',payout_amount=5)
        self.db.add(later);self.user.pending_earnings=24.99;self.db.commit()
        with patch.object(main.stripe.Transfer,'list',return_value={'data':[self.transfer().to_dict()],'has_more':False}),patch.object(main.stripe.Transfer,'create') as create:
            self.assertEqual(self.post().status_code,200);create.assert_not_called()
        self.db.expire_all();self.assertEqual(self.db.get(User,self.uid).pending_earnings,5)
        self.assertEqual(self.db.get(Response,later.id).payout_status,'approved')

    def test_mismatched_balance_and_missing_onboarding_or_key_do_not_transfer(self):
        with patch.object(main.stripe.Transfer,'create') as create:
            self.user.pending_earnings=19;self.db.commit();self.assertEqual(self.post().status_code,409)
            self.user.stripe_onboarding_complete='false';self.db.commit();self.assertEqual(self.post().status_code,400)
            self.user.stripe_onboarding_complete='true';self.db.commit()
            with patch.object(main.stripe,'api_key',None):self.assertEqual(self.post().status_code,503)
            create.assert_not_called()
        self.assertEqual(self.db.query(ParticipantWithdrawal).count(),0)

    def test_feedback_awards_are_included_without_marking_unrelated_responses_paid(self):
        self.user.pending_earnings=24.99;self.db.commit()
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer(amount=2499)):
            response=self.post();self.assertEqual(response.status_code,200,response.text)
        self.db.expire_all();row=self.db.query(ParticipantWithdrawal).one()
        self.assertEqual(row.other_earnings_cents,500)
        self.assertEqual(row.amount_cents,2499)
        self.assertEqual(self.db.get(User,self.uid).pending_earnings,0)

    def test_feedback_credit_is_idempotent_and_cannot_be_rejected_after_credit(self):
        feedback=Feedback(user_id=self.uid,category='product',title='Test feedback',content='Test feedback only.',status='pending')
        self.db.add(feedback);self.db.commit()
        with patch.dict(os.environ,{'ADMIN_KEY':'withdraw-unit-admin'}):
            for _ in range(2):
                result=self.client.post(f'/admin/feedback/{feedback.id}/credit',json={'admin_key':'withdraw-unit-admin','amount':'5.00'})
                self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(self.client.post(f'/admin/feedback/{feedback.id}/credit',json={'admin_key':'withdraw-unit-admin','amount':'6.00'}).status_code,409)
            self.assertEqual(self.client.post(f'/admin/feedback/{feedback.id}/reject',json={'admin_key':'withdraw-unit-admin'}).status_code,409)
        self.db.expire_all();self.assertAlmostEqual(self.db.get(User,self.uid).pending_earnings,24.99)

    def test_paginated_reconciliation_finds_marker_on_later_page(self):
        row=SimpleNamespace(id=1,destination_account_id='acct_withdraw',created_at=datetime.utcnow())
        with patch.object(main.stripe.Transfer,'list',side_effect=[{'data':[{'id':'tr_other','metadata':{}}],'has_more':True},
            {'data':[self.transfer().to_dict()],'has_more':False}]) as listing:
            self.assertEqual(_find_transfer(row)['id'],'tr_withdraw_1')
            self.assertEqual(listing.call_args.kwargs['starting_after'],'tr_other')

    def test_concurrent_clicks_do_not_duplicate_accounting(self):
        with patch.object(main.stripe.Transfer,'create',return_value=self.transfer()) as create,patch.object(main.stripe.Transfer,'list',return_value={'data':[],'has_more':False}):
            with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:self.post(),range(2)))
        self.assertTrue(all(r.status_code in {200,400,409} for r in responses),[(r.status_code,r.text) for r in responses])
        self.db.expire_all();self.assertEqual(self.db.query(ParticipantWithdrawal).count(),1)
        self.assertEqual(self.db.get(User,self.uid).total_withdrawn,19.99)
        self.assertEqual(self.db.get(User,self.uid).pending_earnings,0)
        self.assertEqual(len({c.kwargs['idempotency_key'] for c in create.call_args_list}),1)

    def test_connect_links_stay_on_current_environment(self):
        with patch.object(main,'BASE_URL','https://isolated-staging.example'),patch.object(main.stripe.AccountLink,'create',return_value=SimpleNamespace(url='https://connect.stripe.com/test')) as create:
            response=self.client.get('/connect/onboard',follow_redirects=False)
            self.assertEqual(response.status_code,307)
            self.assertEqual(create.call_args.kwargs['return_url'],'https://isolated-staging.example/connect/complete')
            self.assertEqual(create.call_args.kwargs['refresh_url'],'https://isolated-staging.example/connect/onboard')

    def test_connect_checks_transfers_and_payouts_rather_than_charges(self):
        self.user.stripe_onboarding_complete='false';self.db.commit()
        account=main.stripe.Account.construct_from({'id':'acct_withdraw','object':'account','details_submitted':True,
            'charges_enabled':False,'payouts_enabled':True,'capabilities':{'transfers':'active'}},None)
        with patch.object(main.stripe.Account,'retrieve',return_value=account):
            self.assertEqual(self.client.get('/connect/complete',follow_redirects=False).status_code,307)
        self.db.expire_all();self.assertEqual(self.db.get(User,self.uid).stripe_onboarding_complete,'true')


if __name__=='__main__':unittest.main()
