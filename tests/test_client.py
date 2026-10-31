from __future__ import annotations

import httpx
import pytest

from reedapt import (
    AsyncReedapt,
    AuthenticationError,
    ConflictError,
    ConnectionError,
    InvalidRequestError,
    RateLimitError,
    Reedapt,
    ReedaptError,
    ServerError,
    TimeoutError,
)

from .conftest import Recorder, answer, api_error

DUB = {"id": "job_1", "status": "queued", "progress": 0, "audio": False, "test": True, "created_at": "x"}


def client(recorder: Recorder, **kwargs: object) -> Reedapt:
    return Reedapt(api_key="rk_test_abc", http_client=httpx.Client(transport=httpx.MockTransport(recorder)), **kwargs)  # type: ignore[arg-type]


def test_refuses_to_start_without_a_key() -> None:
    with pytest.raises(ReedaptError):
        Reedapt()


def test_reads_the_key_and_base_url_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REEDAPT_API_KEY", "rk_test_env")
    monkeypatch.setenv("REEDAPT_BASE_URL", "https://example.test/v1/")
    rec = Recorder(lambda r: answer(200, {"target_languages": ["fr"]}))
    Reedapt(http_client=httpx.Client(transport=httpx.MockTransport(rec))).languages.list()
    assert str(rec.calls[0].url) == "https://example.test/v1/languages"
    assert rec.calls[0].headers["authorization"] == "Bearer rk_test_env"


def test_sends_the_key_a_user_agent_and_json() -> None:
    rec = Recorder(lambda r: answer(201, {"dubs": [DUB], "refused": []}))
    created = client(rec).dubs.create(upload_id="job_1", source_language="en", target_languages=["fr"])
    assert created["dubs"][0]["id"] == "job_1"
    call = rec.calls[0]
    assert call.method == "POST"
    assert str(call.url) == "https://api.reedapt.com/v1/dubs"
    assert call.headers["authorization"] == "Bearer rk_test_abc"
    assert call.headers["user-agent"].startswith("reedapt-python/")
    assert rec.body(0) == {"upload_id": "job_1", "source_language": "en", "target_languages": ["fr"]}


def test_optional_dub_fields_are_sent_only_when_given() -> None:
    rec = Recorder(lambda r: answer(201, {"dubs": []}))
    client(rec).dubs.create(
        upload_id="u", source_language="en", target_languages=["fr"], voice_cloning=False, metadata={"a": "b"}
    )
    assert rec.body(0) == {
        "upload_id": "u",
        "source_language": "en",
        "target_languages": ["fr"],
        "voice_cloning": False,
        "metadata": {"a": "b"},
    }


def test_creating_dubs_carries_an_idempotency_key_the_same_on_every_retry() -> None:
    answers = [api_error(500, "INTERNAL"), answer(201, {"dubs": []})]
    rec = Recorder(lambda r: answers.pop(0))
    client(rec).dubs.create(upload_id="u", source_language="en", target_languages=["fr"])
    keys = [c.headers.get("idempotency-key") for c in rec.calls]
    assert len(keys) == 2 and keys[0] and keys[0] == keys[1]


def test_a_callers_idempotency_key_is_used_as_given() -> None:
    rec = Recorder(lambda r: answer(201, {"dubs": []}))
    client(rec).dubs.create(upload_id="u", source_language="en", target_languages=["fr"], idempotency_key="order-1042")
    assert rec.calls[0].headers["idempotency-key"] == "order-1042"


def test_errors_carry_the_status_code_and_request_id_as_the_right_class() -> None:
    rec = Recorder(lambda r: api_error(401, "UNAUTHORIZED"))
    with pytest.raises(AuthenticationError) as info:
        client(rec).usage.retrieve()
    assert (info.value.status, info.value.code, info.value.request_id) == (401, "UNAUTHORIZED", "req_test")
    assert str(info.value).startswith("UNAUTHORIZED: ")


def test_validation_errors_list_their_fields() -> None:
    body = {
        "error": {
            "code": "INVALID_REQUEST",
            "message": "Some fields are missing or invalid.",
            "request_id": "req_1",
            "fields": [{"field": "target_languages", "message": "Field required"}],
        }
    }
    rec = Recorder(lambda r: answer(422, body))
    with pytest.raises(InvalidRequestError) as info:
        client(rec).dubs.create(upload_id="u", source_language="en", target_languages=[])
    assert info.value.fields == [{"field": "target_languages", "message": "Field required"}]
    assert len(rec.calls) == 1


def test_a_rate_limit_waits_for_retry_after_then_retries() -> None:
    answers = [api_error(429, "RATE_LIMITED", {"retry-after": "0"}), answer(200, {"target_languages": []})]
    rec = Recorder(lambda r: answers.pop(0))
    client(rec).languages.list()
    assert len(rec.calls) == 2


def test_limits_that_last_longer_than_a_minute_are_not_retried() -> None:
    rec = Recorder(lambda r: api_error(429, "TOO_MANY_DUBS_RUNNING", {"retry-after": "0"}))
    with pytest.raises(RateLimitError) as info:
        client(rec).dubs.create(upload_id="u", source_language="en", target_languages=["fr"])
    assert info.value.retry_after == 0
    assert len(rec.calls) == 1


def test_server_errors_are_retried_up_to_max_retries_then_raised() -> None:
    rec = Recorder(lambda r: api_error(502, "BAD_GATEWAY"))
    with pytest.raises(ServerError):
        client(rec, max_retries=1).languages.list()
    assert len(rec.calls) == 2


def test_a_network_failure_is_retried_for_safe_requests() -> None:
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return answer(200, {"target_languages": []})

    rec = Recorder(handler)
    client(rec).languages.list()
    assert len(rec.calls) == 2


def test_adding_a_webhook_endpoint_is_not_retried_after_a_network_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    rec = Recorder(handler)
    with pytest.raises(ConnectionError):
        client(rec).webhook_endpoints.create(url="https://example.com/hook")
    assert len(rec.calls) == 1


def test_a_timeout_raises_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(TimeoutError):
        client(Recorder(handler), max_retries=0).languages.list()


def test_cancel_and_delete_return_none_on_204() -> None:
    rec = Recorder(lambda r: httpx.Response(204))
    reedapt = client(rec)
    assert reedapt.dubs.cancel("job_1") is None
    assert reedapt.webhook_endpoints.delete("we_1") is None
    assert str(rec.calls[0].url).endswith("/dubs/job_1/cancel")
    assert rec.calls[1].method == "DELETE"


def test_ids_are_escaped_in_paths() -> None:
    rec = Recorder(lambda r: answer(200, DUB))
    client(rec).dubs.retrieve("../usage")
    assert rec.calls[0].url.raw_path == b"/v1/dubs/..%2Fusage"


def test_list_all_walks_every_page_and_skips_repeats() -> None:
    pages = {
        "0": {"data": [{**DUB, "id": "a"}, {**DUB, "id": "b"}], "has_more": True},
        "2": {"data": [{**DUB, "id": "b"}, {**DUB, "id": "c"}], "has_more": False},
    }
    rec = Recorder(lambda r: answer(200, pages[r.url.params["offset"]]))
    assert [d["id"] for d in client(rec).dubs.list_all(page_size=2)] == ["a", "b", "c"]
    assert rec.calls[0].url.params["limit"] == "2"


def test_wait_until_done_polls_until_the_dub_is_final() -> None:
    statuses = ["queued", "processing", "completed"]
    rec = Recorder(lambda r: answer(200, {**DUB, "status": statuses.pop(0)}))
    seen: list = []
    dub = client(rec).dubs.wait_until_done("job_1", poll_interval=0, on_update=lambda d: seen.append(d["status"]))
    assert dub["status"] == "completed"
    assert seen == ["queued", "processing", "completed"]


async def test_the_async_client_makes_the_same_requests() -> None:
    answers = [api_error(503, "UNAVAILABLE"), answer(201, {"dubs": [DUB]})]
    rec = Recorder(lambda r: answers.pop(0))
    async with AsyncReedapt(
        api_key="rk_test_abc", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec))
    ) as reedapt:
        created = await reedapt.dubs.create(upload_id="u", source_language="en", target_languages=["fr"])
    assert created["dubs"][0]["id"] == "job_1"
    assert len(rec.calls) == 2
    assert rec.calls[0].headers["idempotency-key"] == rec.calls[1].headers["idempotency-key"]


async def test_async_list_all_and_errors() -> None:
    rec = Recorder(
        lambda r: (
            answer(200, {"data": [DUB], "has_more": False})
            if r.url.path.endswith("/dubs")
            else api_error(401, "UNAUTHORIZED")
        )
    )
    reedapt = AsyncReedapt(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    assert [d["id"] async for d in reedapt.dubs.list_all()] == ["job_1"]
    with pytest.raises(AuthenticationError):
        await reedapt.usage.retrieve()


def test_usage_report_asks_for_the_month_given() -> None:
    rec = Recorder(lambda r: answer(200, {"month": "2026-09", "minutes": 1, "keys": [], "languages": []}))
    reedapt = client(rec)
    assert reedapt.usage.report(month="2026-09")["month"] == "2026-09"
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/usage/report?month=2026-09"
    reedapt.usage.report()
    assert str(rec.calls[1].url) == "https://api.reedapt.com/v1/usage/report"


def test_transcript_returns_the_lines() -> None:
    line = {"start_ms": 1200, "end_ms": 3850, "speaker": "SPEAKER_00", "original_text": "Hi.", "text": "Salut."}
    rec = Recorder(lambda r: answer(200, {"dub_id": "job_1", "lines": [line]}))
    assert client(rec).dubs.transcript("job_1")["lines"] == [line]
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/dubs/job_1/transcript"


def test_subtitles_return_the_file_text() -> None:
    vtt = "WEBVTT\n\n00:00:01.200 --> 00:00:03.850\nSalut.\n"
    rec = Recorder(lambda r: httpx.Response(200, text=vtt, headers={"content-type": "text/vtt; charset=utf-8"}))
    reedapt = client(rec)
    assert reedapt.dubs.subtitles("job_1", format="vtt", language="source") == vtt
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/dubs/job_1/subtitles?format=vtt&language=source"
    reedapt.dubs.subtitles("job_1")
    assert str(rec.calls[1].url) == "https://api.reedapt.com/v1/dubs/job_1/subtitles?format=srt&language=target"


def test_subtitles_of_an_unfinished_dub_raise_a_conflict() -> None:
    rec = Recorder(lambda r: api_error(409, "DUB_NOT_READY"))
    with pytest.raises(ConflictError) as info:
        client(rec).dubs.subtitles("job_1")
    assert info.value.code == "DUB_NOT_READY"


def test_resending_a_delivery_is_not_retried_after_a_network_failure() -> None:
    delivery = {
        "id": "whd_1",
        "event_id": "evt_1",
        "event_type": "dub.completed",
        "status": "pending",
        "attempts": 0,
        "created_at": "x",
    }
    rec = Recorder(lambda r: answer(201, delivery))
    assert client(rec).webhook_endpoints.resend_delivery("we_1", "whd_1") == delivery
    assert rec.calls[0].method == "POST"
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/webhook_endpoints/we_1/deliveries/whd_1/resend"

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    down = Recorder(refuse)
    with pytest.raises(ConnectionError):
        client(down).webhook_endpoints.resend_delivery("we_1", "whd_1")
    assert len(down.calls) == 1


async def test_async_transcript_subtitles_and_resend() -> None:
    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/subtitles"):
            return httpx.Response(200, text="1\n00:00:01,200 --> 00:00:03,850\nSalut.\n")
        if r.url.path.endswith("/resend"):
            return answer(201, {"id": "whd_1"})
        return answer(200, {"dub_id": "job_1", "lines": []})

    rec = Recorder(handle)
    reedapt = AsyncReedapt(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    assert (await reedapt.dubs.transcript("job_1"))["lines"] == []
    assert (await reedapt.dubs.subtitles("job_1")).startswith("1\n")
    assert (await reedapt.webhook_endpoints.resend_delivery("we_1", "whd_1"))["id"] == "whd_1"
