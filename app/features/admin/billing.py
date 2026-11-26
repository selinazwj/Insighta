"""Features / admin / billing."""

from app import withdrawals
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from sqlalchemy.orm import Session
import asyncio
from app.database import get_db


def admin_withdrawals(request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(request.headers.get('x-admin-key')):
        raise HTTPException(403, 'Administrator authorization required.')
    rows = db.query(withdrawals.ParticipantWithdrawal).order_by(withdrawals.ParticipantWithdrawal.id.desc()).limit(100).all()
    return {'withdrawals':[{'id':row.id, 'user_id':row.user_id, 'amount_cents':row.amount_cents,
        'status':row.status, 'transfer_id':row.stripe_transfer_id, 'last_error':row.last_error,
        'created_at':row.created_at.isoformat()+'Z'} for row in rows]}


async def admin_reconcile_withdrawal(withdrawal_id: int, request: Request, db: Session = Depends(get_db)):
    from app.features.admin.access import _admin_key_matches

    if not _admin_key_matches(request.headers.get('x-admin-key')):
        raise HTTPException(403, 'Administrator authorization required.')
    # This endpoint only locates an existing Stripe transfer. It cannot send money.
    return await asyncio.to_thread(withdrawals.reconcile, db, withdrawal_id)


__all__ = ['admin_withdrawals', 'admin_reconcile_withdrawal']
