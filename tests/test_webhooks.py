from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

import pytest

from reedapt import Reedapt, WebhookVerificationError, construct_webhook_event, verify_webhook

SECRET = "whsec_test"
BODY = json.dumps({"id": "evt_1", "type": "dub.completed", "created_at": "x", "data": {"dub": {"id": "job_1"}}})


def sign(body: str, t: Optional[int] = None, secret: str = SECRET) -> str:
    t = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode(), f"{t}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def test_a_correctly_signed_delivery_verifies_and_parses() -> None:
    event = construct_webhook_event(BODY, sign(BODY), SECRET)
    assert (event["type"], event["id"]) == ("dub.completed", "evt_1")


def test_the_raw_body_can_be_bytes() -> None:
    verify_webhook(BODY.encode(), sign(BODY), SECRET)


def test_the_client_exposes_the_same_check() -> None:
    assert Reedapt(api_key="k").webhooks.construct_event(BODY, sign(BODY), SECRET)["id"] == "evt_1"


def test_a_changed_body_is_refused() -> None:
    with pytest.raises(WebhookVerificationError):
        verify_webhook(BODY.replace("job_1", "job_2"), sign(BODY), SECRET)


def test_re_encoded_json_is_refused() -> None:
    with pytest.raises(WebhookVerificationError):
        verify_webhook(json.dumps(json.loads(BODY), indent=2), sign(BODY), SECRET)


def test_the_wrong_secret_is_refused() -> None:
    with pytest.raises(WebhookVerificationError):
        verify_webhook(BODY, sign(BODY, secret="whsec_other"), SECRET)


def test_an_old_signature_is_refused_within_a_configurable_tolerance() -> None:
    old = int(time.time()) - 600
    with pytest.raises(WebhookVerificationError, match="too old"):
        verify_webhook(BODY, sign(BODY, old), SECRET)
    verify_webhook(BODY, sign(BODY, old), SECRET, tolerance=900)


def test_a_missing_or_malformed_header_is_refused() -> None:
    for header, reason in ((None, "missing"), ("nonsense", "malformed"), ("t=abc,v1=00", "malformed")):
        with pytest.raises(WebhookVerificationError, match=reason):
            verify_webhook(BODY, header, SECRET)


def test_any_one_of_several_v1_signatures_may_match() -> None:
    t, good = sign(BODY).split(",")
    verify_webhook(BODY, f"{t},v1={'0' * 64},{good}", SECRET)
