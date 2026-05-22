"""Tests for admin cookie auth + credential check.
Uses settings-pinned secret so tests don't depend on real .env."""
from fastapi import Response

from app.services.auth import (
    is_admin,
    set_admin_session,
    clear_admin_session,
    verify_admin_credentials,
)


class _FakeRequest:
    """Minimal Starlette-compatible Request stub for cookie reads."""
    def __init__(self, cookies: dict):
        self.cookies = cookies


def _make_response_with_cookie(token_value: str | None = None) -> Response:
    r = Response()
    if token_value is not None:
        r.set_cookie("mb_admin", token_value)
    return r


def test_no_cookie_is_not_admin():
    assert is_admin(_FakeRequest({})) is False


def test_garbage_cookie_is_not_admin():
    assert is_admin(_FakeRequest({"mb_admin": "garbage"})) is False
    assert is_admin(_FakeRequest({"mb_admin": "admin.fake.signature"})) is False


def test_set_then_read_round_trip():
    """A cookie issued by set_admin_session must be accepted by is_admin."""
    response = Response()
    set_admin_session(response)
    # Pull the cookie value out of the response header
    set_cookie = response.headers.get("set-cookie", "")
    # Extract the mb_admin=... portion
    assert "mb_admin=" in set_cookie
    value = set_cookie.split("mb_admin=", 1)[1].split(";", 1)[0]
    assert is_admin(_FakeRequest({"mb_admin": value})) is True


def test_clear_admin_session_emits_deletion_cookie():
    response = Response()
    clear_admin_session(response)
    set_cookie = response.headers.get("set-cookie", "")
    assert "mb_admin=" in set_cookie
    # Cleared cookies have Max-Age=0 or an expires-in-past directive
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_verify_credentials_matches_settings():
    from app.config import settings
    assert verify_admin_credentials(settings.admin_username, settings.admin_password) is True
    assert verify_admin_credentials(settings.admin_username, "wrong") is False
    assert verify_admin_credentials("wrong", settings.admin_password) is False
