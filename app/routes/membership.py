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
        return RedirectResponse("/auth/login?next=/donate", status_code=303)

    tier = await session.get(MembershipTier, tier_id)
    if tier is None or not tier.active:
        return RedirectResponse("/donate?error=invalid_tier", status_code=303)

    if not tier.stripe_price_id:
        return RedirectResponse("/donate?error=invalid_tier", status_code=303)

    stripe.api_key = settings.stripe_secret_key
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
    mem = None
    if session_id:
        mem = (await session.execute(
            select(UserMembership)
            .where(UserMembership.stripe_checkout_session_id == session_id)
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

    user_id = int(raw_uid)
    tier_id = int(raw_tier)
    checkout_session_id = cs["id"]

    user = await session.get(User, user_id)
    if user is None:
        log.warning("webhook: user_id %s not found — skipping", user_id)
        return JSONResponse({"status": "user_not_found"})

    paid_at = datetime.now(timezone.utc)
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
