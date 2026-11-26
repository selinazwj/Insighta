"""Features / messaging / delivery."""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from typing import Optional
import html
import httpx
import re
import smtplib
from app.bootstrap.settings import BASE_URL
from app.bootstrap.settings import EMAIL_ADDRESS
from app.bootstrap.settings import EMAIL_FROM
from app.bootstrap.settings import EMAIL_PASSWORD
from app.bootstrap.settings import EMAIL_PROVIDER
from app.bootstrap.settings import EMAIL_REPLY_TO
from app.bootstrap.settings import RESEND_API_KEY


def _email_plain_text(body: str) -> str:
    text_body = re.sub(r"(?i)<br\s*/?>", "\n", body or "")
    text_body = re.sub(r"(?i)</p\s*>", "\n\n", text_body)
    text_body = re.sub(r"<[^>]+>", "", text_body)
    return html.unescape(text_body).strip()


def _email_brand_header() -> str:
    logo_url = f"{BASE_URL.rstrip('/')}/static/favicon.png"
    return f"""
    <div style="max-width:620px;margin:0 auto;padding:24px 22px 0;font-family:Arial,sans-serif;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border-bottom:1px solid #eeece6;padding-bottom:16px;">
        <tr>
          <td width="48" style="padding:0 10px 16px 0;vertical-align:middle;">
            <img src="{html.escape(logo_url, quote=True)}" alt="Insighta" width="40" height="40" style="display:block;width:40px;height:40px;object-fit:contain;border:0;">
          </td>
          <td style="padding:0 0 16px 0;vertical-align:middle;font-size:22px;font-weight:700;letter-spacing:-0.3px;color:#184e77;line-height:1;font-family:Arial,sans-serif;">Insighta</td>
        </tr>
      </table>
    </div>
    """


def _with_email_brand_header(body: str) -> str:
    if "data-insighta-email-brand" in (body or ""):
        return body
    return f"""
    <div data-insighta-email-brand="1" style="margin:0;padding:0;background:#ffffff;">
      {_email_brand_header()}
      {body or ""}
    </div>
    """


def send_email(to: str, subject: str, body: str, text_body: Optional[str] = None) -> tuple[bool, Optional[str]]:
    plain_text = text_body or _email_plain_text(body)
    html_body = _with_email_brand_header(body)
    if EMAIL_PROVIDER == "resend":
        if not RESEND_API_KEY or not EMAIL_FROM:
            return False, "Resend email configuration is incomplete"
        payload = {
            "from": EMAIL_FROM,
            "to": [to],
            "subject": subject,
            "html": html_body,
            "text": plain_text,
        }
        if EMAIL_REPLY_TO:
            payload["reply_to"] = EMAIL_REPLY_TO
        try:
            response = httpx.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                    "User-Agent": "Insighta/1.0",
                },
                json=payload,
                timeout=8.0,
            )
            if response.is_success:
                return True, None
            error = f"Resend returned {response.status_code}: {response.text[:500]}"
            print(f"Email error: {error}")
            return False, error
        except Exception as exc:
            error = f"Resend request failed: {exc}"
            print(f"Email error: {error}")
            return False, error

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        return False, "Gmail email configuration is incomplete"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to
        if EMAIL_REPLY_TO:
            msg["Reply-To"] = EMAIL_REPLY_TO
        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to, msg.as_string())
        return True, None
    except Exception as exc:
        error = f"Gmail SMTP failed: {exc}"
        print(f"Email error: {error}")
        return False, error


def send_email_batch(
    messages: list[dict[str, Any]],
    *,
    idempotency_key: str,
) -> tuple[bool, list[str], Optional[str]]:
    """Send up to 100 messages with Resend's idempotent batch endpoint."""
    if EMAIL_PROVIDER != "resend":
        return False, [], "Batch email campaigns require EMAIL_PROVIDER=resend"
    if not RESEND_API_KEY or not EMAIL_FROM:
        return False, [], "Resend email configuration is incomplete"
    if not messages:
        return True, [], None
    if len(messages) > 100:
        return False, [], "Resend batches are limited to 100 messages"
    try:
        response = httpx.post(
            "https://api.resend.com/emails/batch",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "Insighta/1.0",
                "Idempotency-Key": idempotency_key[:256],
            },
            json=messages,
            timeout=20.0,
        )
        if response.is_success:
            payload = response.json()
            ids = [
                str(item.get("id"))
                for item in (payload.get("data") or [])
                if isinstance(item, dict) and item.get("id")
            ]
            if len(ids) != len(messages):
                return False, ids, "Resend returned an incomplete batch response"
            return True, ids, None
        error = f"Resend batch returned {response.status_code}: {response.text[:500]}"
        print(f"Email batch error: {error}")
        return False, [], error
    except Exception as exc:
        error = f"Resend batch request failed: {exc}"
        print(f"Email batch error: {error}")
        return False, [], error


__all__ = ['_email_plain_text', '_email_brand_header', '_with_email_brand_header', 'send_email', 'send_email_batch']
