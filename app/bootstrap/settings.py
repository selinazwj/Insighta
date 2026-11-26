"""Bootstrap / settings."""

from pathlib import Path
import os


# Load the same root .env before importing application services.
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


BASE_DIR = Path(__file__).resolve().parents[2] / "api"


QUALITY_IMPORT_UPLOAD_DIR = Path(os.environ.get(
    "QUALITY_IMPORT_UPLOAD_DIR",
    str(BASE_DIR.parent / "private_uploads" / "quality_imports"),
))


STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")


STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")


EMAIL_ADDRESS = os.environ.get("EMAIL_ADDRESS")


EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")


EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "gmail").strip().lower()


RESEND_API_KEY = os.environ.get("RESEND_API_KEY")


EMAIL_FROM = os.environ.get("EMAIL_FROM") or EMAIL_ADDRESS


EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")


SUPPORT_ALERT_EMAIL = os.environ.get("SUPPORT_ALERT_EMAIL", "vfsa@bu.edu")


BACKGROUND_JOB_ALERT_EMAIL = os.environ.get("BACKGROUND_JOB_ALERT_EMAIL", SUPPORT_ALERT_EMAIL)


VERIFICATION_CODE_EXPIRE_MINUTES = 10


VERIFICATION_CODE_RESEND_COOLDOWN_SECONDS = 60


VERIFICATION_CODE_MAX_PER_HOUR = 5


SURVEY_START_FOLLOWUP_DELAY_MINUTES = int(os.environ.get("SURVEY_START_FOLLOWUP_DELAY_MINUTES", "5"))


SURVEY_START_FOLLOWUP_POLL_SECONDS = int(os.environ.get("SURVEY_START_FOLLOWUP_POLL_SECONDS", "60"))


ENABLE_LEGACY_FOLLOWUP_WORKER = os.environ.get(
    "INSIGHTA_ENABLE_LEGACY_FOLLOWUP_WORKER", "0"
).strip().lower() in {"1", "true", "yes", "on"}


BASE_URL = os.environ.get("BASE_URL", "https://insightaco.org")


GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")


GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


LINKEDIN_CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID")


LINKEDIN_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET")


LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"


LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"


LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


__all__ = ['BASE_DIR', 'QUALITY_IMPORT_UPLOAD_DIR', 'STRIPE_PUBLISHABLE_KEY', 'STRIPE_WEBHOOK_SECRET', 'EMAIL_ADDRESS', 'EMAIL_PASSWORD', 'EMAIL_PROVIDER', 'RESEND_API_KEY', 'EMAIL_FROM', 'EMAIL_REPLY_TO', 'SUPPORT_ALERT_EMAIL', 'BACKGROUND_JOB_ALERT_EMAIL', 'VERIFICATION_CODE_EXPIRE_MINUTES', 'VERIFICATION_CODE_RESEND_COOLDOWN_SECONDS', 'VERIFICATION_CODE_MAX_PER_HOUR', 'SURVEY_START_FOLLOWUP_DELAY_MINUTES', 'SURVEY_START_FOLLOWUP_POLL_SECONDS', 'ENABLE_LEGACY_FOLLOWUP_WORKER', 'BASE_URL', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_AUTH_URL', 'GOOGLE_TOKEN_URL', 'GOOGLE_USERINFO_URL', 'LINKEDIN_CLIENT_ID', 'LINKEDIN_CLIENT_SECRET', 'LINKEDIN_AUTH_URL', 'LINKEDIN_TOKEN_URL', 'LINKEDIN_USERINFO_URL']
