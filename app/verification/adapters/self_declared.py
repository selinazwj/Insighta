"""
self-declared adapter — auto-passes everyone
this is just to prove the plumbing works before we write real adapters
"""
from datetime import datetime, timedelta
import uuid
from app.verification.interface import VerificationSession, VerificationResult


class SelfDeclaredAdapter:
  """
  ive made this adapter just return 'verified' immediately
  no actual checks, used for testing the flow
  """
  
  @property
  def tag(self) -> str:
    return "self_declared"
  
  def start(self, user_id: int, db) -> VerificationSession:
    # generate a fake session id
    session_id = f"self_{uuid.uuid4().hex[:12]}"
    
    # immediately mark as verified (no actual work)
    return VerificationSession(
      session_id=session_id,
      status="verified",
      next_step="wait",
      message="Auto-verified (self-declared mode)"
    )
  
  def check(self, session_id: str, db) -> VerificationResult:
    # always return verified
    return VerificationResult(
      status="verified",
      trust_score=0.5,  # low trust because its self-declared
      verified_at=datetime.utcnow(),
      expires_at=datetime.utcnow() + timedelta(days=365),
      message="Self-declared verification"
    )