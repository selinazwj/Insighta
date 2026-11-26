"""HTTP route assembly.  The ordering is part of the existing URL contract."""

from fastapi.responses import HTMLResponse, PlainTextResponse
from app.google_calendar import register_calendar_routes
from app.features.auth.dependencies import get_current_user
from app.features.jobs import routes as jobs_routes
from app.features.auth import routes as auth_routes
from app.features.auth import oauth as auth_oauth
from app.features.public import routes as public_routes
from app.features.analytics import routes as analytics_routes
from app.features.accounts import onboarding as accounts_onboarding
from app.features.studies import sharing_routes as studies_sharing_routes
from app.features.publisher import routes as publisher_routes
from app.features.studies import lifecycle as studies_lifecycle
from app.features.dashboard import routes as dashboard_routes
from app.features.bookings import routes as bookings_routes
from app.features.participants import routes as participants_routes
from app.features.screening import routes as screening_routes
from app.features.participation import start as participation_start
from app.features.participation import completion as participation_completion
from app.features.notifications import routes as notifications_routes
from app.features.publishing import pages as publishing_pages
from app.features.publishing import routes as publishing_routes
from app.features.billing import routes as billing_routes
from app.features.verification import webhooks as verification_webhooks
from app.features.verification import routes as verification_routes
from app.features.verification import oauth as verification_oauth
from app.features.publishing import editing as publishing_editing
from app.features.accounts import profile as accounts_profile
from app.features.ai import authoring as ai_authoring
from app.features.support import routes as support_routes
from app.features.admin import routes as admin_routes
from app.features.admin import billing as admin_billing
from app.features.admin import jobs as admin_jobs
from app.features.admin import messaging as admin_messaging
from app.features.campaigns import routes as campaigns_routes
from app.features.admin import trust as admin_trust
from app.features.admin import publishing as admin_publishing
from app.features.admin import feedback as admin_feedback
from app.features.admin import listings as admin_listings
from app.features.admin import analytics as admin_analytics
from app.features.admin import discovery as admin_discovery
from app.features.admin import support as admin_support
from app.features.builder import routes as builder_routes
from app.features.responses import submission as responses_submission
from app.features.results import routes as results_routes
from app.features.quality import routes as quality_routes
from app.features.quality import reviews as quality_reviews
from app.features.quality import imports as quality_imports


def register_routes(app):
    """Attach feature handlers without changing route precedence or metadata."""
    app.api_route("/internal/jobs/run-due", methods=["GET", "POST"])(jobs_routes.internal_run_due_background_jobs)
    app.get("/auth/check-email")(auth_routes.check_email_exists)
    app.post("/auth/send-code")(auth_routes.send_auth_code)
    app.get("/auth/google")(auth_oauth.google_login)
    app.get("/auth/google/callback")(auth_oauth.google_callback)
    app.get("/auth/linkedin")(auth_oauth.linkedin_login)
    app.get("/auth/linkedin/callback")(auth_oauth.linkedin_callback)
    app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)(public_routes.robots_txt)
    app.get("/sitemap.xml", include_in_schema=False)(public_routes.sitemap_xml)
    app.get("/studies", response_class=HTMLResponse)(public_routes.public_studies)
    app.get("/studies/{category_slug}", response_class=HTMLResponse)(public_routes.public_studies_by_category)
    app.get("/how-it-works", response_class=HTMLResponse)(public_routes.how_it_works_page)
    app.get("/about", response_class=HTMLResponse)(public_routes.about_page)
    app.get("/privacy", response_class=HTMLResponse)(public_routes.privacy_page)
    app.get("/pricing", response_class=HTMLResponse)(public_routes.pricing_page)
    app.get("/terms", response_class=HTMLResponse)(public_routes.terms_page)
    app.get("/", response_class=HTMLResponse)(public_routes.index)
    app.get("/participant")(public_routes.participant_app_entry)
    app.get("/participant/login")(public_routes.participant_login_entry)
    app.get("/login", response_class=HTMLResponse)(auth_routes.login_page)
    app.post("/login")(auth_routes.login)
    app.post("/password-reset")(auth_routes.password_reset)
    app.get("/register", response_class=HTMLResponse)(auth_routes.show_register)
    app.post("/register", response_class=HTMLResponse)(auth_routes.do_register)
    register_calendar_routes(app, get_current_user)

    app.post("/api/events")(analytics_routes.record_client_event)
    app.get("/complete-profile", response_class=HTMLResponse)(accounts_onboarding.complete_profile_get)
    app.post("/complete-profile", response_class=HTMLResponse)(accounts_onboarding.complete_profile_post)
    app.get("/r/{share_slug}", response_class=HTMLResponse)(studies_sharing_routes.recruitment_share_page)
    app.get("/r/{share_slug}/qr.png")(studies_sharing_routes.recruitment_share_qr)
    app.get("/choice", response_class=HTMLResponse)(public_routes.choice)
    app.get("/guide", response_class=HTMLResponse)(public_routes.guide_page)
    app.get("/publisher", response_class=HTMLResponse)(publisher_routes.publisher_dashboard)
    app.get("/publisher/schedule", response_class=HTMLResponse)(publisher_routes.publisher_schedule)
    app.get("/publisher/study/{survey_id}", response_class=HTMLResponse)(publisher_routes.publisher_study)
    app.post("/publisher/delete/{survey_id}")(studies_lifecycle.delete_survey)
    app.get("/dashboard", response_class=HTMLResponse)(dashboard_routes.dashboard)
    app.get("/bookings/{response_id}/calendar.ics")(bookings_routes.download_booking_calendar)
    app.get("/my-studies", response_class=HTMLResponse)(participants_routes.participant_my_studies)
    app.get("/earnings", response_class=HTMLResponse)(participants_routes.participant_earnings)
    app.get("/dashboard/mobile", response_class=HTMLResponse)(dashboard_routes.dashboard_mobile)
    app.get("/api/dashboard/stats")(dashboard_routes.get_dashboard_stats)
    app.get("/api/surveys/{survey_id}/screening")(screening_routes.get_screening_configuration)
    app.get("/api/surveys/{survey_id}/trust-summary")(screening_routes.get_survey_trust_summary)
    app.post("/api/surveys/{survey_id}/screening/draft")(screening_routes.create_screening_draft)
    app.put("/api/surveys/{survey_id}/screening/draft")(screening_routes.update_screening_draft)
    app.post("/api/surveys/{survey_id}/screening/approve")(screening_routes.approve_screening_draft)
    app.get("/surveys/{survey_id}/screening", response_class=HTMLResponse)(screening_routes.participant_screening_page)
    app.post("/surveys/{survey_id}/screening", response_class=HTMLResponse)(screening_routes.submit_participant_screening)
    app.post("/surveys/{survey_id}/start")(participation_start.start_survey)
    app.get("/surveys/{survey_id}/start-redirect")(participation_start.start_survey_and_redirect)
    app.post("/surveys/{survey_id}/complete")(participation_completion.complete_survey)
    app.post("/api/surveys/{survey_id}/bookings/{response_id}/no-show")(participation_completion.mark_booking_no_show)
    app.post("/api/surveys/{survey_id}/bookings/{response_id}/complete")(participation_completion.confirm_live_interview_complete)
    app.post("/surveys/{survey_id}/modify")(participation_completion.modify_completed_survey)
    app.get("/api/notifications")(notifications_routes.get_notifications)
    app.post("/api/notifications/{notif_id}/accept")(notifications_routes.accept_notification)
    app.post("/api/notifications/{notif_id}/reject")(notifications_routes.reject_notification)
    app.get("/api/publisher/pending-responses")(notifications_routes.get_pending_responses)
    app.get("/publish", response_class=HTMLResponse)(publishing_pages.publish_page)
    app.post("/api/calculate-price")(publishing_routes.calculate_price)
    app.get("/publish_interview", response_class=HTMLResponse)(publishing_pages.publish_interview_page)
    app.post("/publish_interview")(publishing_routes.publish_interview)
    app.get("/payment/success", response_class=HTMLResponse)(billing_routes.payment_success)
    app.post("/webhook/stripe")(billing_routes.stripe_webhook)
    app.post("/webhook/didit")(verification_webhooks.didit_webhook)
    app.get("/connect/onboard")(billing_routes.connect_onboard)
    app.get("/logout")(auth_routes.logout)
    app.get("/connect/complete", response_class=HTMLResponse)(billing_routes.connect_complete)
    app.post("/api/withdraw")(billing_routes.withdraw)
    app.get('/api/withdrawal/status')(billing_routes.withdrawal_status)
    app.get("/participation-review", response_class=HTMLResponse)(verification_routes.participation_review_page)
    app.get("/api/verification/status")(verification_routes.verification_status)
    app.post("/api/verification/didit/session")(verification_routes.start_didit_session)
    app.get("/verify/didit/return", response_class=HTMLResponse)(verification_routes.didit_return_page)
    app.get("/api/participants/{participant_id}/verification")(verification_routes.participant_verification_status)
    app.get("/verify", response_class=HTMLResponse)(verification_routes.verify_page)
    app.post("/verify/send-code")(verification_routes.verify_send_code)
    app.post("/verify", response_class=HTMLResponse)(verification_routes.verify_user)
    app.get("/verify/linkedin")(verification_oauth.verify_linkedin_start)
    app.get("/verify/linkedin/callback")(verification_oauth.verify_linkedin_callback)
    app.post("/surveys/{survey_id}/publish")(studies_lifecycle.publish_existing_survey)
    app.post("/surveys/{survey_id}/close")(studies_lifecycle.close_existing_survey)
    app.post("/surveys/{survey_id}/reopen")(studies_lifecycle.reopen_closed_survey)
    app.get("/api/surveys/{survey_id}/settlement")(studies_lifecycle.study_settlement_status)
    app.post("/api/surveys/{survey_id}/settlement/retry")(studies_lifecycle.retry_study_settlement)
    app.get("/publisher/edit/{survey_id}")(publishing_editing.edit_survey_get)
    app.post("/publisher/edit/{survey_id}")(publishing_editing.edit_survey_post)
    app.get("/profile", response_class=HTMLResponse)(accounts_profile.profile_get)
    app.get("/profile/edit", response_class=HTMLResponse)(accounts_profile.profile_edit_get)
    app.post("/profile")(accounts_profile.profile_post)
    app.post("/api/ai-fill")(ai_authoring.ai_fill)
    app.post("/api/ai-generate-questions")(ai_authoring.ai_generate_questions)
    app.get("/feedback", response_class=HTMLResponse)(support_routes.feedback_page)
    app.post("/feedback")(support_routes.submit_feedback)
    app.get("/api/support/availability")(support_routes.support_availability)
    app.get("/api/support/thread")(support_routes.get_support_thread)
    app.get("/api/support/messages")(support_routes.get_support_messages)
    app.post("/api/support/messages")(support_routes.send_support_message)
    app.get("/admin", response_class=HTMLResponse)(admin_routes.admin_page)
    app.get("/admin/discovery", response_class=HTMLResponse)(admin_routes.admin_discovery_page)
    app.post("/admin/verify")(admin_routes.admin_verify)
    app.get('/admin/withdrawals')(admin_billing.admin_withdrawals)
    app.post('/admin/withdrawals/{withdrawal_id}/reconcile')(admin_billing.admin_reconcile_withdrawal)
    app.get("/admin/background-jobs")(admin_jobs.admin_background_jobs)
    app.post("/admin/background-jobs/run-due")(admin_jobs.admin_run_due_background_jobs)
    app.post("/admin/users/reward-reminder-email")(admin_messaging.admin_bulk_email)
    app.post("/admin/users/bulk-email")(admin_messaging.admin_bulk_email)
    app.api_route(     "/email/preferences/study-recommendations/unsubscribe",     methods=["GET", "POST"],     response_class=HTMLResponse, )(campaigns_routes.unsubscribe_study_recommendations)
    app.post("/internal/email-campaigns/{campaign_id}/send")(campaigns_routes.send_authorized_study_email_campaign)
    app.get("/admin/trust-reviews")(admin_trust.admin_trust_reviews)
    app.post("/admin/trust-reviews/{user_id}")(admin_trust.admin_resolve_trust_review)
    app.get("/admin/blocked-users")(admin_trust.admin_blocked_users)
    app.post("/admin/blocked-users")(admin_trust.admin_block_user_by_email)
    app.post("/admin/api/ai-fill")(admin_publishing.admin_ai_fill)
    app.get("/admin/publish", response_class=HTMLResponse)(admin_publishing.admin_publish_page)
    app.post("/admin/publish")(admin_publishing.admin_publish_survey)
    app.get("/admin/listings/{survey_id}/edit", response_class=HTMLResponse)(admin_publishing.admin_edit_listing_page)
    app.post("/admin/listings/{survey_id}/edit")(admin_publishing.admin_edit_listing_post)
    app.post("/admin/feedback/{feedback_id}/credit")(admin_feedback.grant_credit)
    app.post("/admin/feedback/{feedback_id}/reject")(admin_feedback.reject_feedback)
    app.get("/admin/listings")(admin_listings.admin_list_listings)
    app.get("/admin/analytics")(admin_analytics.admin_analytics)
    app.get("/admin/activity")(admin_analytics.admin_activity)
    app.post("/admin/listings/{survey_id}/delete")(admin_listings.admin_delete_listing)
    app.post("/admin/listings/delete-by-title")(admin_listings.admin_delete_listings_by_title)
    app.post("/admin/listings/reset-research-participation-demo")(admin_listings.admin_reset_research_participation_demo)
    app.get("/admin/feedbacks")(admin_feedback.list_feedbacks)
    app.post("/admin/discovery/find")(admin_discovery.admin_find_discovery_channels)
    app.get("/admin/support/threads")(admin_support.admin_support_threads)
    app.get("/admin/support/threads/{thread_id}/messages")(admin_support.admin_support_messages)
    app.post("/admin/support/threads/{thread_id}/messages")(admin_support.admin_send_support_message)
    app.post("/admin/support/threads/{thread_id}/status")(admin_support.admin_update_support_status)
    app.post("/surveys/create-builtin")(builder_routes.create_builtin_survey)
    app.post("/publish")(publishing_routes.publish_survey)
    app.post("/surveys/{survey_id}/update-info")(builder_routes.update_survey_info)
    app.post("/surveys/{survey_id}/questions")(builder_routes.add_question)
    app.get("/surveys/{survey_id}/questions")(builder_routes.get_questions)
    app.put("/surveys/{survey_id}/questions/{question_id}")(builder_routes.update_question)
    app.delete("/surveys/{survey_id}/questions/{question_id}")(builder_routes.delete_question)
    app.post("/surveys/{survey_id}/questions/reorder")(builder_routes.reorder_questions)
    app.get("/surveys/{survey_id}/builder", response_class=HTMLResponse)(builder_routes.survey_builder)
    app.get("/surveys/{survey_id}/take", response_class=HTMLResponse)(responses_submission.survey_take)
    app.get("/surveys/{survey_id}/results", response_class=HTMLResponse)(results_routes.survey_results_page)
    app.get("/api/surveys/{survey_id}/results")(results_routes.get_survey_results)
    app.post("/surveys/{survey_id}/analyze")(results_routes.analyze_survey)
    app.post("/surveys/{survey_id}/submit")(responses_submission.submit_answers)
    app.get("/api/surveys/{survey_id}/quality-results")(quality_routes.get_survey_quality_results)
    app.get("/api/quality-checks/{check_id}")(quality_routes.get_quality_check_detail)
    app.post("/api/quality-checks/{check_id}/review")(quality_routes.review_quality_check)
    app.post("/api/quality-checks/{check_id}/ai-review")(quality_reviews.queue_single_quality_ai_review)
    app.post("/api/surveys/{survey_id}/quality/ai-review")(quality_reviews.queue_flagged_quality_ai_review)
    app.post("/api/quality-checks/{check_id}/blacklist")(quality_reviews.blacklist_from_quality_check)
    app.post("/api/surveys/{survey_id}/quality/auto-approve")(quality_reviews.auto_approve_quality_by_score)
    app.post("/api/surveys/{survey_id}/quality/import-excel")(quality_imports.import_excel_for_quality_check)
