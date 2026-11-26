"""Features / admin / routes."""

from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import RedirectResponse
from app.bootstrap.runtime import templates


def admin_page(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


def admin_discovery_page(request: Request):
    return RedirectResponse("/admin#discovery", status_code=303)


async def admin_verify(request: Request):
    from app.features.admin.access import _admin_key_matches

    body = await request.json()
    if not _admin_key_matches(body.get("admin_key")):
        raise HTTPException(403, "Unauthorized")
    return JSONResponse({"success": True})


__all__ = ['admin_page', 'admin_discovery_page', 'admin_verify']
