"""Features / auth / dependencies."""

from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User


def get_current_user(
    user_id: str = Cookie(None),
    db: Session = Depends(get_db)
):
    from app.features.auth.policy import _is_email_blocked

    if not user_id:
        raise HTTPException(401, "Not logged in")
    if not str(user_id).isdigit() or not 0 < int(user_id) <= 2**63-1:
        raise HTTPException(401, "Invalid login session")
    user = db.query(User).filter(User.id == int(user_id)).first()
    if not user:
        raise HTTPException(401, "User not found")
    if _is_email_blocked(db, user.email):
        raise HTTPException(403, "Account unavailable")
    return user


__all__ = ['get_current_user']
