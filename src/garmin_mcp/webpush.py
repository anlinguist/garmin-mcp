"""Web Push (RFC 8030/8291/8292) for the Rabbit web app.

Payloads are encrypted with aes128gcm (``http_ece``) and authorized with a
VAPID JWT (``py_vapid``); delivery is a plain POST via ``httpx2``.

SSRF guard: subscription endpoints come from the browser, so only https URLs on
known push-service hosts are accepted, at subscribe time and again at send time,
and redirects are never followed.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import http_ece
import httpx2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02

from .appstore import AppStore
from .logs import log_event

logger = logging.getLogger(__name__)

PUSH_HOSTS = (
    "web.push.apple.com",
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
)
PUSH_HOST_SUFFIXES = (".notify.windows.com", ".push.apple.com")
MAX_SUBSCRIPTIONS = 5
MAX_PAYLOAD = 3000  # well under the 4 KB push-service limit after encryption


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def endpoint_allowed(endpoint: str) -> bool:
    try:
        p = urlparse(endpoint)
    except ValueError:
        return False
    host = (p.hostname or "").lower()
    if p.scheme != "https" or p.port not in (None, 443) or p.username or p.password:
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES)


def parse_subscription(body: Any) -> dict[str, str] | None:
    """Validate a browser ``PushSubscription.toJSON()`` payload."""
    if not isinstance(body, dict):
        return None
    endpoint = body.get("endpoint")
    keys = body.get("keys")
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or not isinstance(keys, dict):
        return None
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not isinstance(p256dh, str) or not isinstance(auth, str):
        return None
    try:
        if len(_b64d(p256dh)) != 65 or len(_b64d(auth)) != 16:
            return None
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64d(p256dh))
    except (ValueError, TypeError):
        return None
    if not endpoint_allowed(endpoint):
        return None
    return {"endpoint": endpoint, "p256dh": p256dh, "auth": auth}


@dataclass
class SendResult:
    endpoint_host: str
    status: int | None
    removed: bool = False


class WebPush:
    def __init__(self, store: AppStore, vapid_private_key: str, subject: str,
                 *, http: httpx2.Client | None = None) -> None:
        if not subject.startswith(("mailto:", "https://")):
            raise RuntimeError("VAPID_SUBJECT must be a mailto: or https: URL")
        self._store = store
        self._vapid = Vapid02.from_string(vapid_private_key)
        self._subject = subject
        self._http = http or httpx2.Client(timeout=10, follow_redirects=False)

    @property
    def public_key(self) -> str:
        """applicationServerKey for ``pushManager.subscribe`` (base64url, uncompressed point)."""
        raw = self._vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return _b64e(raw)

    # ------------------------------------------------------- subscriptions --
    def subscribe(self, user_id: str, sub: dict[str, str]) -> int:
        def add(data: dict[str, Any]) -> int:
            subs = [s for s in data.get("subs", []) if s["endpoint"] != sub["endpoint"]]
            subs.append({**sub, "created": int(time.time())})
            data["subs"] = subs[-MAX_SUBSCRIPTIONS:]
            return len(data["subs"])
        return int(self._store.user_doc(user_id, "push").update(add))

    def unsubscribe(self, user_id: str, endpoint: str) -> None:
        def rm(data: dict[str, Any]) -> None:
            data["subs"] = [s for s in data.get("subs", []) if s["endpoint"] != endpoint]
        self._store.user_doc(user_id, "push").update(rm)

    def count(self, user_id: str) -> int:
        return len(self._store.user_doc(user_id, "push").load().get("subs", []))

    # ---------------------------------------------------------------- send --
    def send(self, user_id: str, title: str, body: str, url: str = "/app/",
             ttl: int = 24 * 3600) -> list[SendResult]:
        """Send one notification to every subscription of ``user_id``."""
        payload = json.dumps({"title": title[:120], "body": body[:500], "url": url}).encode()
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("push payload too large")
        results: list[SendResult] = []
        gone: list[str] = []
        for s in self._store.user_doc(user_id, "push").load().get("subs", []):
            host = urlparse(s["endpoint"]).hostname or ""
            if not endpoint_allowed(s["endpoint"]):
                gone.append(s["endpoint"])
                results.append(SendResult(host, None, removed=True))
                continue
            try:
                status = self._post(s, payload, ttl)
            except httpx2.HTTPError as e:
                log_event(logger, logging.WARNING, "push_send_error", user_id=user_id,
                          host=host, error=type(e).__name__)
                results.append(SendResult(host, None))
                continue
            removed = status in (404, 410)
            if removed:
                gone.append(s["endpoint"])
            log_event(logger, logging.INFO if status < 300 else logging.WARNING, "push_sent",
                      user_id=user_id, host=host, status=status, removed=removed)
            results.append(SendResult(host, status, removed))
        for endpoint in gone:
            self.unsubscribe(user_id, endpoint)
        return results

    def _post(self, sub: dict[str, str], payload: bytes, ttl: int) -> int:
        ephemeral = ec.generate_private_key(ec.SECP256R1())
        content = http_ece.encrypt(payload, private_key=ephemeral, dh=_b64d(sub["p256dh"]),
                                   auth_secret=_b64d(sub["auth"]), version="aes128gcm")
        p = urlparse(sub["endpoint"])
        headers = self._vapid.sign({
            "aud": f"{p.scheme}://{p.netloc}",
            "sub": self._subject,
            "exp": int(time.time()) + 12 * 3600,
        })
        headers.update({
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(ttl),
            "Urgency": "normal",
        })
        r = self._http.post(sub["endpoint"], content=content, headers=headers)
        return r.status_code


def generate_vapid_key() -> str:
    """New VAPID private key as base64url raw scalar (store in Secret Manager)."""
    key = ec.generate_private_key(ec.SECP256R1())
    return _b64e(key.private_numbers().private_value.to_bytes(32, "big"))
