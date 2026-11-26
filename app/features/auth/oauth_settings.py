"""Features / auth / oauth settings."""

from app.bootstrap.settings import BASE_URL


def _linkedin_redirect_uri(path: str) -> str:
    return f"{BASE_URL.rstrip('/')}{path}"


__all__ = ['_linkedin_redirect_uri']
