"""What the sync and async clients share: settings, headers, retries, errors
and reading the file being uploaded."""

from __future__ import annotations

import io
import os
import random
import threading
import uuid
from typing import IO, Any, Dict, Mapping, Optional, Union

import httpx
from typing_extensions import Literal, TypedDict

from ._errors import APIError, ConnectionError, ReedaptError, UploadError, error_for
from ._version import __version__

DEFAULT_BASE_URL = "https://api.reedapt.com/v1"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 2
SIGN_BATCH = 100
PART_ATTEMPTS = 4

# `safe` requests can be sent again after any failure without doing the work
# twice. `unsafe` ones are only retried when the API refused them before acting.
Retry = Literal["safe", "unsafe"]

FileInput = Union[str, "os.PathLike[str]", bytes, bytearray, memoryview, IO[bytes]]


class RequestOptions(TypedDict, total=False):
    idempotency_key: str
    """Sent as ``Idempotency-Key``. Creating uploads and dubs gets one automatically."""
    timeout: float
    max_retries: int
    headers: Mapping[str, str]


class Settings:
    def __init__(
        self,
        api_key: Optional[str],
        base_url: Optional[str],
        timeout: float,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
    ) -> None:
        api_key = api_key or os.environ.get("REEDAPT_API_KEY")
        if not api_key:
            raise ReedaptError("No API key. Pass api_key= or set the REEDAPT_API_KEY environment variable.")
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("REEDAPT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.default_headers = dict(default_headers or {})

    def headers(self, has_body: bool, idempotency_key: Optional[str], options: RequestOptions) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"reedapt-python/{__version__}",
            **self.default_headers,
            **dict(options.get("headers") or {}),
            "Authorization": f"Bearer {self.api_key}",
        }
        if has_body:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers


def idempotency_key_for(idempotent: bool, options: RequestOptions) -> Optional[str]:
    return options.get("idempotency_key") or (str(uuid.uuid4()) if idempotent else None)


def parse(response: httpx.Response, text: bool = False) -> Any:
    if response.is_success:
        if text:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        return response.json()
    try:
        body = response.json().get("error")
    except (ValueError, AttributeError):
        body = None
    raise error_for(response.status_code, body if isinstance(body, dict) else None, response.headers)


def connection_error(method: str, path: str, error: httpx.TransportError, timeout: float) -> ConnectionError:
    from ._errors import TimeoutError

    if isinstance(error, httpx.TimeoutException):
        return TimeoutError(f"{method} {path} took longer than {timeout} seconds.")
    return ConnectionError(f"{method} {path} failed: {error}")


def should_retry(error: ReedaptError, retry: Retry) -> bool:
    if isinstance(error, ConnectionError):
        return retry == "safe"
    if not isinstance(error, APIError):
        return False
    # These 429s are limits that last minutes or hours, not a busy minute.
    if error.status == 429:
        return error.code == "RATE_LIMITED"
    if error.status == 503:
        return True
    if retry == "unsafe":
        return False
    if error.status == 409:
        return error.code == "IDEMPOTENCY_IN_PROGRESS"
    return error.status in (408, 500, 502, 504)


def backoff(attempt: int) -> float:
    return min(0.5 * 2**attempt, 8.0) * (0.75 + random.random() * 0.25)


def retry_delay(error: ReedaptError, attempt: int) -> float:
    if isinstance(error, APIError):
        try:
            seconds = float(error.headers.get("retry-after", ""))
        except ValueError:
            seconds = -1
        if 0 <= seconds <= 60:
            return seconds
    return backoff(attempt)


def part_range(n: int, part_size: int, size: int) -> tuple[int, int]:
    start = (n - 1) * part_size
    return start, min(start + part_size, size)


def sign_batch(n: int, part_count: int, have: Mapping[int, str]) -> list[int]:
    first = ((n - 1) // SIGN_BATCH) * SIGN_BATCH + 1
    last = min(first + SIGN_BATCH - 1, part_count)
    return [p for p in range(first, last + 1) if p not in have]


def part_outcome(n: int, response: httpx.Response) -> tuple[Optional[str], bool, str]:
    """For a part's PUT: its ETag if it worked, whether to sign the link again,
    and what went wrong. A 4xx other than an expired link is final."""
    if response.is_success:
        etag = response.headers.get("etag")
        if not etag:
            raise UploadError(f"Storage accepted part {n} but sent no ETag.")
        return etag, False, ""
    problem = f"storage answered {response.status_code}"
    if response.status_code in (400, 403):
        return None, True, problem
    if response.status_code < 500 and response.status_code not in (408, 429):
        raise UploadError(f"Part {n} failed: {problem}.")
    return None, False, problem


class Source:
    """The file being uploaded, readable by part from several threads."""

    def __init__(self, file: FileInput, filename: Optional[str]) -> None:
        self._owned: Optional[IO[bytes]] = None
        self._bytes: Optional[memoryview] = None
        self._file: Optional[IO[bytes]] = None
        self._lock = threading.Lock()
        if isinstance(file, (bytes, bytearray, memoryview)):
            if not filename:
                raise UploadError("Pass filename=, with its extension, when uploading bytes.")
            self._bytes = memoryview(file).cast("B")
            self.size = self._bytes.nbytes
            self.filename = filename
        elif isinstance(file, (str, os.PathLike)):
            path = os.fspath(file)
            self._owned = self._file = open(path, "rb")  # noqa: SIM115 - read by part from threads; closed by close()
            self.size = os.fstat(self._owned.fileno()).st_size
            self.filename = filename or os.path.basename(path)
        elif hasattr(file, "read") and hasattr(file, "seek"):
            self._file = file
            start = file.tell()
            file.seek(0, io.SEEK_END)
            self.size = file.tell() - start
            self._offset = start
            name = filename or os.path.basename(str(getattr(file, "name", "") or ""))
            if not name:
                raise UploadError("Pass filename=, with its extension, for this file object.")
            self.filename = name
        else:
            raise UploadError("Upload a path, bytes, or a binary file opened for reading.")
        if not hasattr(self, "_offset"):
            self._offset = 0

    def read(self, start: int, end: int) -> bytes:
        if self._bytes is not None:
            return self._bytes[start:end].tobytes()
        assert self._file is not None
        with self._lock:
            self._file.seek(self._offset + start)
            data = self._file.read(end - start)
        if len(data) != end - start:
            raise UploadError(f"{self.filename} got shorter while it was uploading.")
        return data

    def close(self) -> None:
        if self._owned is not None:
            self._owned.close()


def dub_body(
    upload_id: str,
    source_language: str,
    target_languages: list[str],
    voice_cloning: Optional[bool],
    num_speakers: Optional[int],
    media_category: Optional[str],
    metadata: Optional[Mapping[str, str]],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "upload_id": upload_id,
        "source_language": source_language,
        "target_languages": list(target_languages),
    }
    for name, value in (
        ("voice_cloning", voice_cloning),
        ("num_speakers", num_speakers),
        ("media_category", media_category),
        ("metadata", dict(metadata) if metadata is not None else None),
    ):
        if value is not None:
            body[name] = value
    return body


def is_link(file: Any) -> bool:
    return isinstance(file, str) and file.lower().startswith(("http://", "https://"))


def suffixed(key: Optional[str], suffix: str) -> Optional[str]:
    return f"{key}:{suffix}" if key else None


def quote(value: str) -> str:
    from urllib.parse import quote as _quote

    return _quote(value, safe="")
