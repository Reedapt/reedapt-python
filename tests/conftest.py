from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import httpx
import pytest

from reedapt import _base

API = "https://api.reedapt.com/v1"
STORAGE = "https://storage.test/"


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_base, "backoff", lambda attempt: 0.0)
    monkeypatch.delenv("REEDAPT_API_KEY", raising=False)
    monkeypatch.delenv("REEDAPT_BASE_URL", raising=False)


class Recorder:
    """Answers requests from `handler` and records them."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.calls: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.calls.append(request)
        return self.handler(request)

    def body(self, i: int) -> Any:
        return json.loads(self.calls[i].content)


def answer(status: int, body: Any = None, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers) if body is not None else httpx.Response(status, headers=headers)


def api_error(status: int, code: str, headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return answer(status, {"error": {"code": code, "message": f"{code} happened", "request_id": "req_test"}}, headers)


def upload_server(size: int, part_size: int, *, first_links: int = 100, storage: Optional[Callable[[httpx.Request], Optional[httpx.Response]]] = None) -> Recorder:
    """A fake API plus storage for one upload of `size` bytes in parts of `part_size`."""
    part_count = -(-size // part_size)
    upload = {"id": "job_up", "filename": "talk.mp4", "size": size, "part_size": part_size, "part_count": part_count}

    def link(n: int) -> Dict[str, Any]:
        return {"part_number": n, "url": f"{STORAGE}part/{n}"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(STORAGE):
            custom = storage(request) if storage else None
            if custom is not None:
                return custom
            return httpx.Response(200, headers={"etag": f'"etag-{url.rsplit("/", 1)[1]}"'})
        path = url.replace(API, "")
        if path == "/uploads":
            return answer(201, {**upload, "status": "uploading", "parts": [link(n) for n in range(1, min(first_links, part_count) + 1)]})
        if path == "/uploads/job_up/parts":
            return answer(200, {"parts": [link(n) for n in json.loads(request.content)["part_numbers"]]})
        if path in ("/uploads/job_up/complete", "/uploads/job_up"):
            return answer(200, {**upload, "status": "uploaded"})
        if path == "/dubs":
            return answer(201, {"dubs": [{"id": "job_d"}], "refused": []})
        return api_error(404, "NOT_FOUND")

    return Recorder(handler)
