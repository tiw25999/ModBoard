import base64
import secrets

from fastapi import Depends, HTTPException, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

security = HTTPBasic()

# UI-only marker cookie set after a successful admin Basic auth. The cookie
# itself doesn't grant access — every protected route still re-checks
# Basic auth via require_admin. We just use the cookie to render the
# "Admin Mode" badge + Exit link on non-/admin pages where the browser
# wouldn't normally send the Basic auth header.
ADMIN_COOKIE = "mb_admin"
ADMIN_COOKIE_MAX_AGE = 60 * 60 * 8  # 8 hours


def require_admin(
    response: Response,
    creds: HTTPBasicCredentials = Depends(security),
) -> str:
    ok_user = secrets.compare_digest(creds.username, settings.admin_username)
    ok_pass = secrets.compare_digest(creds.password, settings.admin_password)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    response.set_cookie(
        ADMIN_COOKIE,
        "1",
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,
    )
    return creds.username


def admin_marker_present(request) -> bool:
    """Cheap check the templates use — does the request carry the marker
    cookie? Display-level only; never use this to gate a destructive action."""
    return request.cookies.get(ADMIN_COOKIE) == "1"


def check_basic_admin_header(authorization: str | None) -> bool:
    """Validate the Basic auth header without raising. Used by middleware
    to set the marker cookie after a successful admin request."""
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:]).decode("utf-8")
        user, _, password = decoded.partition(":")
    except Exception:
        return False
    return (
        secrets.compare_digest(user, settings.admin_username)
        and secrets.compare_digest(password, settings.admin_password)
    )
