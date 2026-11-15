import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('staging', Path(__file__).resolve().parents[1] / 'scripts/start_staging.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


class StagingTest(unittest.TestCase):
    def config(self):
        return {'INSIGHTA_ENV': 'staging', 'DATABASE_URL': 'postgresql://test:test@localhost/insighta_pricing_staging',
                'RENDER_EXTERNAL_URL': 'https://pricing-test.onrender.com'}

    def test_urls_are_derived_from_test_service(self):
        env = self.config()
        env['BASE_URL'] = 'https://production.invalid'
        staging.configure_staging(env)
        self.assertEqual(env['BASE_URL'], 'https://pricing-test.onrender.com')
        self.assertEqual(env['SEO_SITE_URL'], env['BASE_URL'])
        self.assertEqual(env['SEO_INDEX_STUDIES'], 'false')

    def test_rejects_wrong_environment_database_and_live_payment_keys(self):
        for changes in [{'INSIGHTA_ENV': 'production'}, {'DATABASE_URL': 'sqlite:///survey.db'},
                        {'DATABASE_URL': 'postgresql://test:test@localhost/production'},
                        {'STRIPE_SECRET_KEY': 'sk_live_fake'}, {'STRIPE_PUBLISHABLE_KEY': 'pk_live_fake'}]:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                staging.configure_staging({**self.config(), **changes})

    def test_accepts_test_keys_or_disabled_checkout(self):
        for keys in [{}, {'STRIPE_SECRET_KEY': 'sk_test_fake', 'STRIPE_PUBLISHABLE_KEY': 'pk_test_fake'}]:
            staging.configure_staging({**self.config(), **keys})

class StagingAccountTest(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.models import Base
        from passlib.context import CryptContext
        self.engine=create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db=Session(self.engine);self.addCleanup(self.engine.dispose);self.addCleanup(self.db.close)
        self.hash=CryptContext(schemes=['bcrypt']).hash('only-for-local-unit-test')
        self.env={**StagingTest().config(),'STAGING_RESEARCHER_EMAIL':'preview@insighta.invalid','STAGING_RESEARCHER_PASSWORD_HASH':self.hash}

    def test_optional_seed_is_guarded_and_never_resets_existing_account(self):
        from app.models import User
        self.assertTrue(staging.seed_researcher_account(self.db,self.env))
        user=self.db.query(User).one()
        self.assertEqual(user.password,self.hash)
        self.assertEqual(user.pending_earnings,0)
        self.assertEqual(user.verification_status,'unverified')
        user.password='changed-by-owner';self.db.commit()
        self.assertFalse(staging.seed_researcher_account(self.db,self.env))
        self.assertEqual(user.password,'changed-by-owner')
        self.assertEqual(self.db.query(User).count(),1)
        with self.assertRaises(RuntimeError):
            staging.seed_researcher_account(self.db,{**self.env,'INSIGHTA_ENV':'production'})

    def test_optional_seed_absent_or_invalid(self):
        from app.models import User
        self.assertFalse(staging.seed_researcher_account(self.db,StagingTest().config()))
        for values in [{'STAGING_RESEARCHER_PASSWORD_HASH':'plaintext'}, {'STAGING_RESEARCHER_EMAIL':'real@example.com'}]:
            with self.assertRaises(RuntimeError):staging.seed_researcher_account(self.db,{**self.env,**values})
        self.assertEqual(self.db.query(User).count(),0)
