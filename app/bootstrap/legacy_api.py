"""Legacy import bindings; new code imports feature modules directly."""

from app.compat import install_compatibility_exports
from app.bootstrap import legacy_imports as bootstrap_legacy_imports
from app.bootstrap import runtime as bootstrap_runtime
from app.bootstrap import settings as bootstrap_settings
from app.web import middleware as web_middleware
from app.bootstrap import schema as bootstrap_schema
from app.features.auth import policy as features_auth_policy
from app.features.auth import oauth_settings as features_auth_oauth_settings
from app.features.messaging import delivery as features_messaging_delivery
from app.features.messaging import account_messages as features_messaging_account_messages
from app.features.messaging import study_messages as features_messaging_study_messages
from app.features.jobs import followups as features_jobs_followups
from app.features.jobs import handlers as features_jobs_handlers
from app.features.bookings import service as features_bookings_service
from app.features.jobs import service as features_jobs_service
from app.features.jobs import routes as features_jobs_routes
from app.features.trust import retention as features_trust_retention
from app.bootstrap import lifecycle as bootstrap_lifecycle
from app.features.analytics import events as features_analytics_events
from app.features.trust import risk as features_trust_risk
from app.features.quality import access as features_quality_access
from app.features.matching import eligibility as features_matching_eligibility
from app.features.bookings import slots as features_bookings_slots
from app.features.studies import fields as features_studies_fields
from app.features.quality import serialization as features_quality_serialization
from app.features.auth import navigation as features_auth_navigation
from app.features.auth import cookies as features_auth_cookies
from app.features.studies import sharing as features_studies_sharing
from app.features.auth import codes as features_auth_codes
from app.features.auth import referrals as features_auth_referrals
from app.features.messaging import verification_messages as features_messaging_verification_messages
from app.features.auth import routes as features_auth_routes
from app.features.auth import oauth as features_auth_oauth
from app.features.public import catalog as features_public_catalog
from app.features.public import routes as features_public_routes
from app.features.auth import dependencies as features_auth_dependencies
from app.features.analytics import routes as features_analytics_routes
from app.features.accounts import onboarding as features_accounts_onboarding
from app.features.studies import sharing_routes as features_studies_sharing_routes
from app.features.publisher import routes as features_publisher_routes
from app.features.studies import lifecycle as features_studies_lifecycle
from app.features.dashboard import queries as features_dashboard_queries
from app.features.dashboard import presentation as features_dashboard_presentation
from app.features.dashboard import routes as features_dashboard_routes
from app.features.bookings import routes as features_bookings_routes
from app.features.participants import routes as features_participants_routes
from app.features.screening import service as features_screening_service
from app.features.screening import routes as features_screening_routes
from app.features.participation import start as features_participation_start
from app.features.participation import completion as features_participation_completion
from app.features.notifications import routes as features_notifications_routes
from app.features.publishing import pages as features_publishing_pages
from app.features.publishing import routes as features_publishing_routes
from app.features.billing import routes as features_billing_routes
from app.features.verification import webhooks as features_verification_webhooks
from app.features.verification import routes as features_verification_routes
from app.features.verification import service as features_verification_service
from app.features.verification import oauth as features_verification_oauth
from app.features.publishing import editing as features_publishing_editing
from app.features.accounts import profile as features_accounts_profile
from app.features.ai import authoring as features_ai_authoring
from app.features.support import routes as features_support_routes
from app.features.admin import access as features_admin_access
from app.features.admin import routes as features_admin_routes
from app.features.admin import billing as features_admin_billing
from app.features.admin import jobs as features_admin_jobs
from app.features.admin import messaging as features_admin_messaging
from app.features.campaigns import messages as features_campaigns_messages
from app.features.campaigns import routes as features_campaigns_routes
from app.features.admin import trust as features_admin_trust
from app.features.admin import publishing as features_admin_publishing
from app.features.admin import feedback as features_admin_feedback
from app.features.admin import listings as features_admin_listings
from app.features.admin import analytics as features_admin_analytics
from app.features.admin import discovery as features_admin_discovery
from app.features.admin import support as features_admin_support
from app.features.builder import routes as features_builder_routes
from app.features.responses import submission as features_responses_submission
from app.features.results import routes as features_results_routes
from app.features.quality import routes as features_quality_routes
from app.features.quality import reviews as features_quality_reviews
from app.features.quality import settings as features_quality_settings
from app.features.quality import imports as features_quality_imports


IMPLEMENTATION_MODULES = (
    bootstrap_legacy_imports,
    bootstrap_runtime,
    bootstrap_settings,
    web_middleware,
    bootstrap_schema,
    features_auth_policy,
    features_auth_oauth_settings,
    features_messaging_delivery,
    features_messaging_account_messages,
    features_messaging_study_messages,
    features_jobs_followups,
    features_jobs_handlers,
    features_bookings_service,
    features_jobs_service,
    features_jobs_routes,
    features_trust_retention,
    bootstrap_lifecycle,
    features_analytics_events,
    features_trust_risk,
    features_quality_access,
    features_matching_eligibility,
    features_bookings_slots,
    features_studies_fields,
    features_quality_serialization,
    features_auth_navigation,
    features_auth_cookies,
    features_studies_sharing,
    features_auth_codes,
    features_auth_referrals,
    features_messaging_verification_messages,
    features_auth_routes,
    features_auth_oauth,
    features_public_catalog,
    features_public_routes,
    features_auth_dependencies,
    features_analytics_routes,
    features_accounts_onboarding,
    features_studies_sharing_routes,
    features_publisher_routes,
    features_studies_lifecycle,
    features_dashboard_queries,
    features_dashboard_presentation,
    features_dashboard_routes,
    features_bookings_routes,
    features_participants_routes,
    features_screening_service,
    features_screening_routes,
    features_participation_start,
    features_participation_completion,
    features_notifications_routes,
    features_publishing_pages,
    features_publishing_routes,
    features_billing_routes,
    features_verification_webhooks,
    features_verification_routes,
    features_verification_service,
    features_verification_oauth,
    features_publishing_editing,
    features_accounts_profile,
    features_ai_authoring,
    features_support_routes,
    features_admin_access,
    features_admin_routes,
    features_admin_billing,
    features_admin_jobs,
    features_admin_messaging,
    features_campaigns_messages,
    features_campaigns_routes,
    features_admin_trust,
    features_admin_publishing,
    features_admin_feedback,
    features_admin_listings,
    features_admin_analytics,
    features_admin_discovery,
    features_admin_support,
    features_builder_routes,
    features_responses_submission,
    features_results_routes,
    features_quality_routes,
    features_quality_reviews,
    features_quality_settings,
    features_quality_imports,
)


def install_legacy_api(module_name):
    """Retain original names, including private helpers and mutable patch targets."""
    bindings = {
        name: (module, name)
        for module in IMPLEMENTATION_MODULES
        for name in module.__all__
    }
    install_compatibility_exports(module_name, bindings, IMPLEMENTATION_MODULES)
