"""Web / middleware."""

from fastapi import Request
import os
from app.bootstrap.runtime import logger
from app.observability import request_duration_ms
from app.observability import request_id_from
from app.observability import request_timer_start
from app.observability import slow_request_threshold_ms
from app.seo import SITE_LANGUAGE


SEO_PUBLIC_EXACT_PATHS = {
    "/",
    "/participant",
    "/studies",
    "/about",
    "/how-it-works",
    "/privacy",
    "/terms",
    "/pricing",
    "/robots.txt",
    "/sitemap.xml",
}


def _is_public_seo_path(path: str) -> bool:
    if os.environ.get("INSIGHTA_ENV") == "staging":
        return False
    if path in SEO_PUBLIC_EXACT_PATHS:
        return True
    if path.startswith("/studies/"):
        return True
    if path.startswith("/r/") and not path.endswith("/qr.png"):
        return True
    return False


async def log_request_timing(request: Request, call_next):
    started_at = request_timer_start()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = request_duration_ms(started_at)
        status_code = response.status_code if response is not None else 500
        request_id = request_id_from(request)
        log_extra = {
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
            "request_id": request_id,
        }
        message = (
            "request method=%s path=%s status=%s duration_ms=%.2f request_id=%s"
            % (request.method, request.url.path, status_code, duration_ms, request_id or "-")
        )
        if duration_ms >= slow_request_threshold_ms():
            logger.warning("slow_%s", message, extra=log_extra)
        else:
            logger.info(message, extra=log_extra)


async def apply_seo_and_delivery_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Content-Language", SITE_LANGUAGE)

    if path.startswith("/static/"):
        # Assets are not fingerprinted, so use a moderate cache lifetime rather than immutable caching.
        response.headers.setdefault("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800")
    elif response.status_code >= 400:
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
    elif not _is_public_seo_path(path):
        # Defense in depth: the Jinja partial also emits noindex for non-public HTML pages.
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")

    if not path.startswith('/static/') and not _is_public_seo_path(path):
        response.headers.setdefault('Cache-Control', 'private, no-store')

    return response


def no_store_response(response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


__all__ = ['SEO_PUBLIC_EXACT_PATHS', '_is_public_seo_path', 'log_request_timing', 'apply_seo_and_delivery_headers', 'no_store_response']
