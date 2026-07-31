import os
import re
import unittest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


TEST_DATABASE_URL = "sqlite:////private/tmp/insighta_verification_integration.db"


@unittest.skipUnless(
    os.environ.get("DATABASE_URL") == TEST_DATABASE_URL,
    "Set the isolated integration-test DATABASE_URL to run this module.",
)
class VerificationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from api.main import app
        from app.database import SessionLocal

        cls.client = TestClient(app)
        cls.SessionLocal = SessionLocal

    @classmethod
    def tearDownClass(cls):
        from app.database import engine

        cls.client.close()
        engine.dispose()

    def setUp(self):
        from app.models import (
            ParticipationDecision,
            EmailVerificationCode,
            RiskSignalEvent,
            Response,
            ScreeningAttempt,
            ScreeningQuestion,
            Survey,
            User,
            UserEvent,
            Verification,
        )

        db = self.SessionLocal()
        try:
            db.query(ParticipationDecision).delete()
            db.query(RiskSignalEvent).delete()
            db.query(ScreeningAttempt).delete()
            db.query(ScreeningQuestion).delete()
            db.query(Verification).delete()
            db.query(EmailVerificationCode).delete()
            db.query(UserEvent).delete()
            db.query(Response).delete()
            db.query(Survey).delete()
            db.query(User).delete()
            publisher = User(email="publisher@example.com", password="x")
            participant = User(
                email="participant@example.com",
                password="x",
                verification_status="verified",
                verified_email="participant@example.com",
            )
            db.add_all([publisher, participant])
            db.flush()
            survey = Survey(
                publisher_id=publisher.id,
                title="Student habits",
                description="A test study",
                form_url="__builtin__",
                category="research",
                estimated_time=5,
                reward_amount=5.0,
                target_responses=10,
                status="published",
                required_occupation="student",
            )
            db.add(survey)
            db.commit()
            self.publisher_id = publisher.id
            self.participant_id = participant.id
            self.survey_id = survey.id
        finally:
            db.close()

    def test_retired_auto_verify_route_is_not_mounted(self):
        response = self.client.post("/verifications/start?user_id=1")
        self.assertEqual(response.status_code, 404)

    def test_raw_id_routes_are_removed(self):
        response = self.client.post(
            "/verify/id-upload",
            cookies={"user_id": str(self.participant_id)},
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.get(
            f"/researcher/id-document/{self.participant_id}",
            cookies={"user_id": str(self.publisher_id)},
        )
        self.assertEqual(response.status_code, 404)

    def test_registration_has_two_steps_and_no_occupation_or_id_tiers(self):
        response = self.client.get("/register?role=participant")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Step 1 of 2", response.text)
        self.assertIn("Step 2 of 2", response.text)
        self.assertNotIn('name="occupation"', response.text)
        self.assertNotIn("Step 3 of 3", response.text)

        response = self.client.get(
            "/verify",
            cookies={"user_id": str(self.participant_id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("High Assurance", response.text)
        self.assertNotIn("ID upload", response.text)

    def test_registration_succeeds_without_occupation(self):
        from app.models import EmailVerificationCode, User

        response = self.client.get("/register?role=participant")
        self.assertEqual(response.status_code, 200)
        token_match = re.search(
            r'name="captcha_token" value="([^"]+)"',
            response.text,
        )
        question_match = re.search(
            r"Quick check: what is (\d+) \+ (\d+)\?",
            response.text,
        )
        self.assertIsNotNone(token_match)
        self.assertIsNotNone(question_match)

        email = "new-participant@example.com"
        code = "246810"
        db = self.SessionLocal()
        try:
            db.add(EmailVerificationCode(
                email=email,
                purpose="register",
                code=code,
                expires_at=datetime.utcnow() + timedelta(minutes=10),
            ))
            db.commit()
        finally:
            db.close()

        captcha_answer = sum(int(value) for value in question_match.groups())
        response = self.client.post(
            "/register",
            data={
                "role": "participant",
                "email": email,
                "verification_code": code,
                "first_name": "New",
                "last_name": "Participant",
                "username": "new-participant",
                "birth_year": "2000",
                "birth_month": "1",
                "education_level": "Undergraduate",
                "field": "Social Sciences",
                "status": "Undergraduate student",
                "current_province": "California",
                "language": "English",
                "participation_format": "Online surveys",
                "password": "StrongPass1!",
                "confirm": "StrongPass1!",
                "captcha_token": token_match.group(1),
                "captcha_answer": str(captcha_answer),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        db = self.SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).one()
            self.assertIsNone(user.occupation)
            self.assertEqual(user.verified_tier, "tier_3")
        finally:
            db.close()

    def test_researcher_forms_use_screening_without_tier_selector(self):
        publisher_cookie = {"user_id": str(self.publisher_id)}
        paths = (
            "/publish",
            "/publish_interview",
            f"/publisher/edit/{self.survey_id}",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path, cookies=publisher_cookie)
                self.assertEqual(response.status_code, 200)
                self.assertIn('name="required_occupation"', response.text)
                self.assertNotIn(
                    'name="required_verification_tier"',
                    response.text,
                )
                self.assertNotIn("High Assurance", response.text)
                self.assertNotIn("Tier 1", response.text)

    def test_editing_preserves_stronger_legacy_study_requirement(self):
        from app.models import Survey

        db = self.SessionLocal()
        try:
            survey = db.query(Survey).filter(Survey.id == self.survey_id).one()
            survey.required_verification_tier = "tier_2"
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            f"/publisher/edit/{self.survey_id}",
            data={
                "title": "Student habits updated",
                "description": "A test study",
                "form_url": "__builtin__",
                "task_type": "survey",
                "category": "research",
                "estimated_time": "5",
                "reward_amount": "5",
                "additional_needed": "5",
            },
            cookies={"user_id": str(self.publisher_id)},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        db = self.SessionLocal()
        try:
            survey = db.query(Survey).filter(Survey.id == self.survey_id).one()
            self.assertEqual(survey.required_verification_tier, "tier_2")
        finally:
            db.close()

    def test_same_network_multi_account_study_activity_is_review_only(self):
        from app.models import ParticipationDecision, RiskSignalEvent, User
        from app.verification.assurance import evidence_reference

        network_ip = "203.0.113.8"
        network_ref = evidence_reference("risk_network", network_ip)
        db = self.SessionLocal()
        try:
            for index in range(4):
                user = User(
                    email=f"network-user-{index}@example.com",
                    password="x",
                    verification_status="verified",
                    verified_email=f"network-user-{index}@example.com",
                )
                db.add(user)
                db.flush()
                db.add(RiskSignalEvent(
                    user_id=user.id,
                    survey_id=self.survey_id,
                    event_type="start",
                    network_ref=network_ref,
                ))
            db.commit()
        finally:
            db.close()

        response = self.client.post(
            f"/surveys/{self.survey_id}/start",
            headers={"x-forwarded-for": network_ip},
            cookies={"user_id": str(self.participant_id)},
        )
        self.assertEqual(response.status_code, 200)

        db = self.SessionLocal()
        try:
            decision = db.query(ParticipationDecision).filter(
                ParticipationDecision.participant_id == self.participant_id,
                ParticipationDecision.survey_id == self.survey_id,
                ParticipationDecision.stage == "start",
            ).order_by(ParticipationDecision.created_at.desc()).first()
            self.assertIsNotNone(decision)
            self.assertEqual(decision.human_status, "review")
            self.assertEqual(decision.outcome, "review")
        finally:
            db.close()

    def test_approved_screener_gates_study_start(self):
        publisher_cookie = {"user_id": str(self.publisher_id)}
        participant_cookie = {"user_id": str(self.participant_id)}

        # Occupation is no longer collected at signup or used to hide studies.
        # The study remains visible and confirms occupation through screening.
        response = self.client.get("/dashboard", cookies=participant_cookie)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Student habits", response.text)

        response = self.client.post(
            f"/api/surveys/{self.survey_id}/screening/draft",
            cookies=publisher_cookie,
        )
        self.assertEqual(response.status_code, 200)
        version = response.json()["version"]
        response = self.client.get(
            f"/api/surveys/{self.survey_id}/screening",
            cookies=publisher_cookie,
        )
        questions = [
            question for question in response.json()["questions"]
            if question["version"] == version
        ]
        questions[0]["prompt"] = "Which current occupation best describes you?"
        response = self.client.put(
            f"/api/surveys/{self.survey_id}/screening/draft",
            json={"version": version, "questions": questions},
            cookies=publisher_cookie,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/surveys/{self.survey_id}/screening/approve",
            json={"version": version},
            cookies=publisher_cookie,
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.get(
            f"/publisher/study/{self.survey_id}",
            cookies=publisher_cookie,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI-assisted screening draft", response.text)
        response = self.client.get(
            f"/surveys/{self.survey_id}/screening",
            cookies=participant_cookie,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Study eligibility", response.text)

        response = self.client.post(
            f"/surveys/{self.survey_id}/start",
            cookies=participant_cookie,
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["screening_required"])

        from app.models import ScreeningQuestion

        db = self.SessionLocal()
        try:
            questions = db.query(ScreeningQuestion).filter(
                ScreeningQuestion.survey_id == self.survey_id,
                ScreeningQuestion.status == "approved",
            ).all()
            form = {
                f"answer_{question.id}": question.accepted_answers[0]
                for question in questions
            }
        finally:
            db.close()

        response = self.client.post(
            f"/surveys/{self.survey_id}/screening",
            data=form,
            cookies=participant_cookie,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        response = self.client.post(
            f"/surveys/{self.survey_id}/start",
            cookies=participant_cookie,
        )
        self.assertEqual(response.status_code, 200)
        from app.models import ParticipationDecision

        db = self.SessionLocal()
        try:
            decision = db.query(ParticipationDecision).filter(
                ParticipationDecision.survey_id == self.survey_id,
                ParticipationDecision.participant_id == self.participant_id,
                ParticipationDecision.stage == "start",
            ).order_by(ParticipationDecision.created_at.desc()).first()
            self.assertIsNotNone(decision)
            self.assertEqual(decision.eligibility_status, "passed")
            self.assertEqual(decision.assurance_status, "passed")
        finally:
            db.close()
        response = self.client.get(
            f"/api/surveys/{self.survey_id}/trust-summary",
            cookies=publisher_cookie,
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(
            response.json()["by_stage"]["start"]["allow"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
