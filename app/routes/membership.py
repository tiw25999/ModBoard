"""Membership purchase flow and Stripe webhook handler."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User
from app.models.membership import MembershipTier, UserMembership
from app.services.membership import active_membership, process_checkout_completed

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
log = logging.getLogger(__name__)


@router.post("/membership/checkout")
async def membership_checkout(
    request: Request,
    tier_id: int = Form(...),
    session: AsyncSession = Depends(get_db),
):
    user_state = request.state.user
    if user_state is None:
        # Admin cookie does not create a user session — send them to Google OAuth
        # so a real user row exists before we create a membership.
        login_url = "/auth/google/login?next=/donate" if request.state.is_admin else "/auth/login?next=/donate"
        return RedirectResponse(login_url, status_code=303)

    tier = await session.get(MembershipTier, tier_id)
    if tier is None or not tier.active:
        return RedirectResponse("/donate?error=invalid_tier", status_code=303)

    if not tier.stripe_price_id:
        return RedirectResponse("/donate?error=invalid_tier", status_code=303)

    # Guard: if user already has an active membership for this tier, do not
    # create a duplicate checkout — they would be charged again for no benefit.
    current = await active_membership(session, user_state["id"])
    if current is not None and current.tier_id == tier_id:
        return RedirectResponse("/donate?error=already_active", status_code=303)

    host = (settings.canonical_base or str(request.base_url)).rstrip("/")
    try:
        checkout = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": tier.stripe_price_id, "quantity": 1}],
            client_reference_id=str(user_state["id"]),
            metadata={"tier_id": str(tier.id)},
            success_url=f"{host}/membership/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{host}/donate",
        )
    except stripe.StripeError as exc:
        log.error("Stripe Checkout creation failed: %s", exc)
        return RedirectResponse("/donate?error=payment_unavailable", status_code=303)

    return RedirectResponse(checkout.url, status_code=303)


@router.get("/membership/success", response_class=HTMLResponse)
async def membership_success(
    request: Request,
    session_id: str = "",
    session: AsyncSession = Depends(get_db),
):
    # Require login — the session_id is in browser history and Referer headers.
    user_state = request.state.user
    if user_state is None:
        return RedirectResponse(f"/auth/login?next=/membership/success?session_id={session_id}", status_code=303)

    mem = None
    if session_id:
        mem = (await session.execute(
            select(UserMembership).where(
                UserMembership.stripe_checkout_session_id == session_id,
                UserMembership.user_id == user_state["id"],  # must belong to caller
            )
        )).scalar_one_or_none()
        if mem is not None:
            await session.refresh(mem, ["tier"])
    return templates.TemplateResponse(
        request, "membership_success.html",
        {"membership": mem},
    )


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    """Stripe sends checkout.session.completed here. Verified by signature."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError:
        return JSONResponse({"error": "invalid signature"}, status_code=400)
    except Exception as exc:
        log.error("Webhook parse error: %s", exc)
        return JSONResponse({"error": "parse error"}, status_code=400)

    if event["type"] != "checkout.session.completed":
        return JSONResponse({"status": "ignored"})

    cs = event["data"]["object"]
    raw_uid = cs.get("client_reference_id")
    raw_tier = (cs.get("metadata") or {}).get("tier_id")

    if not raw_uid or not raw_tier:
        log.warning("webhook missing client_reference_id or tier_id — skipping")
        return JSONResponse({"status": "skipped"})

    try:
        user_id = int(raw_uid)
        tier_id = int(raw_tier)
    except (TypeError, ValueError):
        log.warning("webhook: bad user_id/tier_id: %r / %r", raw_uid, raw_tier)
        return JSONResponse({"status": "bad_metadata"}, status_code=400)
    checkout_session_id = cs["id"]

    user = await session.get(User, user_id)
    if user is None:
        log.warning("webhook: user_id %s not found — skipping", user_id)
        return JSONResponse({"status": "user_not_found"})

    # Use Stripe's actual charge timestamp so expiry is precise even when
    # the webhook arrives late (Stripe retries for up to 3 days).
    stripe_created = cs.get("created")
    paid_at = (
        datetime.fromtimestamp(stripe_created, tz=timezone.utc)
        if stripe_created
        else datetime.now(timezone.utc)
    )

    result = await process_checkout_completed(
        session,
        user_id=user_id,
        tier_id=tier_id,
        session_id=checkout_session_id,
        paid_at=paid_at,
    )
    if result is not None:
        await session.commit()

    return JSONResponse({"status": "ok"})
