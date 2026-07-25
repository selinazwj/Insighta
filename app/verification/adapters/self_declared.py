"""Retired self-declared adapter.

Self-declaration is an eligibility input, not identity evidence, and therefore
must never grant a verified status.
"""
from datetime import datetime
import uuid
from app.verification.interface import VerificationSession, VerificationResult


class SelfDeclaredAdapter:
  """
  Compatibility stub that fails closed.
  """
  
  @property
  def tag(self) -> str:
    return "self_declared"
  
  def start(self, user_id: int, db) -> VerificationSession:
    # generate a fake session id
    session_id = f"self_{uuid.uuid4().hex[:12]}"
    
    return VerificationSession(
      session_id=session_id,
      status="rejected",
      next_step="wait",
      message="Self-declaration does not qualify as verification evidence."
    )
  
  def check(self, session_id: str, db) -> VerificationResult:
    return VerificationResult(
      status="rejected",
      trust_score=0.0,
      verified_at=datetime.utcnow(),
      expires_at=datetime.utcnow(),
      message="Self-declaration does not qualify as verification evidence."
    )
