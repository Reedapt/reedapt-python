from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence

import httpx
from typing_extensions import Self, Unpack

from . import _base
from ._base import FileInput, RequestOptions, Retry, Source
from ._errors import ConflictError, ReedaptError, UploadError, WaitError
from ._webhooks import Webhooks
from .types import (
    Dub,
    DubFiles,
    DubList,
    DubsCreated,
    Languages,
    Upload,
    UploadedPart,
    Usage,
    UsageReport,
    WebhookDeliveryList,
    WebhookEndpoint,
    WebhookEndpointList,
)

ProgressCallback = Callable[[int, int], None]
"""Called with (uploaded bytes, total bytes) after each part."""


class Reedapt:
    """A client for the Reedapt API.

    ``api_key`` defaults to the ``REEDAPT_API_KEY`` environment variable.
    Use it as a context manager, or call ``close()``, to release connections.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = _base.DEFAULT_TIMEOUT,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self._settings = _base.Settings(api_key, base_url, timeout, max_retries, default_headers)
        self._http = http_client or httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self.uploads = Uploads(self)
        self.dubs = Dubs(self)
        self.webhook_endpoints = WebhookEndpoints(self)
        self.webhooks = Webhooks()
        self.languages = LanguagesResource(self)
        self.usage = UsageResource(self)

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def dub(
        self,
        file: FileInput,
        *,
        source_language: str,
        target_languages: Sequence[str],
        filename: Optional[str] = None,
        voice_cloning: Optional[bool] = None,
        num_speakers: Optional[int] = None,
        media_category: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
        concurrency: int = 4,
        on_progress: Optional[ProgressCallback] = None,
        idempotency_key: Optional[str] = None,
    ) -> DubsCreated:
        """Uploads a file, or has Reedapt fetch a public ``https://`` link, and
        starts a dub for each target language. Returns once the dubs have
        started, not when they finish."""
        if _base.is_link(file):
            upload = self.uploads.from_url(
                str(file), filename=filename, idempotency_key=_base.suffixed(idempotency_key, "upload")
            )
        else:
            upload = self.uploads.upload_file(
                file,
                filename=filename,
                concurrency=concurrency,
                on_progress=on_progress,
                idempotency_key=_base.suffixed(idempotency_key, "upload"),
            )
        dub_options: RequestOptions = {}
        if idempotency_key:
            dub_options["idempotency_key"] = f"{idempotency_key}:dubs"
        return self.dubs.create(
            upload_id=upload["id"],
            source_language=source_language,
            target_languages=target_languages,
            voice_cloning=voice_cloning,
            num_speakers=num_speakers,
            media_category=media_category,
            metadata=metadata,
            **dub_options,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Optional[Dict[str, Any]] = None,
        retry: Retry = "safe",
        idempotent: bool = False,
        options: Optional[RequestOptions] = None,
    ) -> Any:
        options = options or {}
        key = _base.idempotency_key_for(idempotent, options)
        headers = self._settings.headers(body is not None, key, options)
        retry = "safe" if key else retry
        timeout = options.get("timeout", self._settings.timeout)
        max_retries = options.get("max_retries", self._settings.max_retries)
        query = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            try:
                try:
                    response = self._http.request(
                        method,
                        self._settings.base_url + path,
                        headers=headers,
                        json=body,
                        params=query or None,
                        timeout=timeout,
                    )
                except httpx.TransportError as e:
                    raise _base.connection_error(method, path, e, timeout) from e
                return _base.parse(response)
            except ReedaptError as error:
                if attempt >= max_retries or not _base.should_retry(error, retry):
                    raise
                time.sleep(_base.retry_delay(error, attempt))
                attempt += 1


class Uploads:
    def __init__(self, client: Reedapt) -> None:
        self._client = client

    def create(
        self,
        *,
        filename: Optional[str] = None,
        size: Optional[int] = None,
        source_url: Optional[str] = None,
        **options: Unpack[RequestOptions],
    ) -> Upload:
        """Starts an upload. Most code wants ``upload_file`` or ``from_url`` instead."""
        body = {k: v for k, v in (("filename", filename), ("size", size), ("source_url", source_url)) if v is not None}
        return self._client._request("POST", "/uploads", body=body, idempotent=True, options=options)

    def sign_parts(
        self, upload_id: str, part_numbers: Sequence[int], **options: Unpack[RequestOptions]
    ) -> Dict[str, Any]:
        """Signs links for up to 100 more parts."""
        return self._client._request(
            "POST",
            f"/uploads/{_base.quote(upload_id)}/parts",
            body={"part_numbers": list(part_numbers)},
            options=options,
        )

    def complete(self, upload_id: str, parts: Sequence[UploadedPart], **options: Unpack[RequestOptions]) -> Upload:
        return self._client._request(
            "POST", f"/uploads/{_base.quote(upload_id)}/complete", body={"parts": list(parts)}, options=options
        )

    def retrieve(self, upload_id: str, **options: Unpack[RequestOptions]) -> Upload:
        return self._client._request("GET", f"/uploads/{_base.quote(upload_id)}", options=options)

    def upload_file(
        self,
        file: FileInput,
        *,
        filename: Optional[str] = None,
        concurrency: int = 4,
        on_progress: Optional[ProgressCallback] = None,
        idempotency_key: Optional[str] = None,
    ) -> Upload:
        """Sends a file to storage in parts, in parallel threads, and completes
        the upload. Expired links are signed again and failed parts retried.

        ``file`` is a path, bytes (with ``filename``), or a binary file object.
        Each thread holds one part (16 MiB or more) in memory."""
        source = Source(file, filename)
        try:
            create_options: RequestOptions = {"idempotency_key": idempotency_key} if idempotency_key else {}
            upload = self.create(filename=source.filename, size=source.size, **create_options)
            links: Dict[int, str] = {p["part_number"]: p["url"] for p in upload.get("parts") or []}
            sign_lock = threading.Lock()
            progress_lock = threading.Lock()
            uploaded = [0]

            def link_for(n: int, refresh: bool) -> str:
                with sign_lock:
                    if refresh:
                        links.pop(n, None)
                    if n not in links:
                        wanted = [n] if refresh else _base.sign_batch(n, upload["part_count"], links)
                        signed = self.sign_parts(upload["id"], wanted)
                        links.update({p["part_number"]: p["url"] for p in signed["parts"]})
                    if n not in links:
                        raise UploadError(f"No link was signed for part {n} of {upload['id']}.")
                    return links[n]

            def send(n: int) -> UploadedPart:
                start, end = _base.part_range(n, upload["part_size"], source.size)
                etag = self._put_part(n, source.read(start, end), link_for)
                if on_progress is not None:
                    with progress_lock:
                        uploaded[0] += end - start
                        on_progress(uploaded[0], source.size)
                return {"part_number": n, "etag": etag}

            workers = max(1, min(concurrency, upload["part_count"]))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="reedapt-upload") as pool:
                futures = [pool.submit(send, n) for n in range(1, upload["part_count"] + 1)]
                done, _ = wait_for_futures(futures, return_when=FIRST_EXCEPTION)
                for future in done:
                    if future.exception() is not None:
                        # One failed part stops the parts that haven't started.
                        for other in futures:
                            other.cancel()
                        raise future.exception()  # type: ignore[misc]
                parts: List[UploadedPart] = [f.result() for f in futures]

            try:
                return self.complete(upload["id"], parts)
            except ConflictError as e:
                # A retried complete that had already gone through.
                if e.code == "UPLOAD_COMPLETE":
                    return self.retrieve(upload["id"])
                raise
        finally:
            source.close()

    def _put_part(self, n: int, data: bytes, link_for: Callable[[int, bool], str]) -> str:
        refresh = False
        problem = ""
        for attempt in range(_base.PART_ATTEMPTS):
            if attempt:
                time.sleep(_base.backoff(attempt))
            url = link_for(n, refresh)
            try:
                # No API key here: the signed link carries its own permission.
                response = self._client._http.put(url, content=data, timeout=max(self._client._settings.timeout, 600))
            except httpx.TransportError as e:
                problem, refresh = str(e), False
                continue
            etag, refresh, problem = _base.part_outcome(n, response)
            if etag:
                return etag
        raise UploadError(f"Part {n} failed: {problem}.")

    def from_url(
        self,
        source_url: str,
        *,
        filename: Optional[str] = None,
        wait: bool = True,
        poll_interval: float = 10,
        timeout: float = 3 * 60 * 60,
        idempotency_key: Optional[str] = None,
    ) -> Upload:
        """Has Reedapt download a public link. Waits until it's ready unless ``wait`` is False."""
        options: RequestOptions = {"idempotency_key": idempotency_key} if idempotency_key else {}
        upload = self.create(source_url=source_url, filename=filename, **options)
        if not wait:
            return upload
        return self.wait_until_ready(upload["id"], poll_interval=poll_interval, timeout=timeout)

    def wait_until_ready(self, upload_id: str, *, poll_interval: float = 10, timeout: float = 3 * 60 * 60) -> Upload:
        """Polls an upload until it can be dubbed. Raises ``WaitError`` if it failed."""
        deadline = time.monotonic() + timeout
        while True:
            upload = self.retrieve(upload_id)
            check_upload(upload)
            if upload["status"] == "uploaded":
                return upload
            if time.monotonic() + poll_interval > deadline:
                raise WaitError(f"Upload {upload_id} was still {upload['status']} when the wait ran out.")
            time.sleep(poll_interval)


def check_upload(upload: Upload) -> None:
    if upload["status"] == "failed":
        raise WaitError(f"Upload {upload['id']} failed: {upload.get('error') or 'no reason given'}.")
    if upload["status"] == "used":
        raise WaitError(f"Upload {upload['id']} was already dubbed. Upload the file again.")


class Dubs:
    def __init__(self, client: Reedapt) -> None:
        self._client = client

    def create(
        self,
        *,
        upload_id: str,
        source_language: str,
        target_languages: Sequence[str],
        voice_cloning: Optional[bool] = None,
        num_speakers: Optional[int] = None,
        media_category: Optional[str] = None,
        metadata: Optional[Mapping[str, str]] = None,
        **options: Unpack[RequestOptions],
    ) -> DubsCreated:
        """Starts one dub per target language. Languages that can't start come back in ``refused``."""
        body = _base.dub_body(
            upload_id, source_language, list(target_languages), voice_cloning, num_speakers, media_category, metadata
        )
        return self._client._request("POST", "/dubs", body=body, idempotent=True, options=options)

    def retrieve(self, dub_id: str, **options: Unpack[RequestOptions]) -> Dub:
        return self._client._request("GET", f"/dubs/{_base.quote(dub_id)}", options=options)

    def list(
        self, *, limit: Optional[int] = None, offset: Optional[int] = None, **options: Unpack[RequestOptions]
    ) -> DubList:
        """One page of the account's dubs, newest first."""
        return self._client._request("GET", "/dubs", params={"limit": limit, "offset": offset}, options=options)

    def list_all(self, *, page_size: int = 100) -> Iterator[Dub]:
        """Every dub, newest first, fetching pages as you go."""
        seen = set()
        offset = 0
        while True:
            page = self.list(limit=page_size, offset=offset)
            for dub in page["data"]:
                # Dubs started while paging shift later pages; skip the repeats.
                if dub["id"] not in seen:
                    seen.add(dub["id"])
                    yield dub
            if not page["has_more"] or not page["data"]:
                return
            offset += page_size

    def cancel(self, dub_id: str, **options: Unpack[RequestOptions]) -> None:
        """Stops an unfinished dub and deletes it. Its minutes are returned."""
        self._client._request("POST", f"/dubs/{_base.quote(dub_id)}/cancel", options=options)

    def files(self, dub_id: str, **options: Unpack[RequestOptions]) -> DubFiles:
        """Download links for a completed dub's files. Each link lasts an hour."""
        return self._client._request("GET", f"/dubs/{_base.quote(dub_id)}/files", options=options)

    def wait_until_done(
        self,
        dub_id: str,
        *,
        poll_interval: float = 30,
        timeout: float = 12 * 60 * 60,
        on_update: Optional[Callable[[Dub], None]] = None,
    ) -> Dub:
        """Polls a dub until it is ``completed`` or ``failed`` and returns it.
        Check ``status`` on the result. Webhooks are better for long dubs."""
        deadline = time.monotonic() + timeout
        while True:
            dub = self.retrieve(dub_id)
            if on_update is not None:
                on_update(dub)
            if dub["status"] in ("completed", "failed"):
                return dub
            if time.monotonic() + poll_interval > deadline:
                raise WaitError(f"Dub {dub_id} was still {dub['status']} when the wait ran out.")
            time.sleep(poll_interval)


class WebhookEndpoints:
    def __init__(self, client: Reedapt) -> None:
        self._client = client

    def create(
        self, *, url: str, description: Optional[str] = None, **options: Unpack[RequestOptions]
    ) -> WebhookEndpoint:
        """Adds an endpoint. The answer's ``secret`` is shown only this once."""
        body = {"url": url, **({"description": description} if description is not None else {})}
        # Not retried after a network failure: it could add the endpoint twice.
        return self._client._request("POST", "/webhook_endpoints", body=body, retry="unsafe", options=options)

    def list(self, **options: Unpack[RequestOptions]) -> WebhookEndpointList:
        return self._client._request("GET", "/webhook_endpoints", options=options)

    def delete(self, endpoint_id: str, **options: Unpack[RequestOptions]) -> None:
        self._client._request("DELETE", f"/webhook_endpoints/{_base.quote(endpoint_id)}", options=options)

    def send_test(self, endpoint_id: str, **options: Unpack[RequestOptions]) -> None:
        """Sends a ``webhook.test`` event to the endpoint."""
        self._client._request(
            "POST", f"/webhook_endpoints/{_base.quote(endpoint_id)}/test", retry="unsafe", options=options
        )

    def deliveries(
        self, endpoint_id: str, *, limit: Optional[int] = None, **options: Unpack[RequestOptions]
    ) -> WebhookDeliveryList:
        """Recent deliveries to the endpoint, with what it answered."""
        return self._client._request(
            "GET", f"/webhook_endpoints/{_base.quote(endpoint_id)}/deliveries", params={"limit": limit}, options=options
        )


class LanguagesResource:
    def __init__(self, client: Reedapt) -> None:
        self._client = client

    def list(self, **options: Unpack[RequestOptions]) -> Languages:
        """The codes this key's plan can dub into right now."""
        return self._client._request("GET", "/languages", options=options)


class UsageResource:
    def __init__(self, client: Reedapt) -> None:
        self._client = client

    def retrieve(self, **options: Unpack[RequestOptions]) -> Usage:
        """The plan, the kind of key and the minutes left."""
        return self._client._request("GET", "/usage", options=options)

    def report(self, *, month: Optional[str] = None, **options: Unpack[RequestOptions]) -> UsageReport:
        """A month (``YYYY-MM``, UTC; default this month) of minutes and dubs, per key and per language."""
        return self._client._request("GET", "/usage/report", params={"month": month}, options=options)
