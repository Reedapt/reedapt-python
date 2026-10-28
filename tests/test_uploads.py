from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Optional

import httpx
import pytest

from reedapt import AsyncReedapt, Reedapt, UploadError, WaitError

from .conftest import Recorder, answer, upload_server


def sync(rec: Recorder) -> Reedapt:
    return Reedapt(api_key="rk_test_x", http_client=httpx.Client(transport=httpx.MockTransport(rec)))


def puts(rec: Recorder) -> list:
    return [c for c in rec.calls if c.method == "PUT"]


def complete_body(rec: Recorder) -> dict:
    return json.loads(next(c for c in rec.calls if c.url.path.endswith("/complete")).content)


def test_uploads_bytes_in_parts_and_completes_with_every_etag_in_order() -> None:
    rec = upload_server(10, 4)
    progress: list = []
    upload = sync(rec).uploads.upload_file(b"0123456789", filename="talk.mp4", on_progress=lambda d, t: progress.append((d, t)))
    assert upload["status"] == "uploaded"
    assert rec.body(0) == {"filename": "talk.mp4", "size": 10}
    assert rec.calls[0].headers.get("idempotency-key")
    assert sorted(c.content for c in puts(rec)) == [b"0123", b"4567", b"89"]
    for put in puts(rec):
        assert "authorization" not in put.headers, "no API key on storage links"
    assert complete_body(rec) == {
        "parts": [{"part_number": n, "etag": f'"etag-{n}"'} for n in (1, 2, 3)]
    }
    assert progress[-1] == (10, 10)


def test_uploads_a_file_from_disk_named_after_the_file(tmp_path: Path) -> None:
    path = tmp_path / "sermon.mp3"
    path.write_bytes(b"abcdefghi")
    rec = upload_server(9, 4)
    sync(rec).uploads.upload_file(path)
    assert rec.body(0) == {"filename": "sermon.mp3", "size": 9}
    assert b"".join(c.content for c in sorted(puts(rec), key=lambda c: str(c.url))) == b"abcdefghi"


def test_uploads_an_open_file_object() -> None:
    rec = upload_server(5, 2)
    sync(rec).uploads.upload_file(io.BytesIO(b"hello"), filename="a.wav")
    assert sorted(c.content for c in puts(rec)) == [b"he", b"ll", b"o"]


def test_signs_the_parts_the_first_answer_didnt_include_a_batch_at_a_time() -> None:
    rec = upload_server(250, 1, first_links=100)
    sync(rec).uploads.upload_file(bytes(250), filename="a.mp4", concurrency=8)
    signs = [json.loads(c.content)["part_numbers"] for c in rec.calls if c.url.path.endswith("/parts")]
    assert [(s[0], s[-1]) for s in signs] == [(101, 200), (201, 250)]
    assert len(puts(rec)) == 250


def test_an_expired_link_is_signed_again_and_the_part_resent() -> None:
    state = {"refused": False}

    def storage(request: httpx.Request) -> Optional[httpx.Response]:
        if str(request.url).endswith("/2") and not state["refused"]:
            state["refused"] = True
            return httpx.Response(403, text="<Error>Request has expired</Error>")
        return None

    rec = upload_server(8, 4, storage=storage)
    assert sync(rec).uploads.upload_file(bytes(8), filename="a.mp4")["status"] == "uploaded"
    resign = [json.loads(c.content) for c in rec.calls if c.url.path.endswith("/parts")]
    assert resign == [{"part_numbers": [2]}]


def test_a_part_that_keeps_failing_stops_the_upload() -> None:
    rec = upload_server(8, 4, storage=lambda r: httpx.Response(500) if str(r.url).endswith("/1") else None)
    with pytest.raises(UploadError):
        sync(rec).uploads.upload_file(bytes(8), filename="a.mp4", concurrency=1)
    assert not any(c.url.path.endswith("/complete") for c in rec.calls)


def test_bytes_need_a_filename() -> None:
    with pytest.raises(UploadError):
        sync(upload_server(1, 1)).uploads.upload_file(b"x")


def test_from_url_waits_until_the_download_is_done() -> None:
    statuses = ["fetching", "uploaded"]
    base = {"id": "job_u", "filename": "a.mp4", "size": 5, "part_size": 0, "part_count": 0}
    rec = Recorder(lambda r: answer(202, {**base, "status": "fetching"}) if r.method == "POST" else answer(200, {**base, "status": statuses.pop(0)}))
    upload = sync(rec).uploads.from_url("https://example.com/a.mp4", poll_interval=0)
    assert upload["status"] == "uploaded"
    assert rec.body(0) == {"source_url": "https://example.com/a.mp4"}
    assert len(rec.calls) == 3


def test_from_url_raises_when_the_download_failed() -> None:
    base = {"id": "job_u", "filename": "", "size": 0, "part_size": 0, "part_count": 0, "error": "SOURCE_URL_TOO_SLOW"}
    rec = Recorder(lambda r: answer(202, {**base, "status": "fetching"}) if r.method == "POST" else answer(200, {**base, "status": "failed"}))
    with pytest.raises(WaitError, match="SOURCE_URL_TOO_SLOW"):
        sync(rec).uploads.from_url("https://example.com/a.mp4", poll_interval=0)


def test_dub_uploads_and_starts_the_dubs_in_one_call() -> None:
    rec = upload_server(4, 4)
    created = sync(rec).dub(
        b"abcd", filename="talk.mp4", source_language="en", target_languages=["fr", "es"], idempotency_key="order-7"
    )
    assert created["dubs"][0]["id"] == "job_d"
    dubs = next(c for c in rec.calls if c.url.path.endswith("/v1/dubs"))
    assert json.loads(dubs.content) == {"upload_id": "job_up", "source_language": "en", "target_languages": ["fr", "es"]}
    assert dubs.headers["idempotency-key"] == "order-7:dubs"
    assert rec.calls[0].headers["idempotency-key"] == "order-7:upload"


def test_dub_with_a_link_has_reedapt_fetch_it() -> None:
    base = {"id": "job_up", "filename": "a.mp4", "size": 5, "part_size": 0, "part_count": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/dubs"):
            return answer(201, {"dubs": [{"id": "job_d"}]})
        return answer(202 if r.method == "POST" else 200, {**base, "status": "fetching" if r.method == "POST" else "uploaded"})

    rec = Recorder(handler)
    reedapt = sync(rec)
    created = reedapt.dub("https://example.com/a.mp4", source_language="en", target_languages=["fr"])
    assert created["dubs"][0]["id"] == "job_d"
    assert rec.body(0) == {"source_url": "https://example.com/a.mp4"}


async def test_the_async_upload_sends_every_part_and_completes() -> None:
    rec = upload_server(250, 1, first_links=100)
    async with AsyncReedapt(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec))) as reedapt:
        progress: list = []
        upload = await reedapt.uploads.upload_file(bytes(250), filename="a.mp4", concurrency=6, on_progress=lambda d, t: progress.append(d))
    assert upload["status"] == "uploaded"
    assert len(puts(rec)) == 250
    assert [p["part_number"] for p in complete_body(rec)["parts"]] == list(range(1, 251))
    assert progress[-1] == 250
    signs = [json.loads(c.content)["part_numbers"] for c in rec.calls if c.url.path.endswith("/parts")]
    assert [(s[0], s[-1]) for s in signs] == [(101, 200), (201, 250)]


async def test_an_async_part_that_keeps_failing_stops_the_upload() -> None:
    rec = upload_server(8, 4, storage=lambda r: httpx.Response(500) if str(r.url).endswith("/1") else None)
    reedapt = AsyncReedapt(api_key="k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(rec)))
    with pytest.raises(UploadError):
        await reedapt.uploads.upload_file(bytes(8), filename="a.mp4", concurrency=1)
    assert not any(c.url.path.endswith("/complete") for c in rec.calls)
