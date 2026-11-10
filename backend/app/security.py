import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import UsageEvent, User, get_db, now


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt = bytes.fromhex(stored.split("$")[0])
    return hmac.compare_digest(hash_password(password, salt), stored)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def make_access_token(user_id: str) -> str:
    claims = {"sub": user_id, "exp": int(time.time()) + settings().access_ttl_s}
    return jwt.encode(claims, settings().secret_key, algorithm="HS256")


def user_from_token(token: str, db: Session) -> User:
    try:
        user_id = jwt.decode(token, settings().secret_key, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token") from None
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(401, "unknown user")
    return user


def current_user(authorization: str = Header(""), db: Session = Depends(get_db)) -> User:
    return user_from_token(authorization.removeprefix("Bearer "), db)


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "admin only")
    return user


# shortcut: in-memory limiter is per process, move to Redis when running more than one worker.
_hits: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(request: Request) -> None:
    key = f"{request.client.host if request.client else '?'}:{request.url.path}"
    hits, t = _hits[key], time.monotonic()
    while hits and hits[0] < t - 60:
        hits.popleft()
    if len(hits) >= settings().auth_rate_per_min:
        raise HTTPException(429, "too many requests, try again in a minute")
    hits.append(t)


def check_daily_limit(db: Session, user: User, kind: str, limit: int) -> None:
    """Free tier gets `limit` events of `kind` per rolling day; paid tier is unlimited."""
    if user.tier != "free":
        return
    since = now() - timedelta(days=1)
    used = db.scalar(
        select(func.count()).where(UsageEvent.user_id == user.id, UsageEvent.kind == kind, UsageEvent.ts > since)
    )
    if (used or 0) >= limit:
        raise HTTPException(429, f"daily free limit reached for {kind}")
