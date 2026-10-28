from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable, Dict, List, Mapping, Optional, Sequence

import httpx
from typing_extensions import Self, Unpack

from . import _base
from ._base import FileInput, RequestOptions, Retry, Source
from ._client import ProgressCallback, check_upload
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
    WebhookDeliveryList,
    WebhookEndpoint,
    WebhookEndpointList,
)


class AsyncReedapt:
    """The asyncio client. Same methods as ``Reedapt``, awaited.

    ``api_key`` defaults to the ``REEDAPT_API_KEY`` environment variable.
    Use ``async with``, or call ``await close()``, to release connections.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = _base.DEFAULT_TIMEOUT,
        max_retries: int = _base.DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self._settings = _base.Settings(api_key, base_url, timeout, max_retries, default_headers)
        self._http = http_client or httpx.AsyncClient(timeout=timeout)
        self._owns_http = http_client is None
        self.uploads = AsyncUploads(self)
        self.dubs = AsyncDubs(self)
        self.webhook_endpoints = AsyncWebhookEndpoints(self)
        self.webhooks = Webhooks()
        self.languages = AsyncLanguagesResource(self)
        self.usage = AsyncUsageResource(self)

    @property
    def base_url(self) -> str:
        return self._settings.base_url

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def dub(
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
            upload = await self.uploads.from_url(
                str(file), filename=filename, idempotency_key=_base.suffixed(idempotency_key, "upload")
            )
        else:
            upload = await self.uploads.upload_file(
                file,
                filename=filename,
                concurrency=concurrency,
                on_progress=on_progress,
                idempotency_key=_base.suffixed(idempotency_key, "upload"),
            )
        dub_options: RequestOptions = {}
        if idempotency_key:
            dub_options["idempotency_key"] = f"{idempotency_key}:dubs"
        return await self.dubs.create(
            upload_id=upload["id"],
            source_language=source_language,
            target_languages=target_languages,
            voice_cloning=voice_cloning,
            num_speakers=num_speakers,
            media_category=media_category,
            metadata=metadata,
            **dub_options,
        )

    async def _request(
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
                    response = await self._http.request(
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
                await asyncio.sleep(_base.retry_delay(error, attempt))
                attempt += 1


class AsyncUploads:
    def __init__(self, client: AsyncReedapt) -> None:
        self._client = client

    async def create(
        self,
        *,
        filename: Optional[str] = None,
        size: Optional[int] = None,
        source_url: Optional[str] = None,
        **options: Unpack[RequestOptions],
    ) -> Upload:
        """Starts an upload. Most code wants ``upload_file`` or ``from_url`` instead."""
        body = {k: v for k, v in (("filename", filename), ("size", size), ("source_url", source_url)) if v is not None}
        return await self._client._request("POST", "/uploads", body=body, idempotent=True, options=options)

    async def sign_parts(
        self, upload_id: str, part_numbers: Sequence[int], **options: Unpack[RequestOptions]
    ) -> Dict[str, Any]:
        """Signs links for up to 100 more parts."""
        return await self._client._request(
            "POST",
            f"/uploads/{_base.quote(upload_id)}/parts",
            body={"part_numbers": list(part_numbers)},
            options=options,
        )

    async def complete(
        self, upload_id: str, parts: Sequence[UploadedPart], **options: Unpack[RequestOptions]
    ) -> Upload:
        return await self._client._request(
            "POST", f"/uploads/{_base.quote(upload_id)}/complete", body={"parts": list(parts)}, options=options
        )

    async def retrieve(self, upload_id: str, **options: Unpack[RequestOptions]) -> Upload:
        return await self._client._request("GET", f"/uploads/{_base.quote(upload_id)}", options=options)

    async def upload_file(
        self,
        file: FileInput,
        *,
        filename: Optional[str] = None,
        concurrency: int = 4,
        on_progress: Optional[ProgressCallback] = None,
        idempotency_key: Optional[str] = None,
    ) -> Upload:
        """Sends a file to storage in parts, several at once, and completes the
        upload. Expired links are signed again and failed parts retried.

        ``file`` is a path, bytes (with ``filename``), or a binary file object.
        Each part in flight (16 MiB or more) is held in memory."""
        source = Source(file, filename)
        try:
            create_options: RequestOptions = {"idempotency_key": idempotency_key} if idempotency_key else {}
            upload = await self.create(filename=source.filename, size=source.size, **create_options)
            links: Dict[int, str] = {p["part_number"]: p["url"] for p in upload.get("parts") or []}
            sign_lock = asyncio.Lock()
            uploaded = 0

            async def link_for(n: int, refresh: bool) -> str:
                async with sign_lock:
                    if refresh:
                        links.pop(n, None)
                    if n not in links:
                        wanted = [n] if refresh else _base.sign_batch(n, upload["part_count"], links)
                        signed = await self.sign_parts(upload["id"], wanted)
                        links.update({p["part_number"]: p["url"] for p in signed["parts"]})
                    if n not in links:
                        raise UploadError(f"No link was signed for part {n} of {upload['id']}.")
                    return links[n]

            queue = list(range(upload["part_count"], 0, -1))
            parts: List[UploadedPart] = []

            async def worker() -> None:
                nonlocal uploaded
                while queue:
                    n = queue.pop()
                    start, end = _base.part_range(n, upload["part_size"], source.size)
                    data = await asyncio.get_running_loop().run_in_executor(None, source.read, start, end)
                    etag = await self._put_part(n, data, link_for)
                    parts.append({"part_number": n, "etag": etag})
                    uploaded += end - start
                    if on_progress is not None:
                        on_progress(uploaded, source.size)

            tasks = [asyncio.ensure_future(worker()) for _ in range(max(1, min(concurrency, upload["part_count"])))]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                # One failed part stops the others.
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            parts.sort(key=lambda p: p["part_number"])

            try:
                return await self.complete(upload["id"], parts)
            except ConflictError as e:
                # A retried complete that had already gone through.
                if e.code == "UPLOAD_COMPLETE":
                    return await self.retrieve(upload["id"])
                raise
        finally:
            source.close()

    async def _put_part(self, n: int, data: bytes, link_for: Callable[[int, bool], Any]) -> str:
        refresh = False
        problem = ""
        for attempt in range(_base.PART_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_base.backoff(attempt))
            url = await link_for(n, refresh)
            try:
                # No API key here: the signed link carries its own permission.
                response = await self._client._http.put(
                    url, content=data, timeout=max(self._client._settings.timeout, 600)
                )
            except httpx.TransportError as e:
                problem, refresh = str(e), False
                continue
            etag, refresh, problem = _base.part_outcome(n, response)
            if etag:
                return etag
        raise UploadError(f"Part {n} failed: {problem}.")

    async def from_url(
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
        upload = await self.create(source_url=source_url, filename=filename, **options)
        if not wait:
            return upload
        return await self.wait_until_ready(upload["id"], poll_interval=poll_interval, timeout=timeout)

    async def wait_until_ready(
        self, upload_id: str, *, poll_interval: float = 10, timeout: float = 3 * 60 * 60
    ) -> Upload:
        """Polls an upload until it can be dubbed. Raises ``WaitError`` if it failed."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            upload = await self.retrieve(upload_id)
            check_upload(upload)
            if upload["status"] == "uploaded":
                return upload
            if loop.time() + poll_interval > deadline:
                raise WaitError(f"Upload {upload_id} was still {upload['status']} when the wait ran out.")
            await asyncio.sleep(poll_interval)


class AsyncDubs:
    def __init__(self, client: AsyncReedapt) -> None:
        self._client = client

    async def create(
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
        return await self._client._request("POST", "/dubs", body=body, idempotent=True, options=options)

    async def retrieve(self, dub_id: str, **options: Unpack[RequestOptions]) -> Dub:
        return await self._client._request("GET", f"/dubs/{_base.quote(dub_id)}", options=options)

    async def list(
        self, *, limit: Optional[int] = None, offset: Optional[int] = None, **options: Unpack[RequestOptions]
    ) -> DubList:
        """One page of the account's dubs, newest first."""
        return await self._client._request("GET", "/dubs", params={"limit": limit, "offset": offset}, options=options)

    async def list_all(self, *, page_size: int = 100) -> AsyncIterator[Dub]:
        """Every dub, newest first, fetching pages as you go."""
        seen = set()
        offset = 0
        while True:
            page = await self.list(limit=page_size, offset=offset)
            for dub in page["data"]:
                # Dubs started while paging shift later pages; skip the repeats.
                if dub["id"] not in seen:
                    seen.add(dub["id"])
                    yield dub
            if not page["has_more"] or not page["data"]:
                return
            offset += page_size

    async def cancel(self, dub_id: str, **options: Unpack[RequestOptions]) -> None:
        """Stops an unfinished dub and deletes it. Its minutes are returned."""
        await self._client._request("POST", f"/dubs/{_base.quote(dub_id)}/cancel", options=options)

    async def files(self, dub_id: str, **options: Unpack[RequestOptions]) -> DubFiles:
        """Download links for a completed dub's files. Each link lasts an hour."""
        return await self._client._request("GET", f"/dubs/{_base.quote(dub_id)}/files", options=options)

    async def wait_until_done(
        self,
        dub_id: str,
        *,
        poll_interval: float = 30,
        timeout: float = 12 * 60 * 60,
        on_update: Optional[Callable[[Dub], None]] = None,
    ) -> Dub:
        """Polls a dub until it is ``completed`` or ``failed`` and returns it.
        Check ``status`` on the result. Webhooks are better for long dubs."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            dub = await self.retrieve(dub_id)
            if on_update is not None:
                on_update(dub)
            if dub["status"] in ("completed", "failed"):
                return dub
            if loop.time() + poll_interval > deadline:
                raise WaitError(f"Dub {dub_id} was still {dub['status']} when the wait ran out.")
            await asyncio.sleep(poll_interval)


class AsyncWebhookEndpoints:
    def __init__(self, client: AsyncReedapt) -> None:
        self._client = client

    async def create(
        self, *, url: str, description: Optional[str] = None, **options: Unpack[RequestOptions]
    ) -> WebhookEndpoint:
        """Adds an endpoint. The answer's ``secret`` is shown only this once."""
        body = {"url": url, **({"description": description} if description is not None else {})}
        # Not retried after a network failure: it could add the endpoint twice.
        return await self._client._request("POST", "/webhook_endpoints", body=body, retry="unsafe", options=options)

    async def list(self, **options: Unpack[RequestOptions]) -> WebhookEndpointList:
        return await self._client._request("GET", "/webhook_endpoints", options=options)

    async def delete(self, endpoint_id: str, **options: Unpack[RequestOptions]) -> None:
        await self._client._request("DELETE", f"/webhook_endpoints/{_base.quote(endpoint_id)}", options=options)

    async def send_test(self, endpoint_id: str, **options: Unpack[RequestOptions]) -> None:
        """Sends a ``webhook.test`` event to the endpoint."""
        await self._client._request(
            "POST", f"/webhook_endpoints/{_base.quote(endpoint_id)}/test", retry="unsafe", options=options
        )

    async def deliveries(
        self, endpoint_id: str, *, limit: Optional[int] = None, **options: Unpack[RequestOptions]
    ) -> WebhookDeliveryList:
        """Recent deliveries to the endpoint, with what it answered."""
        return await self._client._request(
            "GET", f"/webhook_endpoints/{_base.quote(endpoint_id)}/deliveries", params={"limit": limit}, options=options
        )


class AsyncLanguagesResource:
    def __init__(self, client: AsyncReedapt) -> None:
        self._client = client

    async def list(self, **options: Unpack[RequestOptions]) -> Languages:
        """The codes this key's plan can dub into right now."""
        return await self._client._request("GET", "/languages", options=options)


class AsyncUsageResource:
    def __init__(self, client: AsyncReedapt) -> None:
        self._client = client

    async def retrieve(self, **options: Unpack[RequestOptions]) -> Usage:
        """The plan, the kind of key and the minutes left."""
        return await self._client._request("GET", "/usage", options=options)
