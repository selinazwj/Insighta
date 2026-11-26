"""Features / messaging / verification messages."""

from typing import Optional
from app.bootstrap.settings import VERIFICATION_CODE_EXPIRE_MINUTES


def _send_verification_email(email: str, purpose: str, code: str) -> tuple[bool, Optional[str]]:
    from app.features.messaging.delivery import send_email

    if purpose == "register":
        subject = "Insighta registration verification code"
        title = "Verify your email"
        body_text = "Use the following verification code to finish creating your Insighta account."
    elif purpose == "verify_identity":
        # ive added this for verification — copy for the identity verification OTP
        subject = "Insighta identity verification code"
        title = "Verify your identity"
        body_text = "Use the following verification code to verify your identity and claim your payout."
    else:
        subject = "Insighta password reset verification code"
        title = "Reset your password"
        body_text = "Use the following verification code to reset your Insighta password."

    body = f'''
    <div style="font-family: DM Sans, Arial, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 24px; color: #1a1a18;">
      <div style="margin-bottom: 20px;">
        <div style="font-family: Georgia, serif; font-size: 28px; color: #2d6a4f; margin-bottom: 8px;">Insighta</div>
        <div style="font-size: 13px; color: #8a8a82;">Secure verification</div>
      </div>
      <div style="background: #f3f1ea; border: 1px solid #e0ddd3; border-radius: 16px; padding: 24px;">
        <h2 style="font-family: Georgia, serif; font-size: 26px; margin: 0 0 10px; color: #1a1a18;">{title}</h2>
        <p style="font-size: 15px; line-height: 1.7; color: #4a4a44; margin: 0 0 20px;">{body_text}</p>
        <div style="font-size: 13px; color: #8a8a82; margin-bottom: 8px;">Verification code</div>
        <div style="font-size: 36px; font-weight: 700; letter-spacing: 8px; color: #2d6a4f; background: white; border: 1px solid #d6d0c1; border-radius: 12px; padding: 18px 20px; text-align: center;">
          {code}
        </div>
        <p style="font-size: 13px; line-height: 1.7; color: #8a8a82; margin: 20px 0 0;">
          This code expires in {VERIFICATION_CODE_EXPIRE_MINUTES} minutes. If you did not request this, you can safely ignore it.
        </p>
      </div>
      <p style="font-size: 12px; color: #8a8a82; margin-top: 20px;">Sent securely by Insighta.</p>
    </div>
    '''
    text_body = (
        f"Insighta - {title}\n\n{body_text}\n\n"
        f"Verification code: {code}\n\n"
        f"This code expires in {VERIFICATION_CODE_EXPIRE_MINUTES} minutes. "
        "If you did not request this, you can safely ignore it."
    )
    return send_email(email, subject, body, text_body=text_body)


__all__ = ['_send_verification_email']
