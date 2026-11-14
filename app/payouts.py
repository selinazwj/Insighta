from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models import Response, User


UNDER_REVIEW = "pending_review"
APPROVED = "approved"
PAID = "paid"
REJECTED = "rejected"
LEGACY_RELEASED = "pending"


def _amount(response: Response) -> float:
    return float(response.payout_amount or 0.0)


def _participant(db: Session, response: Response) -> User | None:
    if not response.participant_id:
        return None
    return db.query(User).filter(User.id == response.participant_id).populate_existing().with_for_update().first()


def mark_response_under_review(response: Response) -> None:
    response.payout_status = UNDER_REVIEW
    if response.completed_at and not response.review_due_at:
        completed = response.completed_at
        if completed.tzinfo:
            completed = completed.astimezone(timezone.utc).replace(tzinfo=None)
        response.review_due_at = completed + timedelta(days=7)


def release_response_payout(db: Session, response: Response, *, automated=False, now=None) -> None:
    if response.payout_status in {APPROVED, PAID}:
        return
    from app.settlements import assert_review_mutable
    assert_review_mutable(db, response, action="approve", automated=automated, now=now)
    if response.payout_status in {APPROVED, PAID}:
        return
    participant = _participant(db, response)
    if participant and response.payout_status != LEGACY_RELEASED:
        participant.pending_earnings = (getattr(participant, "pending_earnings", 0.0) or 0.0) + _amount(response)
    response.payout_status = APPROVED
    response.payout_reviewed_at = now or datetime.utcnow()
    db.flush()


def reject_response_payout(db: Session, response: Response) -> None:
    from app.settlements import assert_review_mutable
    assert_review_mutable(db, response, action="reject")
    if response.payout_status == PAID:
        return
    participant = _participant(db, response)
    if participant and response.payout_status in {APPROVED, LEGACY_RELEASED}:
        participant.pending_earnings = max(
            0.0,
            (getattr(participant, "pending_earnings", 0.0) or 0.0) - _amount(response),
        )
    response.payout_status = REJECTED
    response.payout_reviewed_at = datetime.utcnow()
    db.flush()


def return_response_to_review(db: Session, response: Response) -> None:
    from app.settlements import assert_review_mutable
    assert_review_mutable(db, response, action="reset")
    participant = _participant(db, response)
    if participant and response.payout_status in {APPROVED, LEGACY_RELEASED}:
        participant.pending_earnings = max(
            0.0,
            (getattr(participant, "pending_earnings", 0.0) or 0.0) - _amount(response),
        )
    response.payout_status = UNDER_REVIEW
