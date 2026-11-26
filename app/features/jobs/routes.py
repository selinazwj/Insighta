"""Features / jobs / routes."""

from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi.responses import JSONResponse


def internal_run_due_background_jobs(
    request: Request,
    limit: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    from app.features.jobs.service import _cron_secret_matches
    from app.features.jobs.service import run_due_background_jobs

    if not _cron_secret_matches(request):
        raise HTTPException(403, "Unauthorized")
    result = run_due_background_jobs(limit=limit)
    return JSONResponse({"ok": True, **result})


__all__ = ['internal_run_due_background_jobs']
