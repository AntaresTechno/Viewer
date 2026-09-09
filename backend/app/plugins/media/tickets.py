"""Short-lived media resource tickets (短期资源票据).

Used to expose real upstream stream/image URLs to the browser without putting
them in the DB or embedding long-lived JWTs into resource URLs. ``/resource/``
endpoint validates the ticket (no Authorization header needed for <video>/<img>),
then re-checks user & item ownership.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from ...core.config import settings


def _sign(message: str) -> str:
    return hmac.new(
        settings.secret_key.encode(), message.encode(), hashlib.sha256
    ).hexdigest()


def make_ticket(*, user_id: int, library_item_id: int, unit_key: str,
                resource_index: int, ttl_seconds: int = 300) -> str:
    payload = {
        "v": 1,
        "u": user_id,
        "li": library_item_id,
        "uKey": unit_key,
        "i": resource_index,
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    raw = json.dumps(payload, separators=(",", ":"))
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return f"{body}.{_sign(body)}"


def verify_ticket(ticket: str) -> dict | None:
    """Return validated payload dict or None (tampered/expired)."""
    if "." not in ticket:
        return None
    body, sig = ticket.split(".", 1)
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    if payload.get("v") != 1 or int(payload.get("exp", 0)) < time.time():
        return None
    return payload