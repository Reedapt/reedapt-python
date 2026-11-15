from __future__ import annotations

import httpx
import pytest

from reedapt import (
    APIError,
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


def test_create_dubs_passes_instructions_through() -> None:
    rec = Recorder(lambda r: answer(201, {"dubs": []}))
    client(rec).dubs.create(
        upload_id="upl_1", source_language="en", target_languages=["fr"], instructions="Keep the name Adaeze."
    )
    assert rec.body(0)["instructions"] == "Keep the name Adaeze."
    client(rec).dubs.create(upload_id="upl_1", source_language="en", target_languages=["fr"])
    assert "instructions" not in rec.body(1)


def test_quality_returns_the_report() -> None:
    quality = {
        "dub_id": "job_1",
        "checked": True,
        "listened": True,
        "lines": 40,
        "fixed": 2,
        "to_review": 1,
        "flagged": [{"line": 3, "start_ms": 9000, "checks": ["fit"], "fixed": "shortened"}],
    }
    rec = Recorder(lambda r: answer(200, quality))
    assert client(rec).dubs.quality("job_1") == quality
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/dubs/job_1/quality"


def test_requests_create_defaults_to_ask_and_is_not_retried_after_a_network_failure() -> None:
    run = {
        "id": "run_1",
        "dub_id": "job_1",
        "status": "done",
        "request": "Natural?",
        "reply": "Yes.",
        "created_at": "x",
    }
    rec = Recorder(lambda r: answer(200, run))
    assert client(rec).dubs.requests.create("job_1", text="Natural?") == run
    assert rec.calls[0].method == "POST"
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/dubs/job_1/requests"
    assert rec.body(0) == {"text": "Natural?", "autonomy": "ask"}
    assert "idempotency-key" not in rec.calls[0].headers
    client(rec).dubs.requests.create("job_1", text="Fix it.", autonomy="decide")
    assert rec.body(1)["autonomy"] == "decide"

    def down(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    failing = Recorder(down)
    with pytest.raises(ConnectionError):
        client(failing).dubs.requests.create("job_1", text="x")
    assert len(failing.calls) == 1


def test_a_request_that_is_not_released_yet_raises_not_available() -> None:
    rec = Recorder(lambda r: api_error(403, "NOT_AVAILABLE"))
    with pytest.raises(APIError) as info:
        client(rec).dubs.requests.create("job_1", text="x")
    assert info.value.status == 403
    assert info.value.code == "NOT_AVAILABLE"


def test_runs_retrieve_and_answer() -> None:
    run = {
        "id": "run_1",
        "dub_id": "job_1",
        "status": "needs_input",
        "request": "Calmer.",
        "reply": "I can soften line 3.",
        "proposal": {"kind": "edit_lines", "summary": "Soften line 3", "lines": 1},
        "options": [{"id": "apply", "label": "Apply"}, {"id": "decline", "label": "Decline"}],
        "created_at": "x",
    }
    rec = Recorder(lambda r: answer(200, run if r.method == "GET" else {**run, "status": "declined"}))
    reedapt = client(rec)
    assert reedapt.runs.retrieve("run_1") == run
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/runs/run_1"
    assert reedapt.runs.answer("run_1", choice="decline")["status"] == "declined"
    assert rec.calls[1].method == "POST"
    assert str(rec.calls[1].url) == "https://api.reedapt.com/v1/runs/run_1/answer"
    assert rec.body(1) == {"choice": "decline"}


def test_lines_list_and_edit() -> None:
    line = {
        "line": 3,
        "start_ms": 1,
        "end_ms": 2,
        "speaker": "SPEAKER_00",
        "original_text": "Hi.",
        "text": "Salut.",
        "delivery_style": None,
        "voice_instructions": None,
    }
    rec = Recorder(
        lambda r: (
            answer(200, {"dub_id": "job_1", "lines": [line]})
            if r.method == "GET"
            else answer(202, {"dub_id": "job_1", "lines": [3], "status": "revoicing"})
        )
    )
    reedapt = client(rec)
    assert reedapt.dubs.lines.list("job_1")["lines"] == [line]
    assert str(rec.calls[0].url) == "https://api.reedapt.com/v1/dubs/job_1/lines"
    assert reedapt.dubs.lines.edit("job_1", lines=[{"line": 3, "text": "Bonjour."}])["status"] == "revoicing"
    assert str(rec.calls[1].url) == "https://api.reedapt.com/v1/dubs/job_1/lines/edit"
    assert rec.body(1) == {"lines": [{"line": 3, "text": "Bonjour."}]}


async def test_async_quality_requests_runs_and_lines() -> None:
    def handle(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/quality"):
            return answer(200, {"dub_id": "job_1", "checked": False})
        if r.url.path.endswith("/lines/edit"):
            return answer(202, {"dub_id": "job_1", "lines": [3], "status": "revoicing"})
        if r.url.path.endswith("/lines"):
            return answer(200, {"dub_id": "job_1", "lines": []})
        return answer(200, {"id": "run_1", "status": "done"})

    rec = Recorder(handle)
    reedapt = AsyncReedapt(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    assert (await reedapt.dubs.quality("job_1"))["checked"] is False
    assert (await reedapt.dubs.requests.create("job_1", text="x"))["id"] == "run_1"
    assert (await reedapt.runs.answer("run_1", choice="apply"))["id"] == "run_1"
    assert (await reedapt.runs.retrieve("run_1"))["id"] == "run_1"
    assert (await reedapt.dubs.lines.list("job_1"))["lines"] == []
    assert (await reedapt.dubs.lines.edit("job_1", lines=[{"line": 3, "text": "x"}]))["status"] == "revoicing"
