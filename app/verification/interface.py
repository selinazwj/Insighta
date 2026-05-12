"""
ive defined the contract every verification adapter must follow
this is the single source of truth for what 'verification' means
"""
from typing import Protocol, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class VerificationSession:
  """
  what gets returned when verification starts
  the frontend uses this to know what to show next
  """
  session_id: str
  status: str  # 'pending' | 'verified' | 'rejected'
  next_step: str  # 'redirect' | 'upload' | 'input' | 'wait'
  redirect_url: Optional[str] = None
  message: Optional[str] = None


@dataclass
class VerificationResult:
  """
  what the adapter returns after checking
  """
  status: str  # 'pending' | 'verified' | 'rejected'
  trust_score: float  # 0.0 to 1.0
  evidence_ref: Optional[str] = None  # s3 key or provider session id
  verified_at: Optional[datetime] = None
  expires_at: Optional[datetime] = None
  message: Optional[str] = None


class VerifierAdapter(Protocol):
  """
  every adapter implements these two methods
  the system doesnt care which adapter runs, just that it follows this shape
  """
  
  @property
  def tag(self) -> str:
    """occupation tag this adapter handles — eg 'physician', 'student'"""
    ...
  
  def start(self, user_id: int, db) -> VerificationSession:
    """
    kick off verification for this user
    returns what the frontend should do next
    """
    ...
  
  def check(self, session_id: str, db) -> VerificationResult:
    """
    check the status of an ongoing verification
    returns the current state
    """
    ...