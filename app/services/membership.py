from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.membership import MembershipTier, UserMembership


async def active_membership(
    session: AsyncSession, user_id: int
) -> UserMembership | None:
    """Return the highest-expiry non-expired membership, or None."""
    now = datetime.now(timezone.utc)
    return (
        await session.execute(
            select(UserMembership)
            .where(
                UserMembership.user_id == user_id,
                UserMembership.expires_at > now,
            )
            .order_by(UserMembership.expires_at.desc())
            .limit(1)
            .options(selectinload(UserMembership.tier))
        )
    ).scalar_one_or_none()


async def vote_weight_for(session: AsyncSession, user_id: int) -> int:
    """Return the vote weight for this user (1 if no active membership)."""
    mem = await active_membership(session, user_id)
    if mem is None:
        return 1
    return mem.tier.vote_weight


async def process_checkout_completed(
    session: AsyncSession,
    *,
    user_id: int,
    tier_id: int,
    session_id: str,
    paid_at: datetime,
) -> UserMembership | None:
    """Insert a UserMembership row for a completed Stripe checkout.

    Idempotent: if session_id already exists, returns None without inserting.
    Uses a savepoint so the outer transaction remains valid on duplicate.
    """
    row = UserMembership(
        user_id=user_id,
        tier_id=tier_id,
        stripe_checkout_session_id=session_id,
        expires_at=paid_at + timedelta(days=30),
        created_at=paid_at,
    )
    # Use a savepoint (nested transaction) so that an IntegrityError on the
    # UNIQUE constraint rolls back only this insert, leaving the outer session
    # alive. A bare session.rollback() would invalidate the outer transaction.
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return row
    except IntegrityError:
        return None
