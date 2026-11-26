"""Features / admin / feedback."""

from app import withdrawals
from datetime import datetime
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Feedback
from app.models import User
from app.pricing import PricingError


async def grant_credit(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    from app.pricing import money_cents
    try:
        amount_cents = money_cents(body.get('amount'))
    except PricingError as exc:
        raise HTTPException(400, str(exc))
    if amount_cents <= 0:
        raise HTTPException(400, 'Amount must be positive.')
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback:
        raise HTTPException(404, 'Feedback not found')
    user = db.query(User).filter(User.id == feedback.user_id).populate_existing().with_for_update().first()
    if not user:
        raise HTTPException(404, 'User not found')
    user = withdrawals.lock_participant(db, user.id)
    feedback = db.query(Feedback).filter_by(id=feedback_id).populate_existing().with_for_update().one()
    if feedback.status == 'credited':
        if withdrawals.cents(feedback.credit_amount) != amount_cents:
            raise HTTPException(409, 'This feedback was already credited with a different amount.')
        return {'success':True, 'user_email':user.email, 'amount':feedback.credit_amount,
                'new_balance':user.pending_earnings, 'already_credited':True}
    amount = amount_cents/100
    user.pending_earnings = (getattr(user, 'pending_earnings', 0.0) or 0.0) + amount
    feedback.status = 'credited'
    feedback.credit_amount = amount
    feedback.reviewed_at = datetime.utcnow()
    db.commit()
    return JSONResponse({"success": True, "user_email": user.email, "amount": amount, "new_balance": user.pending_earnings})


async def reject_feedback(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")): raise HTTPException(403, "Unauthorized")
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback: raise HTTPException(404, "Feedback not found")
    feedback = db.query(Feedback).filter_by(id=feedback_id).populate_existing().with_for_update().one()
    if feedback.status == 'credited':
        raise HTTPException(409, 'Credited feedback cannot be rejected; retain the award record.')
    changed = db.query(Feedback).filter(Feedback.id == feedback_id, Feedback.status != 'credited').update(
        {'status':'rejected', 'reviewed_at':datetime.utcnow()}, synchronize_session=False)
    if not changed:
        raise HTTPException(409, 'Credited feedback cannot be rejected; retain the award record.')
    db.commit()
    return JSONResponse({"success": True})


async def list_feedbacks(request: Request, admin_key: str = Query(None), db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(admin_key): raise HTTPException(403, "Unauthorized")
    feedbacks = db.query(Feedback).order_by(Feedback.created_at.desc()).all()
    result = []
    for f in feedbacks:
        user = db.query(User).filter(User.id == f.user_id).first()
        result.append({"id": f.id, "user_email": user.email if user else "unknown", "category": f.category, "title": f.title, "content": f.content, "status": f.status, "credit_amount": f.credit_amount, "created_at": str(f.created_at)})
    return JSONResponse(result)


__all__ = ['grant_credit', 'reject_feedback', 'list_feedbacks']
