"""Bot-defense helpers with an optional shared Redis rate-limit backend."""
import hashlib
import hmac
import os
import random
import secrets
import time
from typing import Optional

import httpx


# ---------------------------
# 1. rate limiting
# ---------------------------

# Local fallback for development when no shared Redis URL is configured.
_RATE_BUCKETS: dict = {}
_REDIS_CLIENT = None
_REDIS_INIT_ATTEMPTED = False

_REDIS_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local cutoff = tonumber(ARGV[1])
local now = tonumber(ARGV[2])
local member = ARGV[3]
local max_hits = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)
if count >= max_hits then
  redis.call('EXPIRE', key, ttl)
  return 0
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return 1
"""


def _is_production() -> bool:
    environment = (
        os.environ.get("ENVIRONMENT")
        or os.environ.get("VERCEL_ENV")
        or ""
    ).strip().lower()
    return environment in {"production", "prod"}


def _shared_rate_limit_client():
    global _REDIS_CLIENT, _REDIS_INIT_ATTEMPTED
    if _REDIS_INIT_ATTEMPTED:
        return _REDIS_CLIENT
    _REDIS_INIT_ATTEMPTED = True
    redis_url = (os.environ.get("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        import redis

        _REDIS_CLIENT = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
    except (ImportError, ValueError):
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


def _shared_rate_limit(key: str, max_hits: int, window_seconds: int) -> Optional[bool]:
    client = _shared_rate_limit_client()
    if client is None:
        if (os.environ.get("REDIS_URL") or "").strip() and _is_production():
            return False
        return None
    now_ms = int(time.time() * 1000)
    rate_key_secret = (
        os.environ.get("RATE_LIMIT_HASH_SECRET")
        or os.environ.get("VERIFICATION_EVIDENCE_SECRET")
        or _CAPTCHA_SECRET
    )
    safe_key = hmac.new(
        rate_key_secret.encode(),
        key.encode(),
        hashlib.sha256,
    ).hexdigest()
    member = f"{now_ms}:{secrets.token_hex(5)}"
    try:
        allowed = client.eval(
            _REDIS_SLIDING_WINDOW_SCRIPT,
            1,
            f"insighta:rate:{safe_key}",
            now_ms - window_seconds * 1000,
            now_ms,
            member,
            max_hits,
            max(1, window_seconds),
        )
        return bool(allowed)
    except Exception:
        if _is_production():
            return False
        return None


def check_rate_limit(key: str, max_hits: int, window_seconds: int) -> bool:
    """
    returns True if this hit is allowed, False if the caller should back off.
    sliding window: we only count hits younger than window_seconds.
    """
    shared_result = _shared_rate_limit(key, max_hits, window_seconds)
    if shared_result is not None:
        return shared_result

    now = time.time()
    hits = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
    if len(hits) >= max_hits:
        _RATE_BUCKETS[key] = hits  # keep the pruned list so memory doesnt grow
        return False
    hits.append(now)
    _RATE_BUCKETS[key] = hits
    # opportunistic cleanup so the dict doesnt fill with dead keys
    if len(_RATE_BUCKETS) > 10000:
        cutoff = now - window_seconds
        for k in list(_RATE_BUCKETS.keys()):
            if not _RATE_BUCKETS[k] or _RATE_BUCKETS[k][-1] < cutoff:
                del _RATE_BUCKETS[k]
    return True


def client_ip(request) -> str:
    """
    best-effort client ip — behind a proxy the real ip is in X-Forwarded-For
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------
# 2. math captcha (stateless, HMAC signed)
# ---------------------------

# secret rotates on every restart unless pinned via env — thats fine,
# it just invalidates any captcha older than the restart
_CAPTCHA_SECRET = os.environ.get("CAPTCHA_SECRET") or secrets.token_hex(32)
CAPTCHA_TTL_SECONDS = 600  # 10 minutes to fill the form


def _sign(payload: str) -> str:
    return hmac.new(_CAPTCHA_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_captcha():
    """
    returns (question, token). the token holds expiry + hmac(answer:expiry)
    but NOT the answer itself, so a bot cant just parse it out.
    """
    a, b = random.randint(1, 9), random.randint(1, 9)
    answer = a + b
    expiry = int(time.time()) + CAPTCHA_TTL_SECONDS
    token = f"{expiry}:{_sign(f'{answer}:{expiry}')}"
    return f"{a} + {b}", token


def verify_captcha(token: Optional[str], user_answer: Optional[str]) -> bool:
    """
    recompute the hmac with the users answer — if it matches the signed one
    the answer is right. constant-time compare, expiry checked.
    """
    if not token or not user_answer:
        return False
    try:
        expiry_str, sig = token.split(":", 1)
        expiry = int(expiry_str)
        answer = int(str(user_answer).strip())
    except (ValueError, AttributeError):
        return False
    if time.time() > expiry:
        return False
    expected = _sign(f"{answer}:{expiry}")
    return hmac.compare_digest(expected, sig)


# ---------------------------
# 3. honeypot
# ---------------------------

# the register form has a hidden input named "website" — humans never see it,
# autofill bots stuff every field. anything in it means bot.
HONEYPOT_FIELD = "website"


def honeypot_triggered(form) -> bool:
    return bool((form.get(HONEYPOT_FIELD) or "").strip())


# ---------------------------
# 4. managed bot challenge
# ---------------------------

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_site_key() -> Optional[str]:
    site_key = (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()
    secret_key = (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()
    return site_key if site_key and secret_key else None


async def verify_bot_challenge(
    *,
    managed_token: Optional[str],
    fallback_token: Optional[str],
    fallback_answer: Optional[str],
    remote_ip: Optional[str],
) -> bool:
    """
    Validate a managed challenge when configured.

    Local environments without provider credentials retain the signed math
    challenge for development only. Production should configure Turnstile; a
    provider error fails closed instead of silently accepting the fallback.
    """
    secret_key = (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip()
    site_key = (os.environ.get("TURNSTILE_SITE_KEY") or "").strip()
    if not (secret_key and site_key):
        if _is_production():
            return False
        return verify_captcha(fallback_token, fallback_answer)
    if not managed_token:
        return False

    payload = {
        "secret": secret_key,
        "response": managed_token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(TURNSTILE_VERIFY_URL, data=payload)
        if response.status_code != 200:
            return False
        result = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return result.get("success") is True
