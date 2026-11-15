"""Web Push notifications (RFC 8291 aes128gcm payload encryption + RFC 8292 VAPID), built on `cryptography`.

Generate keys once:  uv run python -m app.push keys   -> put the private key in VOX_VAPID_PRIVATE_KEY.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.config import settings
from app.db import PushSubscription, SessionLocal


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _vapid_key() -> ec.EllipticCurvePrivateKey:
    raw = settings().vapid_private_key
    if not raw:
        raise RuntimeError("push is not configured: set VOX_VAPID_PRIVATE_KEY (uv run python -m app.push keys)")
    return ec.derive_private_key(int.from_bytes(unb64(raw), "big"), ec.SECP256R1())


def public_key() -> str:
    return b64(_raw_public(_vapid_key().public_key())) if settings().vapid_private_key else ""


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None,
            server_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291: one aes128gcm record addressed to the browser's subscription keys."""
    ua_public = unb64(p256dh)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = _hkdf(unb64(auth), shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">IB", 4096, len(as_public)) + as_public + ciphertext


def vapid_header(endpoint: str) -> str:
    key = _vapid_key()
    origin = "{0.scheme}://{0.netloc}".format(urlparse(endpoint))
    header = b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64(json.dumps({"aud": origin, "exp": int(time.time()) + 12 * 3600,
                             "sub": settings().vapid_subject}).encode())
    r, s = decode_dss_signature(key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    token = f"{header}.{claims}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={b64(_raw_public(key.public_key()))}"


async def notify(user_id: str, title: str, body: str, url: str = "/") -> int:
    """Push to every browser the user subscribed; drops subscriptions the push service says are gone."""
    if not settings().vapid_private_key:
        return 0
    with SessionLocal() as db:
        subs = list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
        payload = json.dumps({"title": title, "body": body, "url": url}).encode()
        sent = 0
        async with httpx.AsyncClient(timeout=15) as http:
            for sub in subs:
                try:
                    response = await http.post(sub.endpoint, content=encrypt(payload, sub.p256dh, sub.auth), headers={
                        "Authorization": vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
                        "TTL": "86400", "Urgency": "high",
                    })
                except httpx.HTTPError:
                    continue
                if response.status_code in (404, 410):
                    db.delete(sub)
                elif response.status_code < 300:
                    sent += 1
        db.commit()
    return sent


if __name__ == "__main__" and sys.argv[1:] == ["keys"]:
    private = ec.generate_private_key(ec.SECP256R1())
    print("VOX_VAPID_PRIVATE_KEY=" + b64(private.private_numbers().private_value.to_bytes(32, "big")))
    print("public key (sent to browsers automatically): " + b64(_raw_public(private.public_key())))
