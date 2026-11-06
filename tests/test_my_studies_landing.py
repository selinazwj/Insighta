import os
import json
import re
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite:////private/tmp/insighta_my_studies_landing_test.db'
os.environ['INSIGHTA_AUTO_SCHEMA_BOOTSTRAP'] = '1'

from fastapi.testclient import TestClient
from api import main
from app.models import Response, Survey, User


class MyStudiesLandingTest(unittest.TestCase):
    def setUp(self):
        main.Base.metadata.drop_all(bind=main.engine)
        main.Base.metadata.create_all(bind=main.engine)
        self.db = main.SessionLocal()
        self.addCleanup(self.db.close)
        publisher = User(email='publisher@example.com', password='x')
        participant = User(email='participant@example.com', password='x')
        self.db.add_all([publisher, participant])
        self.db.flush()
        self.survey = Survey(
            publisher_id=publisher.id, title='Study information',
            description='Full description of the research and eligibility.',
            form_url='https://example.com/questionnaire', category='research',
            task_type='survey', estimated_time=5, target_responses=10,
            reward_amount=1.37, status='published', share_slug='my-study-test',
        )
        self.db.add(self.survey)
        self.db.flush()
        self.response = Response(
            survey_id=self.survey.id, participant_id=participant.id,
            status='started', started_at=datetime(2026, 11, 1, 12),
        )
        self.db.add(self.response)
        self.db.commit()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.client.cookies.set('user_id', str(participant.id))

    def listing(self):
        return self.client.get('/r/my-study-test?from=my-studies')

    def studies_payload(self, page):
        return json.loads(re.search(r'const (?:surveys|allSurveys)\s*=\s*(\[.*?\]);', page.text).group(1))

    def test_continue_opens_information_without_restarting_participation(self):
        studies = self.client.get('/my-studies')
        self.assertEqual(studies.status_code, 200)
        self.assertEqual(self.studies_payload(studies)[0]['share_path'], '/r/my-study-test')
        started_at = self.response.started_at
        page = self.listing()
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.survey.description, page.text)
        self.assertIn('You already started this study.', page.text)
        self.assertIn('>Continue study</button>', page.text)
        self.assertIn('href="/my-studies" data-brand-action="my-studies"', page.text)
        self.db.refresh(self.response)
        self.assertEqual(self.response.status, 'started')
        self.assertEqual(self.response.started_at, started_at)
        self.assertEqual(self.db.query(Response).count(), 1)

    def test_dashboard_start_and_my_studies_share_the_same_information_page(self):
        self.db.delete(self.response)
        self.db.commit()
        with patch.dict(os.environ, {'INSIGHTA_DASHBOARD_AI_RECOMMENDATIONS': '0'}):
            for route in ['/dashboard', '/dashboard/mobile']:
                page = self.client.get(route)
                self.assertEqual(page.status_code, 200)
                self.assertEqual(self.studies_payload(page)[0]['share_path'], '/r/my-study-test')
        with patch.object(main, '_participation_decision', return_value=(SimpleNamespace(outcome='allow'), None)), patch.object(
            main, '_enqueue_survey_start_followup_if_needed', return_value=None,
        ):
            started = self.client.get(f'/surveys/{self.survey.id}/start-redirect',
                                      params={'next': self.survey.form_url}, follow_redirects=False)
        self.assertEqual(started.status_code, 303)
        self.assertEqual(started.headers['location'], self.survey.form_url)
        studies = self.client.get('/my-studies')
        self.assertEqual(self.studies_payload(studies)[0]['share_path'], '/r/my-study-test')
        self.assertTrue(self.studies_payload(studies)[0]['is_started'])
        self.assertEqual(self.listing().status_code, 200)
        self.assertEqual(self.db.query(Response).count(), 1)

    def test_continuing_from_information_page_still_requires_screening(self):
        with patch.object(main, '_screening_passed', return_value=False):
            redirected = self.client.get(f'/surveys/{self.survey.id}/start-redirect',
                                         params={'next': self.survey.form_url}, follow_redirects=False)
        self.assertEqual(redirected.status_code, 303)
        self.assertEqual(redirected.headers['location'], f'/surveys/{self.survey.id}/screening')

    def test_builtin_study_continues_from_information_page(self):
        self.survey.form_url = '__builtin__'
        self.db.commit()
        page = self.listing()
        self.assertIn(f"startStudy('/surveys/{self.survey.id}/take')", page.text)
        self.assertIn('>Continue study</button>', page.text)

    def test_completed_and_closed_studies_keep_information_without_start_action(self):
        self.response.status = 'completed'
        self.db.commit()
        page = self.listing()
        self.assertIn('You already completed this study.', page.text)
        self.assertNotIn('onclick="startExternalStudy(', page.text)
        self.assertIn('View in My Studies', page.text)
        self.survey.status = 'closed'
        self.db.commit()
        page = self.listing()
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.survey.description, page.text)
        self.assertIn('This listing is currently closed.', page.text)
        self.assertNotIn('onclick="startExternalStudy(', page.text)

    def test_scheduled_interview_preserves_booking(self):
        self.survey.task_type = 'interview'
        self.response.status = 'scheduled'
        self.response.booking_slot = '["Fri, Nov 6, 10:00 - 10:30"]'
        self.db.commit()
        page = self.listing()
        self.assertIn('Your interview is scheduled.', page.text)
        self.assertIn('Open meeting link', page.text)
        self.db.refresh(self.response)
        self.assertEqual(self.response.status, 'scheduled')
        self.assertEqual(self.response.booking_slot, '["Fri, Nov 6, 10:00 - 10:30"]')

    def test_skipped_interview_can_start_from_information_page(self):
        self.survey.task_type = 'interview'
        self.response.status = 'skipped'
        self.db.commit()
        page = self.listing()
        self.assertNotIn('Your interview is awaiting researcher confirmation.', page.text)
        self.assertIn('onclick="startExternalStudy(', page.text)
        self.db.refresh(self.response)
        self.assertEqual(self.response.status, 'skipped')


if __name__ == '__main__':
    unittest.main()
