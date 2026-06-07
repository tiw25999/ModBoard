"""Unit tests for membership service helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import User
from app.models.membership import MembershipTier, UserMembership
from app.services.membership import active_membership, vote_weight_for, process_checkout_completed

DB_URL = "postgresql+asyncpg://postgres@127.0.0.1:5432/modboard"
_engine = create_async_engine(DB_URL, echo=False, poolclass=NullPool)
_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def _make_user(suffix: str) -> int:
    async with _factory() as s:
        u = User(
            google_sub=f"mem_sub_{suffix}",
            email=f"mem_{suffix}@test.local",
            name="MemberTest",
            created_at=datetime.now(timezone.utc),
            last_login_at=datetime.now(timezone.utc),
        )
        s.add(u)
        await s.commit()
        return u.id


async def _cleanup_user(uid: int) -> None:
    async with _factory() as s:
        await s.execute(text("DELETE FROM user_memberships WHERE user_id=:id"), {"id": uid})
        await s.execute(text("DELETE FROM users WHERE id=:id"), {"id": uid})
        await s.commit()


async def _tier_id() -> int:
    async with _factory() as s:
        # ORDER BY id guarantees Coffee (id=1, weight=2) every time.
        row = (await s.execute(
            select(MembershipTier).order_by(MembershipTier.id).limit(1)
        )).scalar_one()
        return row.id


@pytest.mark.asyncio
async def test_vote_weight_no_membership():
    uid = await _make_user(uuid.uuid4().hex[:8])
    try:
        async with _factory() as s:
            w = await vote_weight_for(s, uid)
        assert w == 1
    finally:
        await _cleanup_user(uid)


@pytest.mark.asyncio
async def test_vote_weight_active_membership():
    uid = await _make_user(uuid.uuid4().hex[:8])
    tid = await _tier_id()
    try:
        now = datetime.now(timezone.utc)
        async with _factory() as s:
            s.add(UserMembership(
                user_id=uid, tier_id=tid,
                stripe_checkout_session_id=f"cs_act_{uuid.uuid4().hex[:8]}",
                expires_at=now + timedelta(days=30),
                created_at=now,
            ))
            await s.commit()
            w = await vote_weight_for(s, uid)
        assert w == 2, "Coffee tier weight should be 2"
    finally:
        await _cleanup_user(uid)


@pytest.mark.asyncio
async def test_vote_weight_expired_membership():
    uid = await _make_user(uuid.uuid4().hex[:8])
    tid = await _tier_id()
    try:
        now = datetime.now(timezone.utc)
        async with _factory() as s:
            s.add(UserMembership(
                user_id=uid, tier_id=tid,
                stripe_checkout_session_id=f"cs_exp_{uuid.uuid4().hex[:8]}",
                expires_at=now - timedelta(seconds=1),
                created_at=now - timedelta(days=31),
            ))
            await s.commit()
            w = await vote_weight_for(s, uid)
        assert w == 1, "expired membership falls back to 1"
    finally:
        await _cleanup_user(uid)


@pytest.mark.asyncio
async def test_active_membership_returns_latest():
    uid = await _make_user(uuid.uuid4().hex[:8])
    tid = await _tier_id()
    try:
        now = datetime.now(timezone.utc)
        sid_active = f"cs_lat_{uuid.uuid4().hex[:8]}"
        async with _factory() as s:
            s.add_all([
                UserMembership(
                    user_id=uid, tier_id=tid,
                    stripe_checkout_session_id=f"cs_old_{uuid.uuid4().hex[:8]}",
                    expires_at=now - timedelta(seconds=1),
                    created_at=now - timedelta(days=31),
                ),
                UserMembership(
                    user_id=uid, tier_id=tid,
                    stripe_checkout_session_id=sid_active,
                    expires_at=now + timedelta(days=20),
                    created_at=now,
                ),
            ])
            await s.commit()
            result = await active_membership(s, uid)
        assert result is not None
        assert result.stripe_checkout_session_id == sid_active
    finally:
        await _cleanup_user(uid)


@pytest.mark.asyncio
async def test_webhook_handler_idempotent():
    uid = await _make_user(uuid.uuid4().hex[:8])
    tid = await _tier_id()
    session_id = f"cs_idem_{uuid.uuid4().hex[:8]}"
    try:
        now = datetime.now(timezone.utc)
        async with _factory() as s1:
            r1 = await process_checkout_completed(
                s1, user_id=uid, tier_id=tid, session_id=session_id, paid_at=now
            )
            assert r1 is not None
            await s1.commit()

        async with _factory() as s2:
            r2 = await process_checkout_completed(
                s2, user_id=uid, tier_id=tid, session_id=session_id, paid_at=now
            )
            assert r2 is None

        async with _factory() as verify:
            count = (await verify.execute(
                select(func.count()).select_from(UserMembership)
                .where(UserMembership.stripe_checkout_session_id == session_id)
            )).scalar()
        assert count == 1
    finally:
        await _cleanup_user(uid)
