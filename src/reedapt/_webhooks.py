from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional, Union

from ._errors import WebhookVerificationError
from .types import WebhookEvent

Payload = Union[str, bytes, bytearray]

__all__ = ["verify_webhook", "construct_webhook_event", "Webhooks"]


def verify_webhook(payload: Payload, header: Optional[str], secret: str, *, tolerance: int = 300) -> None:
    """Checks a delivery's ``Reedapt-Signature`` header against the raw request
    body, exactly as it arrived. Raises ``WebhookVerificationError`` if it
    doesn't match or is older than ``tolerance`` seconds."""
    if not header:
        raise WebhookVerificationError("The Reedapt-Signature header is missing.")
    timestamp = ""
    signatures = []
    for item in header.split(","):
        name, sep, value = item.partition("=")
        if not sep:
            continue
        name, value = name.strip(), value.strip()
        if name == "t":
            timestamp = value
        elif name == "v1":
            signatures.append(value)
    if not timestamp.isdigit() or not signatures:
        raise WebhookVerificationError("The Reedapt-Signature header is malformed.")
    if abs(time.time() - int(timestamp)) > tolerance:
        raise WebhookVerificationError("The signature is too old. Check your server's clock.")
    body = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, given) for given in signatures):
        raise WebhookVerificationError(
            "The signature doesn't match. Use the endpoint's secret and the raw body, not re-encoded JSON."
        )


def construct_webhook_event(payload: Payload, header: Optional[str], secret: str, *, tolerance: int = 300) -> WebhookEvent:
    """Verifies a delivery and returns its parsed event."""
    verify_webhook(payload, header, secret, tolerance=tolerance)
    return json.loads(payload)


class Webhooks:
    def verify(self, payload: Payload, header: Optional[str], secret: str, *, tolerance: int = 300) -> None:
        verify_webhook(payload, header, secret, tolerance=tolerance)

    def construct_event(self, payload: Payload, header: Optional[str], secret: str, *, tolerance: int = 300) -> WebhookEvent:
        return construct_webhook_event(payload, header, secret, tolerance=tolerance)
