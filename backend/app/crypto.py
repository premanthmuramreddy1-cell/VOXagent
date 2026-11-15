"""Encrypted per-user secrets: OAuth tokens, the GitHub token, the form-filling profile.

Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from VOX_SECRET_KEY by HKDF, so the key
lives only in the environment and rotating VOX_SECRET_KEY makes old blobs unreadable by design.
"""

import base64
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Secret, now


@lru_cache
def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"voxagent-secrets-v1", info=b"fernet").derive(
        settings().secret_key.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(value: Any) -> str:
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def decrypt(blob: str) -> Any:
    try:
        return json.loads(_fernet().decrypt(blob.encode()))
    except InvalidToken:
        raise ValueError("stored secret cannot be decrypted (was VOX_SECRET_KEY changed?)") from None


def save_secret(db: Session, user_id: str, kind: str, value: Any) -> None:
    row = db.scalar(select(Secret).where(Secret.user_id == user_id, Secret.kind == kind))
    if row is None:
        row = Secret(user_id=user_id, kind=kind, blob="")
        db.add(row)
    row.blob, row.updated_at = encrypt(value), now()
    db.commit()


def load_secret(db: Session, user_id: str, kind: str) -> Any:
    row = db.scalar(select(Secret).where(Secret.user_id == user_id, Secret.kind == kind))
    return decrypt(row.blob) if row else None


def delete_secret(db: Session, user_id: str, kind: str) -> bool:
    row = db.scalar(select(Secret).where(Secret.user_id == user_id, Secret.kind == kind))
    if row:
        db.delete(row)
        db.commit()
    return row is not None
