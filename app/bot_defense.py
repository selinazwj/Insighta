"""
ive written the bot defense helpers (phase 4)
three layers, all self contained so we dont need external services:
1. rate limiting  — in-memory sliding window, keyed by ip / email / user id
2. math captcha   — HMAC signed so the server stays stateless
3. honeypot       — a hidden form field that only bots fill in
"""
import hashlib
import hmac
import os
import random
import secrets
import time
from typing import Optional


# ---------------------------
# 1. rate limiting
# ---------------------------

# key -> list of hit timestamps. in-memory on purpose: resets on restart,
# good enough for a single uvicorn worker. redis comes later if we scale.
_RATE_BUCKETS: dict = {}


def check_rate_limit(key: str, max_hits: int, window_seconds: int) -> bool:
    """
    returns True if this hit is allowed, False if the caller should back off.
    sliding window: we only count hits younger than window_seconds.
    """
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
