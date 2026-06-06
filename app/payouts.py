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
    return db.query(User).filter(User.id == response.participant_id).first()


def mark_response_under_review(response: Response) -> None:
    response.payout_status = UNDER_REVIEW


def release_response_payout(db: Session, response: Response) -> None:
    if response.payout_status in {APPROVED, PAID}:
        return
    participant = _participant(db, response)
    if participant and response.payout_status != LEGACY_RELEASED:
        participant.pending_earnings = (getattr(participant, "pending_earnings", 0.0) or 0.0) + _amount(response)
    response.payout_status = APPROVED


def reject_response_payout(db: Session, response: Response) -> None:
    if response.payout_status == PAID:
        return
    participant = _participant(db, response)
    if participant and response.payout_status in {APPROVED, LEGACY_RELEASED}:
        participant.pending_earnings = max(
            0.0,
            (getattr(participant, "pending_earnings", 0.0) or 0.0) - _amount(response),
        )
    response.payout_status = REJECTED


def return_response_to_review(db: Session, response: Response) -> None:
    participant = _participant(db, response)
    if participant and response.payout_status in {APPROVED, LEGACY_RELEASED}:
        participant.pending_earnings = max(
            0.0,
            (getattr(participant, "pending_earnings", 0.0) or 0.0) - _amount(response),
        )
    response.payout_status = UNDER_REVIEW
