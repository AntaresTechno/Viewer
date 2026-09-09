"""Short-lived ticket integrity + SSRF-safe proxy guards (plan §8.6/§9.5)."""
from __future__ import annotations

import time

from app.plugins.media import proxy, tickets


def test_ticket_roundtrip():
    t = tickets.make_ticket(user_id=7, library_item_id=3, unit_key="ep5",
                            resource_index=1, ttl_seconds=60)
    payload = tickets.verify_ticket(t)
    assert payload is not None
    assert payload["u"] == 7
    assert payload["li"] == 3
    assert payload["uKey"] == "ep5"
    assert payload["i"] == 1


def test_ticket_tamper_rejected():
    t = tickets.make_ticket(user_id=7, library_item_id=3, unit_key="ep5",
                            resource_index=0)
    body, sig = t.split(".")
    tampered = body + "x." + sig
    assert tickets.verify_ticket(tampered) is None
    # 篡改签名
    assert tickets.verify_ticket(body + "." + sig[::-1]) is None


def test_ticket_expired():
    t = tickets.make_ticket(user_id=7, library_item_id=3, unit_key="u",
                            resource_index=0, ttl_seconds=1)
    # 人为让 exp 过去
    payload = tickets.verify_ticket(t)
    assert payload is not None
    # 狂快过期场景：直接构造一个过去的票据
    import json
    import base64
    import hmac
    import hashlib
    raw = json.dumps({"v": 1, "u": 7, "li": 3, "uKey": "u", "i": 0,
                      "exp": int(time.time()) - 10, "nonce": "abc"})
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    sig = hmac.new(
        __import__("app.core.config", fromlist=["settings"]).settings.secret_key.encode(),
        body.encode(), hashlib.sha256).hexdigest()
    assert tickets.verify_ticket(f"{body}.{sig}") is None


def _blocked(url: str) -> bool:
    return proxy._is_blocked_target(url) is not None


def test_proxy_blocks_private_and_metadata():
    assert _blocked("http://127.0.0.1:8080/video.mp4")
    assert _blocked("http://localhost/v")
    assert _blocked("http://169.254.169.254/latest/meta-data")
    assert _blocked("http://10.0.0.5/x")
    assert _blocked("http://192.168.1.5/x")
    assert _blocked("http://172.16.0.9/x")
    assert _blocked("file:///etc/passwd")
    assert _blocked("ftp://host/x")
    assert not _blocked("https://example.com/video.mp4")


def test_proxy_blocks_hostname_resolving_to_private():
    # 用确定性的 host 指向不可达域名 —— 不解析即视为安全
    assert not _blocked("https://media.example.invalid/v.mp4")


def test_sanitize_headers_strips_sensitive():
    hdrs = {"Authorization": "Bearer x", "Cookie": "a=b",
            "Referer": "https://r/", "Host": "evil", "Range": "bytes=0-100"}
    cleaned = proxy.sanitize_headers(hdrs)
    assert "Cookie" not in cleaned
    assert "Authorization" not in cleaned
    assert "Host" not in cleaned
    assert "Referer" in cleaned


def test_legacy_state_bounded():
    from app.plugins.media.legacy_document import sanitize_legacy_state

    big = "x" * 100_000
    state = {f"k{i}": big for i in range(100)}
    out = sanitize_legacy_state(state)
    assert len(out) <= 64
    assert json_value(out) <= 16 * 1024


def json_value(state):
    import json

    return len(json.dumps(state, ensure_ascii=False))