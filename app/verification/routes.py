"""
ive written the FastAPI routes for verification
frontend hits these endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.verification import service

# ive created the router that will be included in main.py
router = APIRouter(prefix="/verifications", tags=["verification"])


def get_current_user_id(user_id: int = None):
  """
  placeholder — in real code this comes from the session/cookie
  for now we'll pass it in requests
  """
  if not user_id:
    raise HTTPException(401, "Not authenticated")
  return user_id


@router.post("/start")
def start_verification_endpoint(
  user_id: int,  # in real code this comes from get_current_user dependency
  db: Session = Depends(get_db)
):
  """
  user clicks 'claim reward' — this kicks off verification
  """
  user = db.query(User).filter(User.id == user_id).first()
  if not user:
    raise HTTPException(404, "User not found")
  
  result = service.start_verification(user, db)
  return result


@router.get("/status/{session_id}")
def check_status_endpoint(
  session_id: str,
  user_id: int,  # from session in real code
  db: Session = Depends(get_db)
):
  """
  frontend polls this to check verification progress
  """
  result = service.check_verification_status(session_id, user_id, db)
  return result