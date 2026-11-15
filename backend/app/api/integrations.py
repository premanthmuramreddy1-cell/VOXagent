"""Connections to outside services: Google, GitHub, Telegram/WhatsApp linking, payments, and their webhooks."""

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import billing, bots, deploy, github, google
from app.config import settings
from app.crypto import delete_secret
from app.db import BotLink, Secret, User, get_db
from app.security import current_user

router = APIRouter()


@router.get("/integrations")
def status(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    kinds = {s.kind for s in db.scalars(select(Secret).where(Secret.user_id == user.id))}
    links = {b.platform for b in db.scalars(select(BotLink).where(BotLink.user_id == user.id))}
    cfg = settings()
    return {
        "google": {"available": google.configured(), "connected": "google" in kinds},
        "github": {"oauth": bool(cfg.github_client_id), "connected": "github" in kinds},
        "telegram": {"available": bool(cfg.telegram_token), "linked": "telegram" in links},
        "whatsapp": {"available": bool(cfg.whatsapp_token and cfg.whatsapp_phone_id), "linked": "whatsapp" in links},
        "deploy": cfg.deploy_provider.split(":", 1)[0] or None,
        "billing": {"available": billing.configured(), "tier": user.tier},
    }


def _back(ok: bool, what: str) -> RedirectResponse:
    flag = "connected" if ok else "failed"
    return RedirectResponse(f"{settings().public_url}/#/account?{what}={flag}", status_code=303)


@router.get("/integrations/google/connect")
def google_connect(user: User = Depends(current_user)) -> dict:
    try:
        return {"url": google.connect_url(user.id)}
    except google.GoogleError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/integrations/google/callback")
async def google_callback(code: str = "", state: str = "", db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        await google.finish_connect(db, code, state)
    except google.GoogleError:
        return _back(False, "google")
    return _back(True, "google")


@router.get("/integrations/github/connect")
def github_connect(user: User = Depends(current_user)) -> dict:
    try:
        return {"url": github.connect_url(user.id)}
    except github.GithubError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/integrations/github/callback")
async def github_callback(code: str = "", state: str = "", db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        await github.finish_connect(db, code, state)
    except (github.GithubError, google.GoogleError):
        return _back(False, "github")
    return _back(True, "github")


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=300)


@router.post("/integrations/github/token")
async def github_token(body: TokenBody, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    try:
        return {"login": await github.save_token(db, user.id, body.token.strip())}
    except github.GithubError as exc:
        raise HTTPException(400, str(exc)) from None


@router.delete("/integrations/{kind}")
def disconnect(kind: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if kind not in ("google", "github"):
        raise HTTPException(404, "unknown integration")
    return {"removed": delete_secret(db, user.id, kind)}


@router.post("/bots/link-code")
def link_code(user: User = Depends(current_user)) -> dict:
    return {"code": bots.new_link_code(user.id), "expires_in_s": 600}


@router.delete("/bots/{platform}")
def unlink(platform: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rows = list(db.scalars(select(BotLink).where(BotLink.user_id == user.id, BotLink.platform == platform)))
    for row in rows:
        db.delete(row)
    db.commit()
    return {"removed": len(rows)}


@router.post("/billing/checkout")
async def checkout(user: User = Depends(current_user)) -> dict:
    try:
        return {"url": await billing.checkout_url(user)}
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc)) from None


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    payload = await request.body()
    try:
        billing.verify(payload, request.headers.get("stripe-signature", ""), settings().stripe_webhook_secret)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"result": billing.apply_event(db, json.loads(payload))}


@router.get("/webhooks/whatsapp")
def whatsapp_verify(request: Request) -> Response:
    params = request.query_params
    token = settings().whatsapp_verify_token
    if params.get("hub.mode") == "subscribe" and token and params.get("hub.verify_token") == token:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(403, "verification failed")


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request) -> dict:
    payload = await request.body()
    if not bots.whatsapp_signature_ok(payload, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(401, "bad signature")
    for entry in json.loads(payload).get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                await bots.whatsapp_message(message)
    return {"ok": True}


@router.get("/deploy/provider")
def deploy_provider(_user: User = Depends(current_user)) -> dict:
    return {"provider": settings().deploy_provider.split(":", 1)[0] or None, "help": deploy.__doc__}
