"""
ive written the verification service — the orchestrator
routes call this, it calls adapters, writes results, promotes responses
"""
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import User, Verification, Response
from app.verification.resolver import resolve_adapter
from app.verification.exceptions import AlreadyVerified


def start_verification(user: User, db: Session):
  """
  starts verification for a user
  checks cache first, then calls the adapter
  """
  # check if already verified and not expired
  existing = db.query(Verification).filter(
    Verification.user_id == user.id,
    Verification.status == 'verified',
    Verification.expires_at > datetime.utcnow()
  ).first()
  
  if existing:
    return {
      "session_id": f"cached_{existing.id}",
      "status": "verified",
      "next_step": "wait",
      "message": "Already verified"
    }
  
  # get the right adapter for this user
  adapter = resolve_adapter(user)
  
  # start verification
  session = adapter.start(user.id, db)
  
  # if adapter returned verified immediately (like self_declared), write it
  if session.status == "verified":
    result = adapter.check(session.session_id, db)
    _write_verification(user.id, adapter.tag, result, db)
    _promote_pending_responses(user.id, db)
  
  return {
    "session_id": session.session_id,
    "status": session.status,
    "next_step": session.next_step,
    "redirect_url": session.redirect_url,
    "message": session.message
  }


def check_verification_status(session_id: str, user_id: int, db: Session):
  """
  checks the status of an ongoing verification
  frontend polls this
  """
  # if its a cached session, just return verified
  if session_id.startswith("cached_"):
    return {"status": "verified", "message": "Already verified"}
  
  # otherwise call the adapter to check
  # for now we only have self_declared which is instant
  # real adapters will query external APIs here
  
  return {"status": "verified", "message": "Verification complete"}


def is_verified_for_withdrawal(user: User, db: Session) -> bool:
  """
  checks if user has any valid verification
  called by the /api/withdraw endpoint
  """
  valid = db.query(Verification).filter(
    Verification.user_id == user.id,
    Verification.status == 'verified',
    Verification.expires_at > datetime.utcnow()
  ).first()
  
  return valid is not None


def _write_verification(user_id: int, method: str, result, db: Session):
  """
  ive written this helper to save verification results to the db
  """
  verification = Verification(
    user_id=user_id,
    attribute='general',  # for now, later this will be more specific
    method=method,
    status=result.status,
    trust_score=result.trust_score,
    evidence_ref=result.evidence_ref,
    verified_at=result.verified_at,
    expires_at=result.expires_at,
    verified_by='system'
  )
  db.add(verification)
  db.commit()


def _promote_pending_responses(user_id: int, db: Session):
  """
  ive written this to promote all pending responses for this user
  and credit their earnings
  """
  pending_responses = db.query(Response).filter(
    Response.participant_id == user_id,
    Response.verification_status == 'pending'
  ).all()
  
  total_to_credit = 0.0
  
  for r in pending_responses:
    r.verification_status = 'verified'
    if r.payout_amount:
      total_to_credit += r.payout_amount
  
  # credit the user's pending_earnings
  user = db.query(User).filter(User.id == user_id).first()
  if user:
    current = getattr(user, 'pending_earnings', 0.0) or 0.0
    user.pending_earnings = current + total_to_credit
  
  db.commit()