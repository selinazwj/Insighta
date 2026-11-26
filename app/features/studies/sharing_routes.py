"""Features / studies / sharing routes."""

from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import StreamingResponse
from io import BytesIO
from sqlalchemy.orm import Session
from typing import Optional
from urllib.parse import urlencode
import json
from app.bootstrap.runtime import templates
from app.database import get_db
from app.models import Response
from app.models import Survey
from app.models import User
from app.seo import CATEGORY_CONTENT
from app.seo import category_image
from app.seo import category_label
from app.seo import site_url as seo_site_url
from app.seo import study_seo


def recruitment_share_page(
    share_slug: str,
    request: Request,
    from_: Optional[str] = Query(None, alias="from"),
    user_id: str = Cookie(None),
    db: Session = Depends(get_db),
):
    from app.features.analytics.events import _record_user_event
    from app.features.auth.navigation import _participant_dashboard_url
    from app.features.bookings.service import _booking_calendar_path
    from app.features.bookings.service import _booking_google_calendar_links
    from app.features.bookings.slots import _parse_booking_slots
    from app.features.studies.fields import _is_online_interview_survey
    from app.features.studies.fields import _participant_study_type_label
    from app.features.studies.fields import _survey_external_start_url
    from app.features.studies.fields import _survey_has_external_start_link
    from app.features.studies.fields import _task_type_label
    from app.features.studies.fields import _uses_booking_flow

    survey = db.query(Survey).filter(Survey.share_slug == share_slug).first()
    if not survey:
        raise HTTPException(404, "Listing not found")
    if survey.status not in {"published", "closed"}:
        raise HTTPException(404, "Listing is not published")

    current_user = None
    user_response = None
    if user_id:
        try:
            current_user = db.query(User).filter(User.id == int(user_id)).first()
        except Exception:
            current_user = None
    if current_user:
        user_response = db.query(Response).filter(
            Response.survey_id == survey.id,
            Response.participant_id == current_user.id,
        ).first()

    availability_slots = []
    if getattr(survey, "availability_slots", None):
        try:
            parsed_slots = json.loads(survey.availability_slots)
            if isinstance(parsed_slots, list):
                availability_slots = parsed_slots
        except Exception:
            availability_slots = []

    display_reward = getattr(survey, "admin_display_reward_amount", None)
    if display_reward is None:
        display_reward = survey.reward_amount
    booked_slots = []
    for row in db.query(Response.booking_slot).filter(
            Response.survey_id == survey.id,
            Response.booking_slot.isnot(None),
            Response.participant_id != (current_user.id if current_user else 0),
        ).all():
        booked_slots.extend(_parse_booking_slots(row[0]))

    next_path = f"/r/{share_slug}"
    dashboard_path = _participant_dashboard_url(request)
    from_participant_app = from_ == "participant"
    from_my_studies = from_ == "my-studies"
    if from_my_studies:
        brand_action, brand_href, brand_label = "my-studies", "/my-studies", "Back to My Studies"
    else:
        brand_action = "back" if from_participant_app else ("dashboard" if current_user else "home")
        brand_href = dashboard_path if brand_action in {"back", "dashboard"} else "/"
        brand_label = "Back to dashboard" if from_participant_app else ("Dashboard" if current_user else "Insighta")
    _record_user_event(
        db,
        request,
        "listing_view",
        user=current_user,
        target_type="survey",
        target_id=survey.id,
        page_path=next_path,
        metadata={"study_title": survey.title, "source": from_ or "Share link", "surface": "recruitment_share"},
    )
    db.commit()
    normalized_category = (survey.category or "other").strip().lower()
    if normalized_category not in CATEGORY_CONTENT:
        normalized_category = "other"
    seo = study_seo(survey, next_path, indexable=survey.status == "published")
    response = templates.TemplateResponse("recruitment_share.html", {
        "request": request,
        "survey": survey,
        "seo": seo,
        "category_label": category_label(normalized_category),
        "category_path": f"/studies/{normalized_category}",
        "study_image": survey.image_url or category_image(normalized_category),
        "current_user": current_user,
        "user_response": user_response,
        "calendar_url": _booking_calendar_path(survey, user_response),
        "google_calendar_links": _booking_google_calendar_links(survey, user_response),
        "availability_slots": availability_slots,
        "booked_slots": booked_slots,
        "display_reward": display_reward,
        "share_url": seo_site_url(next_path),
        "login_url": f"/login?{urlencode({'role': 'participant', 'next': next_path})}",
        "register_url": f"/register?{urlencode({'role': 'participant', 'next': next_path})}",
        "dashboard_url": dashboard_path,
        "brand_action": brand_action,
        "brand_href": brand_href,
        "brand_label": brand_label,
        "is_builtin": survey.form_url == "__builtin__",
        "is_interview": _uses_booking_flow(getattr(survey, "task_type", None)),
        "is_online_interview": _is_online_interview_survey(survey),
        "has_external_start_link": _survey_has_external_start_link(survey),
        "external_start_url": _survey_external_start_url(survey),
        "external_start_redirect_url": (
            f"/surveys/{survey.id}/start-redirect?{urlencode({'next': _survey_external_start_url(survey)})}"
            if _survey_external_start_url(survey) else ""
        ),
        "task_type_label": _participant_study_type_label(survey) or _task_type_label(getattr(survey, "task_type", None)),
    })
    if survey.status != "published":
        response.headers["X-Robots-Tag"] = "noindex, follow, noarchive"
    return response


def recruitment_share_qr(
    share_slug: str,
    request: Request,
    download: bool = Query(False),
    db: Session = Depends(get_db),
):
    survey = db.query(Survey).filter(Survey.share_slug == share_slug).first()
    if not survey or survey.status not in {"published", "closed"}:
        raise HTTPException(404, "Listing not found")
    try:
        import qrcode
    except ImportError as exc:
        raise HTTPException(503, "QR generation is not installed") from exc

    share_url = seo_site_url(f"/r/{share_slug}")
    qr = qrcode.QRCode(version=None, box_size=10, border=3)
    qr.add_data(share_url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#184e77", back_color="white")
    output = BytesIO()
    image.save(output, format="PNG")
    output.seek(0)
    disposition = "attachment" if download else "inline"
    filename = f"insighta-{share_slug}-qr.png"
    return StreamingResponse(
        output,
        media_type="image/png",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


__all__ = ['recruitment_share_page', 'recruitment_share_qr']
