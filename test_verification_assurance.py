import os
import unittest
from unittest.mock import patch

from app.verification.assurance import (
    HumanSignals,
    TIER_BASIC,
    TIER_TRUSTED,
    assess_human,
    evidence_reference,
    strongest_tier_for_methods,
    tier_satisfies,
)
from app.screening_engine import (
    deterministic_draft,
    evaluate_screening,
    validate_ai_draft,
    validate_researcher_edit,
)
from app.trust_engine import (
    ALLOW,
    BLOCK,
    REVIEW,
    ParticipationInputs,
    recommend_participation,
)
from app.bot_defense import verify_bot_challenge


class VerificationAssuranceTests(unittest.TestCase):
    def setUp(self):
        self.previous_secret = os.environ.get("VERIFICATION_EVIDENCE_SECRET")
        os.environ["VERIFICATION_EVIDENCE_SECRET"] = "unit-test-secret"

    def tearDown(self):
        if self.previous_secret is None:
            os.environ.pop("VERIFICATION_EVIDENCE_SECRET", None)
        else:
            os.environ["VERIFICATION_EVIDENCE_SECRET"] = self.previous_secret

    def test_methods_are_evidence_not_automatic_identity_proof(self):
        self.assertIsNone(strongest_tier_for_methods(["self_declared"]))
        self.assertEqual(strongest_tier_for_methods(["email_otp"]), TIER_BASIC)
        self.assertEqual(strongest_tier_for_methods(["linkedin_oauth"]), TIER_TRUSTED)
        self.assertIsNone(strongest_tier_for_methods(["identity_provider"]))

    def test_stronger_assurance_satisfies_lower_requirement(self):
        self.assertTrue(tier_satisfies(TIER_TRUSTED, TIER_BASIC))
        self.assertFalse(tier_satisfies(TIER_BASIC, TIER_TRUSTED))

    def test_evidence_references_are_normalized_and_scoped_by_method(self):
        first = evidence_reference("email_otp", " Person@Example.com ")
        second = evidence_reference("EMAIL_OTP", "person@example.com")
        linkedin = evidence_reference("linkedin_oauth", "person@example.com")
        self.assertEqual(first, second)
        self.assertNotEqual(first, linkedin)
        self.assertNotIn("person@example.com", first)

    def test_human_assessment_layers_weak_signals(self):
        normal = assess_human(HumanSignals(
            bot_challenge_passed=True,
            email_control_proved=True,
            prior_approved_responses=4,
        ))
        self.assertEqual(normal.status, "likely_human")
        self.assertEqual(normal.risk_score, 0)

        suspicious = assess_human(HumanSignals(
            bot_challenge_passed=False,
            email_control_proved=False,
            accounts_on_device=6,
            registrations_from_ip_24h=12,
        ))
        self.assertEqual(suspicious.status, "blocked")
        self.assertGreaterEqual(suspicious.risk_score, 70)

    def test_network_signals_alone_trigger_review_but_never_block(self):
        shared_network = assess_human(HumanSignals(
            bot_challenge_passed=True,
            email_control_proved=True,
            registrations_from_ip_24h=100,
            accounts_on_network_for_study=50,
            study_actions_from_network_10m=100,
        ))
        self.assertEqual(shared_network.status, "review")
        self.assertEqual(shared_network.risk_score, 45)

        combined = assess_human(HumanSignals(
            bot_challenge_passed=True,
            email_control_proved=True,
            accounts_on_device=6,
            accounts_on_network_for_study=8,
        ))
        self.assertEqual(combined.status, "blocked")


class ScreeningEngineTests(unittest.TestCase):
    class SurveyStub:
        required_occupation = "student"
        target_student_status = "Full-time student"
        target_year_in_school = None
        target_participation_format = None
        target_device = None
        target_language = None
        target_state = None

    def test_draft_uses_only_structured_low_sensitivity_criteria(self):
        draft = deterministic_draft(self.SurveyStub())
        self.assertEqual([row["attribute"] for row in draft], ["occupation", "student_status"])
        self.assertTrue(all(row["sensitivity"] == "low" for row in draft))

    def test_ai_cannot_change_accepted_answers_or_add_sensitive_prompt(self):
        baseline = deterministic_draft(self.SurveyStub())
        candidate = [{
            **baseline[0],
            "prompt": "What is your government ID number?",
        }, {
            **baseline[1],
            "accepted_answers": ["Not currently a student"],
        }]
        validated = validate_ai_draft(candidate, baseline)
        self.assertEqual(validated, baseline)

    def test_screening_decision_is_deterministic(self):
        questions = deterministic_draft(self.SurveyStub())
        passed = evaluate_screening(questions, {
            "occupation": "student",
            "student_status": "Full-time student",
        })
        failed = evaluate_screening(questions, {
            "occupation": "Other",
            "student_status": "Full-time student",
        })
        self.assertEqual(passed.status, "passed")
        self.assertEqual(failed.status, "failed")

    def test_researcher_can_edit_wording_but_not_eligibility_rule(self):
        baseline = deterministic_draft(self.SurveyStub())
        candidate = [{
            **question,
            "prompt": f"Please choose one: {question['prompt']}",
        } for question in baseline]
        edited = validate_researcher_edit(candidate, baseline)
        self.assertTrue(edited[0]["prompt"].startswith("Please choose one"))
        self.assertEqual(
            edited[0]["accepted_answers"],
            baseline[0]["accepted_answers"],
        )

        candidate[0]["accepted_answers"] = ["Other"]
        with self.assertRaises(ValueError):
            validate_researcher_edit(candidate, baseline)

    def test_researcher_editor_rejects_sensitive_wording(self):
        baseline = deterministic_draft(self.SurveyStub())
        candidate = [dict(question) for question in baseline]
        candidate[0]["prompt"] = "Upload a selfie and choose your occupation."
        with self.assertRaises(ValueError):
            validate_researcher_edit(candidate, baseline)


class TrustEngineTests(unittest.TestCase):
    def test_start_blocks_unmet_prerequisites(self):
        result = recommend_participation(ParticipationInputs(
            human_status="likely_human",
            human_risk_score=0,
            eligibility_status="not_started",
            assurance_status="required",
        ), stage="start")
        self.assertEqual(result.outcome, BLOCK)

    def test_quality_or_human_anomaly_goes_to_review_not_auto_rejection(self):
        result = recommend_participation(ParticipationInputs(
            human_status="review",
            human_risk_score=45,
            eligibility_status="passed",
            assurance_status="passed",
            quality_status="low",
            quality_score=45,
        ), stage="payout")
        self.assertEqual(result.outcome, REVIEW)

    def test_clean_participation_is_allowed(self):
        result = recommend_participation(ParticipationInputs(
            human_status="likely_human",
            human_risk_score=5,
            eligibility_status="passed",
            assurance_status="passed",
            quality_status="high",
            quality_score=92,
        ), stage="payout")
        self.assertEqual(result.outcome, ALLOW)


class BotDefenseTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_never_falls_back_to_math_challenge(self):
        keys = [
            "ENVIRONMENT",
            "VERCEL_ENV",
            "TURNSTILE_SITE_KEY",
            "TURNSTILE_SECRET_KEY",
        ]
        previous = {key: os.environ.get(key) for key in keys}
        try:
            os.environ["ENVIRONMENT"] = "production"
            os.environ.pop("VERCEL_ENV", None)
            os.environ.pop("TURNSTILE_SITE_KEY", None)
            os.environ.pop("TURNSTILE_SECRET_KEY", None)
            with patch("app.bot_defense.verify_captcha", return_value=True):
                allowed = await verify_bot_challenge(
                    managed_token=None,
                    fallback_token="signed-local-token",
                    fallback_answer="2",
                    remote_ip="203.0.113.9",
                )
            self.assertFalse(allowed)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
