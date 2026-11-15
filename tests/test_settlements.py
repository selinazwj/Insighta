import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:////private/tmp/insighta_settlement_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP'] = '1'
os.environ['INSIGHTA_ENABLE_SETTLEMENT_WORKER'] = '0'
from fastapi.testclient import TestClient
from fastapi import HTTPException
from api import main
from app.models import Survey, Response, User, StudySettlement, Notification
from app.pricing import calculate_quote
from app.payouts import mark_response_under_review, release_response_payout, reject_response_payout
from app.settlements import scan_settlements, process_refund, run_settlement_cycle, refund_cents, ensure_settlement


class SettlementTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(main.engine)
        main.Base.metadata.create_all(main.engine)
        self.db = main.SessionLocal()
        self.addCleanup(self.db.close)
        self.owner = User(email='settlement-owner@example.invalid', password='x')
        self.db.add(self.owner)
        self.db.flush()
        self.now = datetime.utcnow()
        self.client = TestClient(main.app)
        self.client.cookies.set('user_id', str(self.owner.id))
        self.addCleanup(self.client.close)
        self.key = patch.object(main.stripe, 'api_key', 'sk_test_mock')
        self.key.start()
        self.addCleanup(self.key.stop)

    def study(self, count=20, kind='in_person', reward='40', closed=True, reason='completed', legacy=False):
        q = calculate_quote(kind, 'cash', reward, count, 'within_1_week')
        s = Survey(publisher_id=self.owner.id, title='Settlement test', description='Test', form_url='https://example.invalid',
                   category='research', task_type=kind, estimated_time=30, reward_amount=q['reward_per_person_cents']/100,
                   target_responses=count, status='closed' if closed else 'published', payment_status='paid',
                   pricing_snapshot=None if legacy else q, closed_at=self.now if closed else None,
                   close_reason=reason, stripe_payment_intent_id='cs_mock', stripe_funding_payment_intent_id='pi_mock')
        self.db.add(s)
        self.db.commit()
        return s

    def completion(self, study, days=8, status='pending_review', participant=None):
        person = participant or User(email=f'participant-{self.db.query(User).count()}@example.invalid', password='x', pending_earnings=0)
        if not participant:
            self.db.add(person)
            self.db.flush()
        r = Response(survey_id=study.id, participant_id=person.id, status='completed',
                     completed_at=self.now-timedelta(days=days), payout_amount=study.reward_amount,
                     payout_status=status)
        self.db.add(r)
        self.db.flush()
        if status=='pending_review': mark_response_under_review(r)
        self.db.commit()
        return r

    def scan(self, now=None, verified=True):
        scan_settlements(self.db, lambda *_: verified, now=now or self.now)
        self.db.expire_all()
        return self.db.query(StudySettlement).first()

    def refund(self, row, status='succeeded'):
        return dict(id='re_mock', amount=row.refund_amount_cents, currency='usd', payment_intent='pi_mock',
                    status=status, metadata={'insighta_settlement_id': str(row.id)})

    def empty_refunds(self):
        return patch.object(main.stripe.Refund, 'list', return_value=SimpleNamespace(auto_paging_iter=lambda: iter([])))

    def test_snapshot_refund_formula_and_cancellation(self):
        q = calculate_quote('in_person', 'cash', '40', 20, 'within_1_week')
        self.assertEqual(refund_cents(q, 16), 24200)
        self.assertEqual(refund_cents(q, 0), 121000)
        self.assertEqual(refund_cents(q, 0, 'researcher_cancelled'), 88000)
        self.assertEqual(refund_cents(q, 200), 0)

    def test_close_waits_for_review_then_auto_approves_and_refunds(self):
        s = self.study(count=2)
        r = self.completion(s, days=6)
        row = self.scan()
        self.assertEqual(row.status, 'waiting_review')
        self.assertIsNone(row.refund_amount_cents)
        row = self.scan(now=self.now+timedelta(days=2))
        self.assertEqual(row.status, 'ready')
        self.assertEqual(row.qualified_count, 1)
        self.assertEqual(row.refund_amount_cents, 6050)
        self.assertEqual(self.db.get(Response, r.id).payout_status, 'approved')
        self.assertEqual(self.db.get(User, r.participant_id).pending_earnings, 40)
        with self.empty_refunds(), patch.object(main.stripe.Refund, 'create', return_value=self.refund(row)) as create:
            process_refund(self.db, row.id, now=self.now+timedelta(days=2))
            process_refund(self.db, row.id, now=self.now+timedelta(days=3))
        self.assertEqual(create.call_count, 1)
        self.assertEqual(row.status, 'refunded')
        self.assertEqual(create.call_args.kwargs['amount'], 6050)
        self.assertEqual(create.call_args.kwargs['payment_intent'], 'pi_mock')

    def test_verification_hold_is_never_refunded_as_unused(self):
        s = self.study(count=1)
        r = self.completion(s)
        self.assertEqual(self.scan(verified=False).status, 'waiting_verification')
        self.assertEqual(self.db.get(Response, r.id).payout_status, 'pending_review')
        self.assertEqual(self.scan(verified=True).status, 'no_refund')

    def test_over_recruitment_and_duplicate_people_do_not_gain_extra_rewards(self):
        s = self.study(count=1)
        first = self.completion(s)
        second = self.completion(s)
        duplicate = self.completion(s, participant=self.db.get(User, first.participant_id))
        row = self.scan()
        self.assertEqual(row.qualified_count, 1)
        self.assertEqual(row.status, 'no_refund')
        self.assertEqual(self.db.get(Response, second.id).payout_status, 'over_capacity')
        self.assertEqual(self.db.get(Response, duplicate.id).payout_status, 'over_capacity')
        self.assertEqual(self.db.get(User, first.participant_id).pending_earnings, 40)
        self.assertEqual(self.db.get(User, second.participant_id).pending_earnings, 0)

    def test_deadline_closes_paid_quoted_study_and_legacy_stays_unchanged(self):
        s = self.study(count=1, closed=False)
        s.deadline_at = self.now-timedelta(seconds=1)
        old = self.study(count=1, closed=False, legacy=True)
        old.deadline_at = self.now-timedelta(seconds=1)
        legacy_response = self.completion(old)
        self.db.commit()
        row = self.scan()
        self.assertEqual(self.db.get(Survey, s.id).status, 'closed')
        self.assertEqual(row.close_reason, 'deadline')
        self.assertEqual(self.db.get(Survey, old.id).status, 'published')
        self.assertEqual(self.db.get(Response, legacy_response.id).payout_status, 'pending_review')
        self.assertEqual(self.db.query(StudySettlement).count(), 1)

    def test_review_rejection_window_and_frozen_settlement_are_enforced(self):
        s = self.study(count=2)
        r = self.completion(s)
        with self.assertRaises(HTTPException) as error:
            reject_response_payout(self.db, r)
        self.assertEqual(error.exception.status_code, 409)
        self.db.rollback()
        row = self.scan()
        self.assertEqual(row.status, 'ready')
        with self.assertRaises(HTTPException): reject_response_payout(self.db, r)

    def test_approved_zero_unused_never_calls_stripe(self):
        s = self.study(count=1)
        self.completion(s, status='approved')
        with patch.object(main.stripe.Refund, 'create') as create:
            run_settlement_cycle(self.db, lambda *_: True, now=self.now)
        create.assert_not_called()
        self.assertEqual(self.db.query(StudySettlement).one().status, 'no_refund')

    def test_timeout_reconciles_existing_refund_after_idempotency_window(self):
        self.study(count=1)
        row = self.scan()
        with self.empty_refunds(), patch.object(main.stripe.Refund, 'create', side_effect=TimeoutError('unknown provider outcome')) as create:
            process_refund(self.db, row.id, now=self.now)
        self.assertEqual(row.status, 'retrying')
        found = self.refund(row)
        with patch.object(main.stripe.Refund, 'list', return_value=SimpleNamespace(auto_paging_iter=lambda: iter([found]))), patch.object(main.stripe.Refund, 'create') as second:
            process_refund(self.db, row.id, now=self.now+timedelta(days=2))
        self.assertEqual(create.call_count, 1)
        second.assert_not_called()
        self.assertEqual(row.status, 'refunded')

    def test_pending_refund_waits_for_authoritative_success(self):
        self.study(count=1)
        row = self.scan()
        with self.empty_refunds(), patch.object(main.stripe.Refund, 'create', return_value=self.refund(row, 'pending')):
            process_refund(self.db, row.id, now=self.now)
        self.assertEqual(row.status, 'refund_pending')
        with patch.object(main.stripe.Refund, 'retrieve', return_value=self.refund(row)), patch.object(main.stripe.Refund, 'create') as create:
            process_refund(self.db, row.id, now=self.now+timedelta(minutes=10))
        self.assertEqual(row.status, 'refunded')
        create.assert_not_called()

    def test_failed_provider_refund_requires_manual_review_not_new_refund(self):
        self.study(count=1)
        row = self.scan()
        with self.empty_refunds(), patch.object(main.stripe.Refund, 'create', return_value=self.refund(row, 'failed')):
            process_refund(self.db, row.id, now=self.now)
        self.assertEqual(row.status, 'manual_review')
        self.assertEqual(self.client.post(f'/api/surveys/{row.survey_id}/settlement/retry').status_code, 409)

    def test_missing_key_retries_are_bounded(self):
        self.study(count=1)
        row = self.scan()
        with patch.object(main.stripe, 'api_key', None), patch.object(main.stripe.Refund, 'create') as create:
            for attempt in range(6): process_refund(self.db, row.id, now=self.now+timedelta(days=attempt))
        create.assert_not_called()
        self.assertEqual(row.status, 'manual_review')
        self.assertEqual(row.attempts, 5)

    def test_phase_one_checkout_can_resolve_funding_without_repricing(self):
        s = self.study(count=1)
        s.stripe_funding_payment_intent_id = None
        self.db.commit()
        row = self.scan()
        session = dict(payment_status='paid', amount_total=6050, currency='usd', payment_intent='pi_mock', metadata={'survey_id':str(s.id)})
        with patch.object(main.stripe.checkout.Session, 'retrieve', return_value=session), self.empty_refunds(), patch.object(main.stripe.Refund, 'create', return_value=self.refund(row)):
            process_refund(self.db, row.id, now=self.now)
        self.assertEqual(row.status, 'refunded')
        self.assertEqual(row.pricing_snapshot['total_cents'], 6050)

    def test_funding_mismatch_never_creates_refund(self):
        s = self.study(count=1)
        s.stripe_funding_payment_intent_id = None
        self.db.commit()
        row = self.scan()
        with patch.object(main.stripe.checkout.Session, 'retrieve', return_value={'amount_total':1}), patch.object(main.stripe.Refund, 'create') as create:
            process_refund(self.db, row.id, now=self.now)
        create.assert_not_called()
        self.assertNotEqual(row.status, 'refunded')

    def test_owner_close_summary_reopen_delete_and_cancel_rules(self):
        s = self.study(count=1, closed=False)
        self.assertEqual(self.client.post(f'/surveys/{s.id}/close', data={'close_reason':'researcher_cancelled'}, follow_redirects=False).status_code, 303)
        self.db.expire_all()
        row = self.scan()
        self.assertEqual(row.refund_amount_cents, 4400)
        self.assertEqual(self.client.post(f'/surveys/{s.id}/reopen', follow_redirects=False).status_code, 409)
        self.assertEqual(self.client.post(f'/publisher/delete/{s.id}', follow_redirects=False).status_code, 409)
        status = self.client.get(f'/api/surveys/{s.id}/settlement')
        self.assertEqual(status.json()['settlement']['refund_amount_cents'], 4400)
        detail = self.client.get(f'/publisher/study/{s.id}')
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn('Refund to original card(s)', detail.text)
        other = User(email='other@example.invalid', password='x')
        self.db.add(other); self.db.commit()
        self.client.cookies.set('user_id', str(other.id))
        self.assertEqual(self.client.get(f'/api/surveys/{s.id}/settlement').status_code, 404)

    def test_deadline_completion_and_late_live_completion_are_blocked(self):
        s = self.study(count=1, closed=False)
        r = self.completion(s)
        s.deadline_at = self.now-timedelta(days=1)
        self.db.commit()
        result = self.client.post(f'/api/surveys/{s.id}/bookings/{r.id}/complete')
        self.assertEqual(result.status_code, 409)

    def test_no_show_frees_place_without_changing_order_price(self):
        s = self.study(count=1, closed=False)
        r = self.completion(s)
        r.status, r.payout_status = 'scheduled', 'pending'
        self.db.commit()
        response = self.client.post(f'/api/surveys/{s.id}/bookings/{r.id}/no-show')
        self.assertEqual(response.status_code, 200, response.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Response, r.id).status, 'no_show')
        self.assertEqual(self.db.get(Survey, s.id).pricing_snapshot['total_cents'], 6050)
        self.completion(s)
        self.assertIsNone(self.scan())
        self.client.post(f'/surveys/{s.id}/close', follow_redirects=False)
        self.assertEqual(self.scan().status, 'no_refund')

    def test_early_rejection_makes_unused_place_refundable(self):
        s = self.study(count=1)
        r = self.completion(s, days=1)
        reject_response_payout(self.db, r)
        self.db.commit()
        row = self.scan()
        self.assertEqual(row.qualified_count, 0)
        self.assertEqual(row.refund_amount_cents, 6050)

    def test_refund_total_ignores_later_pricing_configuration(self):
        self.study(count=1)
        with patch('app.pricing.pricing_config', side_effect=RuntimeError('New prices unavailable')):
            row = self.scan()
        self.assertEqual(row.refund_amount_cents, 6050)

    def test_stale_approval_cannot_credit_the_same_reward_twice(self):
        s = self.study(count=1, closed=False)
        r = self.completion(s, days=1)
        response_id, participant_id = r.id, r.participant_id
        with main.SessionLocal() as other:
            other_response = other.get(Response, response_id)
            release_response_payout(other, other_response)
            other.commit()
        # This session still holds the pre-approval response object.
        self.assertEqual(r.payout_status, 'pending_review')
        release_response_payout(self.db, r)
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(self.db.get(User, participant_id).pending_earnings, 40)

    def test_paid_reward_cannot_be_rejected_even_inside_review_window(self):
        s = self.study(count=1, closed=False)
        r = self.completion(s, days=1, status='paid')
        with self.assertRaises(HTTPException): reject_response_payout(self.db, r)

    def test_refund_webhook_uses_authoritative_status(self):
        self.study(count=1)
        row = self.scan()
        row.payment_intent_id = 'pi_mock'; row.stripe_refund_id = 're_mock'; row.status = 'refund_pending'
        self.db.commit()
        old_event = self.refund(row, 'pending')
        with patch.object(main.stripe.Webhook, 'construct_event', return_value={'type':'refund.updated','data':{'object':old_event}}), patch.object(main.stripe.Refund, 'retrieve', return_value=self.refund(row)):
            self.assertEqual(self.client.post('/webhook/stripe',content=b'mock').status_code,200)
        self.db.expire_all()
        self.assertEqual(self.db.get(StudySettlement,row.id).status,'refunded')


if __name__ == '__main__':
    unittest.main()
