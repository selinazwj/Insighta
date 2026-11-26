"""Features / messaging / account messages."""

from typing import Optional
import html
import re
from app.bootstrap.settings import BASE_URL
from app.bootstrap.settings import SUPPORT_ALERT_EMAIL
from app.models import SupportMessage
from app.models import SupportThread
from app.models import User


def send_support_alert_email(user: User, thread: SupportThread, message: SupportMessage):
    from app.features.messaging.delivery import send_email

    if not SUPPORT_ALERT_EMAIL:
        return
    user_label = html.escape(user.email or user.username or f"User #{user.id}")
    user_name = html.escape(user.username or "")
    subject_user = (user.email or "a user").replace("\r", " ").replace("\n", " ")
    message_body = html.escape(message.body or "").replace("\n", "<br>")
    admin_url = f"{BASE_URL.rstrip('/')}/admin#support"
    send_email(
        to=SUPPORT_ALERT_EMAIL,
        subject=f"[Insighta Support] New message from {subject_user}",
        body=f"""
        <div style="font-family: sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 24px; color: #1a1a18;">
          <h2 style="font-size: 22px; margin-bottom: 8px;">New support message</h2>
          <p style="color: #8a8a82; margin-bottom: 22px;">A user sent a message through Insighta support.</p>
          <div style="background: #f3f1ea; border-radius: 10px; padding: 18px 20px; margin-bottom: 22px;">
            <div style="font-size: 12px; color: #8a8a82; margin-bottom: 4px;">User</div>
            <div style="font-size: 15px; font-weight: 600;">{user_label}</div>
            {f'<div style="font-size: 13px; color: #8a8a82; margin-top: 4px;">{user_name}</div>' if user_name else ''}
            <div style="font-size: 12px; color: #8a8a82; margin-top: 14px; margin-bottom: 4px;">Thread</div>
            <div style="font-size: 15px;">#{thread.id}</div>
          </div>
          <div style="border: 1px solid #e0ddd3; border-radius: 10px; padding: 16px 18px; margin-bottom: 22px; line-height: 1.55;">
            {message_body}
          </div>
          <a href="{admin_url}" style="display: inline-block; padding: 11px 18px; background: #2d6a4f; color: white; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 14px;">
            Open support inbox
          </a>
        </div>
        """,
    )


def _welcome_email_role(role: Optional[str], return_to: Optional[str], participant_app: Optional[str]) -> str:
    normalized = (role or "").strip().lower()
    if normalized in {"participant", "researcher"}:
        return normalized
    safe_return = return_to or ""
    if participant_app == "1" or safe_return.startswith(("/dashboard", "/r/", "/surveys/")):
        return "participant"
    return "researcher"


def _send_welcome_followup_email(user: User, role: str) -> tuple[bool, Optional[str]]:
    from app.features.messaging.delivery import send_email

    first_name = (getattr(user, "first_name", None) or getattr(user, "username", None) or "there").strip()
    escaped_first = html.escape(first_name)
    role = "researcher" if role == "researcher" else "participant"

    if role == "researcher":
        subject = "Welcome to Insighta - let's get your study staffed"
        text_body = f"""Hi {first_name},

Welcome to Insighta, and thanks for signing up.

We help researchers reach the right participants, especially for studies targeting niche or hard-to-reach populations, where general panels tend to fall short. You focus on the research; we handle finding and matching qualified participants through channels they already trust.

Here's how to get started:
1. Tell us about your study: your target population, criteria, sample size, and timeline.
2. We'll map where those participants are and bring you a first batch to review.
3. You only move forward with the ones who fit.

The fastest way to begin is a quick 15-minute call so we can understand your study and show you exactly how we'd reach your participants. You can grab a time here: https://calendly.com/yuhan-wei-2001-oh-h/30min
Or just reply to this email with a few details about your study, and we'll take it from there.

Looking forward to helping you recruit,
The Insighta Team"""
        body = f"""
        <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.65;">
          <h2 style="margin:0 0 18px;color:#184e77;">Welcome to Insighta</h2>
          <p>Hi {escaped_first},</p>
          <p>Welcome to Insighta, and thanks for signing up.</p>
          <p>We help researchers reach the right participants, especially for studies targeting niche or hard-to-reach populations, where general panels tend to fall short. You focus on the research; we handle finding and matching qualified participants through channels they already trust.</p>
          <p><strong>Here's how to get started:</strong></p>
          <ol>
            <li>Tell us about your study: your target population, criteria, sample size, and timeline.</li>
            <li>We'll map where those participants are and bring you a first batch to review.</li>
            <li>You only move forward with the ones who fit.</li>
          </ol>
          <p>The fastest way to begin is a quick 15-minute call so we can understand your study and show you exactly how we'd reach your participants. You can grab a time here: <a href="https://calendly.com/yuhan-wei-2001-oh-h/30min">https://calendly.com/yuhan-wei-2001-oh-h/30min</a></p>
          <p>Or just reply to this email with a few details about your study, and we'll take it from there.</p>
          <p>Looking forward to helping you recruit,<br>The Insighta Team</p>
        </div>
        """
        return send_email(user.email, subject, body, text_body=text_body)

    subject = "Welcome to Insighta! Here's what happens next"
    text_body = f"""Hi {first_name},

Thanks for joining Insighta. You're all set.

Here's what happens now: when a study matches your profile, we'll email you with the details: what it's about, what's involved, the time commitment, and any compensation. You're never automatically signed up for anything. You decide, study by study, whether it's a fit for you.

A few things we want you to know upfront:
* We only work with studies that have proper ethical (IRB) approval.
* We'll never share your information without your consent.

Here's some research that you can start off with:
BU CARD STOP study for Social Anxiety: https://insightaco.org/r/Ta29WANH7J
Paid research study on time and memory: https://insightaco.org/r/940DVmCuNO

If you ever have questions, feel free to use our chat box and connect to our team member directly.
Now, please explore around!

Warmly,
The Insighta Team"""
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.65;">
      <h2 style="margin:0 0 18px;color:#168aad;">Welcome to Insighta!</h2>
      <p>Hi {escaped_first},</p>
      <p>Thanks for joining Insighta. You're all set.</p>
      <p>Here's what happens now: when a study matches your profile, we'll email you with the details: what it's about, what's involved, the time commitment, and any compensation. You're never automatically signed up for anything. You decide, study by study, whether it's a fit for you.</p>
      <p><strong>A few things we want you to know upfront:</strong></p>
      <ul>
        <li>We only work with studies that have proper ethical (IRB) approval.</li>
        <li>We'll never share your information without your consent.</li>
      </ul>
      <p>If you ever have questions, feel free to use our chat box and connect to our team member directly.</p>
      <p>Now, please explore around!</p>
      <p>Warmly,<br>The Insighta Team</p>
    </div>
    """
    return send_email(user.email, subject, body, text_body=text_body)


def _send_reward_setup_reminder_email(user: User) -> tuple[bool, Optional[str]]:
    from app.features.messaging.delivery import send_email

    if not user.email:
        return False, "Missing user email"
    first_name = (user.first_name or user.username or "there").strip()
    escaped_first = html.escape(first_name)
    profile_url = f"{BASE_URL.rstrip('/')}/profile"
    stripe_url = f"{BASE_URL.rstrip('/')}/connect/onboard"
    subject = "Complete your Insighta profile and payout setup"
    text_body = f"""Hi {first_name},

Quick reminder from Insighta: to make sure rewards can be matched, reviewed, and paid correctly, please complete your Insighta profile.

If you participate in paid studies, you will also need to connect Stripe before rewards can be paid out. This helps us send approved rewards securely.

What to do next:
1. Complete or update your profile: {profile_url}
2. If you are participating in paid studies, connect Stripe payouts: {stripe_url}
3. Keep your contact information current so we can reach you about study matches, bookings, and rewards.

Rewards are not issued automatically. Insighta reviews study participation and eligibility before releasing approved rewards.

Warmly,
The Insighta Team"""
    body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.65;">
      <h2 style="margin:0 0 18px;color:#168aad;">Complete your profile and payout setup</h2>
      <p>Hi {escaped_first},</p>
      <p>Quick reminder from Insighta: to make sure rewards can be matched, reviewed, and paid correctly, please complete your Insighta profile.</p>
      <p>If you participate in paid studies, you will also need to connect Stripe before rewards can be paid out. This helps us send approved rewards securely.</p>
      <div style="background:#f6fbf8;border:1px solid rgba(24,78,119,0.12);border-radius:10px;padding:16px 18px;margin:18px 0;">
        <p style="margin:0 0 8px;font-weight:700;color:#184e77;">What to do next</p>
        <ol style="margin:0;padding-left:20px;">
          <li>Complete or update your profile.</li>
          <li>If you are participating in paid studies, connect Stripe payouts.</li>
          <li>Keep your contact information current so we can reach you about study matches, bookings, and rewards.</li>
        </ol>
      </div>
      <p>
        <a href="{profile_url}" style="display:inline-block;background:#168aad;color:#fff;text-decoration:none;border-radius:8px;padding:11px 16px;font-weight:700;margin-right:8px;">Complete profile</a>
        <a href="{stripe_url}" style="display:inline-block;background:#184e77;color:#fff;text-decoration:none;border-radius:8px;padding:11px 16px;font-weight:700;">Connect Stripe</a>
      </p>
      <p style="font-size:13px;color:#5a8297;">Rewards are not issued automatically. Insighta reviews study participation and eligibility before releasing approved rewards.</p>
      <p>Warmly,<br>The Insighta Team</p>
    </div>
    """
    return send_email(user.email, subject, body, text_body=text_body)


def _admin_bulk_email_line_html(line: str) -> str:
    escaped = html.escape(line.strip())
    return re.sub(
        r"(https?://[^\s<]+)",
        r'<a href="\1" style="color:#168aad;font-weight:700;">\1</a>',
        escaped,
    )


def _admin_bulk_email_body(message: str) -> str:
    paragraphs = []
    for block in re.split(r"\n\s*\n", (message or "").strip()):
        lines = [_admin_bulk_email_line_html(line) for line in block.splitlines() if line.strip()]
        if lines:
            paragraphs.append(f"<p>{'<br>'.join(lines)}</p>")
    body_html = "\n".join(paragraphs) or "<p></p>"
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 620px; margin: 0 auto; padding: 28px 22px; color: #1a1a18; line-height: 1.65;">
      {body_html}
      <p style="margin-top:24px;">Warmly,<br>The Insighta Team</p>
    </div>
    """


def _send_admin_bulk_email(user: User, subject: str, message: str) -> tuple[bool, Optional[str]]:
    from app.features.messaging.delivery import send_email

    if not user.email:
        return False, "Missing user email"
    return send_email(user.email, subject, _admin_bulk_email_body(message), text_body=message)


__all__ = ['send_support_alert_email', '_welcome_email_role', '_send_welcome_followup_email', '_send_reward_setup_reminder_email', '_admin_bulk_email_line_html', '_admin_bulk_email_body', '_send_admin_bulk_email']
