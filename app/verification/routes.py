"""Retired prototype routes.

These routes previously trusted a caller-supplied user_id and used a
self-declared adapter that auto-approved every request. The main application no
longer mounts this router. Keeping an explicit 410 response makes an accidental
future mount fail safely.
"""

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/verifications", tags=["verification"])

@router.post("/start")
def start_verification_endpoint():
    raise HTTPException(
        410,
        "This prototype verification endpoint has been retired. Use /verify.",
    )


@router.get("/status/{session_id}")
def check_status_endpoint(session_id: str):
    raise HTTPException(
        410,
        "This prototype verification endpoint has been retired. Use /verify.",
    )
