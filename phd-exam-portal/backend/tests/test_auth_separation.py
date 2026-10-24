"""Tests for the strict candidate/admin separation and RBAC.

Requires Postgres and Redis:

    docker compose exec backend python -m pytest tests/test_auth_separation.py -q
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.security import create_token, decode_token, hash_password, verify_password
from app.enums import AdminRole

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------ unit: tokens
async def test_candidate_token_is_rejected_by_the_admin_scope():
    token, _ = create_token("11111111-1111-1111-1111-111111111111", "candidate")
    assert decode_token(token, "candidate") is not None
    assert decode_token(token, "admin") is None


async def test_admin_token_is_rejected_by_the_candidate_scope():
    token, _ = create_token("22222222-2222-2222-2222-222222222222", "admin")
    assert decode_token(token, "admin") is not None
    assert decode_token(token, "candidate") is None


async def test_tampered_token_is_rejected():
    token, _ = create_token("33333333-3333-3333-3333-333333333333", "admin")
    assert decode_token(token[:-3] + "aaa", "admin") is None


async def test_the_two_cookies_have_different_names():
    assert settings.CANDIDATE_COOKIE_NAME != settings.ADMIN_COOKIE_NAME


async def test_password_hashing_roundtrip():
    h = hash_password("dsirt@123")
    assert h != "dsirt@123"
    assert h.startswith("$2")
    assert verify_password("dsirt@123", h)
    assert not verify_password("wrong", h)


async def test_overlong_password_is_rejected_not_truncated():
    """bcrypt silently truncates past 72 bytes; that must not pass silently."""
    with pytest.raises(ValueError):
        hash_password("x" * 100)


# ------------------------------------------------------ integration: routes
@pytest.fixture
async def client():
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _seed_admin(db):
    from app.models import Admin

    row = Admin(
        email="admin@example.com",
        hashed_password=hash_password("Admin@123"),
        role=AdminRole.SUPER_ADMIN,
        is_active=True,
    )
    db.add(row)
    await db.commit()
    return row


async def test_candidate_cookie_cannot_reach_admin_api(client, db, candidate):
    login = await client.post(
        "/api/auth/candidate/login",
        json={"email": candidate.email, "password": "dsirt@123"},
    )
    assert login.status_code == 200
    assert settings.CANDIDATE_COOKIE_NAME in login.cookies

    for path in ("/api/admin/dashboard", "/api/admin/candidates", "/api/admin/sessions/live"):
        response = await client.get(path)
        assert response.status_code == 401, f"{path} leaked to a candidate token"


async def test_admin_cookie_cannot_reach_candidate_api(client, db):
    await _seed_admin(db)
    login = await client.post(
        "/api/auth/admin/login",
        json={"email": "admin@example.com", "password": "Admin@123"},
    )
    assert login.status_code == 200

    for path in ("/api/exam/state", "/api/exam/paper", "/api/violations/mine"):
        response = await client.get(path)
        assert response.status_code == 401, f"{path} leaked to an admin token"


async def test_auth_cookies_are_httponly_and_samesite(client, db, candidate):
    response = await client.post(
        "/api/auth/candidate/login",
        json={"email": candidate.email, "password": "dsirt@123"},
    )
    header = response.headers.get("set-cookie", "").lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header


async def test_wrong_password_gives_the_same_message_as_unknown_email(client, db, candidate):
    a = await client.post(
        "/api/auth/candidate/login",
        json={"email": candidate.email, "password": "nope"},
    )
    b = await client.post(
        "/api/auth/candidate/login",
        json={"email": "nobody@example.com", "password": "nope"},
    )
    assert a.status_code == b.status_code == 401
    assert a.json()["detail"] == b.json()["detail"]


async def test_blocked_candidate_cannot_sign_in(client, db, candidate):
    from app.enums import CandidateStatus

    candidate.status = CandidateStatus.BLOCKED
    await db.commit()
    response = await client.post(
        "/api/auth/candidate/login",
        json={"email": candidate.email, "password": "dsirt@123"},
    )
    assert response.status_code == 403


async def test_unauthenticated_api_call_is_401_not_a_redirect(client):
    response = await client.get("/api/exam/state")
    assert response.status_code == 401


async def test_unauthenticated_page_redirects_to_the_right_login(client):
    candidate_page = await client.get(
        "/dashboard", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert candidate_page.status_code == 302
    assert candidate_page.headers["location"] == "/login"

    admin_page = await client.get(
        "/admin/dashboard", headers={"accept": "text/html"}, follow_redirects=False
    )
    assert admin_page.status_code == 302
    assert admin_page.headers["location"] == "/admin/login"


async def test_login_pages_are_separate_and_reachable(client):
    for path in ("/login", "/admin/login"):
        response = await client.get(path)
        assert response.status_code == 200
    candidate = (await client.get("/login")).text
    admin = (await client.get("/admin/login")).text
    assert "/api/auth/candidate/login" in candidate
    assert "/api/auth/admin/login" not in candidate
    assert "/api/auth/admin/login" in admin
    assert "/api/auth/candidate/login" not in admin
