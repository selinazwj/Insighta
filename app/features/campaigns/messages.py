"""Features / campaigns / messages."""

from typing import Any
from urllib.parse import urlencode
import hashlib
import hmac
import html
import os
import re
from app.bootstrap.settings import BASE_URL
from app.bootstrap.settings import EMAIL_FROM
from app.bootstrap.settings import EMAIL_REPLY_TO
from app.models import Survey
from app.models import User


STUDY_RECOMMENDATION_CAMPAIGN_EVENT = "study_recommendation_campaign_authorized"


STUDY_RECOMMENDATION_SENT_EVENT = "study_recommendation_email_sent"


STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT = "study_recommendation_unsubscribed"


def _study_email_unsubscribe_token(user_id: int) -> str:
    signing_key = (
        os.environ.get("ADMIN_KEY")
        or os.environ.get("VERIFICATION_EVIDENCE_SECRET")
        or ""
    ).encode("utf-8")
    if not signing_key:
        return ""
    return hmac.new(
        signing_key,
        f"study-recommendations:{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _study_email_unsubscribe_url(user_id: int) -> str:
    return (
        f"{BASE_URL.rstrip('/')}/email/preferences/study-recommendations/unsubscribe?"
        f"{urlencode({'user_id': user_id, 'token': _study_email_unsubscribe_token(user_id)})}"
    )


def _study_recommendation_message(user: User, surveys: list[Survey]) -> dict[str, Any]:
    from app.features.messaging.delivery import _with_email_brand_header

    first_name = html.escape((user.first_name or user.username or "").strip())
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    cards = []
    text_sections = []
    for survey in surveys:
        title = html.escape((survey.title or "").strip())
        description = html.escape((survey.description or "").strip())
        if len(description) > 430:
            description = description[:427].rstrip() + "..."
        display_reward = survey.admin_display_reward_amount
        if display_reward is None:
            display_reward = survey.reward_amount
        reward_label = f"Up to ${float(display_reward or 0):,.0f}"
        study_url = f"{BASE_URL.rstrip('/')}/r/{survey.share_slug}"
        cards.append(f"""
        <div style="border:1px solid #e0ddd3;border-radius:14px;padding:20px;margin:18px 0;">
          <div style="font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#2d6a4f;margin-bottom:8px;">
            {html.escape(reward_label)} · About {int(survey.estimated_time or 0)} minutes
          </div>
          <h2 style="font-size:20px;line-height:1.3;margin:0 0 10px;color:#1a1a18;">{title}</h2>
          <p style="font-size:14px;line-height:1.6;color:#55554f;margin:0 0 16px;">{description}</p>
          <a href="{html.escape(study_url, quote=True)}"
             style="display:inline-block;background:#184e77;color:#fff;text-decoration:none;font-weight:700;padding:11px 18px;border-radius:9px;">
            View study details
          </a>
        </div>
        """)
        text_sections.append(
            f"{survey.title.strip()}\n"
            f"{reward_label} · About {int(survey.estimated_time or 0)} minutes\n"
            f"{survey.description.strip()}\n"
            f"{study_url}"
        )
    unsubscribe_url = _study_email_unsubscribe_url(user.id)
    body = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:0 auto;padding:26px 22px 34px;color:#1a1a18;">
      <p style="font-size:16px;line-height:1.6;margin-top:0;">{greeting}</p>
      <p style="font-size:16px;line-height:1.6;">
        Two paid Boston University research opportunities are currently recruiting on Insighta.
        Review the eligibility details below and apply if either study is a fit.
      </p>
      {''.join(cards)}
      <p style="font-size:13px;line-height:1.55;color:#6f6f68;margin-top:24px;">
        Participation is voluntary. Review each study page for eligibility, location, compensation,
        and researcher contact details.
      </p>
      <p style="font-size:12px;line-height:1.5;color:#8a8a82;margin-top:22px;">
        You received this email because you have an Insighta account.
        <a href="{html.escape(unsubscribe_url, quote=True)}" style="color:#6f6f68;">Unsubscribe from study recommendations</a>.
      </p>
    </div>
    """
    text_body = (
        f"{html.unescape(re.sub('<[^>]+>', '', greeting))}\n\n"
        "Two paid Boston University research opportunities are currently recruiting on Insighta.\n\n"
        + "\n\n".join(text_sections)
        + "\n\nParticipation is voluntary. Review each study page for full details."
        + f"\n\nUnsubscribe from study recommendations: {unsubscribe_url}"
    )
    message: dict[str, Any] = {
        "from": EMAIL_FROM,
        "to": [user.email],
        "subject": "Two paid Boston University research opportunities",
        "html": _with_email_brand_header(body),
        "text": text_body,
        "headers": {
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
        "tags": [{"name": "campaign", "value": "bu-study-recommendations"}],
    }
    if EMAIL_REPLY_TO:
        message["reply_to"] = EMAIL_REPLY_TO
    return message


__all__ = ['STUDY_RECOMMENDATION_CAMPAIGN_EVENT', 'STUDY_RECOMMENDATION_SENT_EVENT', 'STUDY_RECOMMENDATION_UNSUBSCRIBED_EVENT', '_study_email_unsubscribe_token', '_study_email_unsubscribe_url', '_study_recommendation_message']
