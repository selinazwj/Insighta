"""Security helpers for AI Growth jump and prediction features."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from typing import Optional
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qsl

from fastapi import HTTPException, Request


_TOKEN_SECRET = os.environ.get("AI_GROWTH_TOKEN_SECRET") or os.environ.get("SECRET_KEY") or "insighta-ai-growth-dev-secret"
_ALLOWED_EXTERNAL_DOMAINS = {
    d.strip().lower()
    for d in os.environ.get("AI_GROWTH_ALLOWED_EXTERNAL_DOMAINS", "").split(",")
    if d.strip()
}


def stable_hash(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def short_hash(value: Optional[str], length: int = 16) -> Optional[str]:
    h = stable_hash(value)
    return h[:length] if h else None


def generate_plain_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hmac.new(_TOKEN_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def is_safe_internal_next(next_url: Optional[str]) -> bool:
    if not next_url:
        return False
    parsed = urlparse(next_url)
    if parsed.scheme or parsed.netloc:
        return False
    if not next_url.startswith("/"):
        return False
    if next_url.startswith("//"):
        return False
    return True


def login_redirect_with_next(path: str) -> str:
    safe_path = path if is_safe_internal_next(path) else "/choice"
    return "/login?" + urlencode({"next": safe_path})


def validate_external_url(url: Optional[str]) -> str:
    """Allow only HTTPS task URLs and optional configured domain whitelist."""
    value = (url or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Task URL is empty.")
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https":
        raise HTTPException(status_code=400, detail="External task URL must use https://.")
    if not parsed.netloc:
        raise HTTPException(status_code=400, detail="External task URL is missing a host.")
    lowered_host = parsed.hostname.lower() if parsed.hostname else ""
    if _ALLOWED_EXTERNAL_DOMAINS:
        allowed = any(lowered_host == d or lowered_host.endswith("." + d) for d in _ALLOWED_EXTERNAL_DOMAINS)
        if not allowed:
            raise HTTPException(status_code=400, detail="External task URL domain is not in the allowlist.")
    return value


def append_query_params(url: str, params: dict) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: str(v) for k, v in params.items() if v is not None})
    new_query = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def request_ip_hash(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
    return short_hash(ip)


def request_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:1000]


def base_url_from_request(request: Request) -> str:
    env_base = os.environ.get("BASE_URL")
    if env_base:
        return env_base.rstrip("/")
    return str(request.base_url).rstrip("/")


def quote_next(next_url: str) -> str:
    return quote(next_url, safe="/")
