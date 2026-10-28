# Reedapt Python library

The official Python library for the [Reedapt API](https://docs.reedapt.com). Upload video or audio, dub it into other languages in each speaker's own voice, and get the files back.

## Install

```bash
pip install reedapt
```

Python 3.9 or later. Sync and asyncio clients, fully typed.

## Quickstart

Make a key under **Developers** in [Reedapt](https://dub.reedapt.com/developers) and keep it in `REEDAPT_API_KEY`. Test keys are free and dub the first 60 seconds with a watermark.

```python
from reedapt import Reedapt

reedapt = Reedapt()  # reads REEDAPT_API_KEY

created = reedapt.dub(
    "interview.mp4",
    source_language="en",
    target_languages=["fr", "es"],
)

for started in created["dubs"]:
    dub = reedapt.dubs.wait_until_done(started["id"])
    if dub["status"] == "completed":
        files = reedapt.dubs.files(dub["id"])["files"]
        print(dub["target_language"], files[0]["url"])
    else:
        print(dub["target_language"], "failed:", dub["error"]["message"])
```

`dub()` takes a path, bytes (with `filename=`), a binary file object, or a public `https://` link that Reedapt downloads itself. In production, use [webhooks](#webhooks) instead of `wait_until_done`.

## Async

```python
import asyncio
from reedapt import AsyncReedapt


async def main() -> None:
    async with AsyncReedapt() as reedapt:
        created = await reedapt.dub("interview.mp4", source_language="en", target_languages=["fr"])
        dub = await reedapt.dubs.wait_until_done(created["dubs"][0]["id"])
        print(dub["status"])


asyncio.run(main())
```

## Uploads

`uploads.upload_file` sends the file straight to storage in parts, four at a time, signs more links as it needs them, and retries parts that fail:

```python
upload = reedapt.uploads.upload_file(
    "film.mov",
    concurrency=4,
    on_progress=lambda done, total: print(f"{100 * done // total}%"),
)

created = reedapt.dubs.create(
    upload_id=upload["id"],
    source_language="en",
    target_languages=["yo"],
    metadata={"order_id": "1042"},
)
```

For a file that's already online:

```python
upload = reedapt.uploads.from_url("https://example.com/media/talk.mp4")
```

## Dubs

```python
reedapt.dubs.retrieve("job_3f9a2c7e1b4d")
reedapt.dubs.list(limit=20)
for dub in reedapt.dubs.list_all():
    print(dub["id"], dub["status"])
reedapt.dubs.files("job_3f9a2c7e1b4d")  # links last an hour
reedapt.dubs.cancel("job_3f9a2c7e1b4d")  # minutes are returned

reedapt.languages.list()  # {"target_languages": [...]}
reedapt.usage.retrieve()  # plan, key mode and minutes left
```

Results are plain dictionaries, typed with `TypedDict`s in `reedapt.types`.

## Webhooks

Add an endpoint under **Developers**, or with `reedapt.webhook_endpoints.create(url=...)`, and keep its `whsec_` secret. Check every delivery against the raw body:

```python
import os
from flask import Flask, abort, request
from reedapt import WebhookVerificationError, construct_webhook_event

app = Flask(__name__)


@app.post("/reedapt/webhooks")
def reedapt_webhook():
    try:
        event = construct_webhook_event(
            request.get_data(),
            request.headers.get("Reedapt-Signature"),
            os.environ["REEDAPT_WEBHOOK_SECRET"],
        )
    except WebhookVerificationError:
        abort(400)
    if event["type"] == "dub.completed":
        dub = event["data"]["dub"]  # queue the work, then answer quickly
    return "", 200
```

An event can arrive more than once, so skip any `event["id"]` you've already handled.

## Errors

Failed requests raise a subclass of `APIError` with the HTTP `status`, a stable `code`, a plain-English `message` and the `request_id` to quote to support:

```python
from reedapt import APIError, RateLimitError

try:
    reedapt.dubs.create(upload_id=upload_id, source_language="en", target_languages=["fr"])
except RateLimitError as e:
    if e.code == "TOO_MANY_DUBS_RUNNING":
        ...  # start more once some finish
except APIError as e:
    print(e.status, e.code, e.message, e.request_id)
```

| Class | When |
| --- | --- |
| `AuthenticationError` | 401: the key is missing, wrong or revoked. |
| `NotFoundError` | 404 |
| `ConflictError` | 409: the object isn't in a state that allows this. |
| `InvalidRequestError` | 400, 411, 413, 422. `fields` lists invalid fields. |
| `RateLimitError` | 429. `retry_after` is in seconds. |
| `ServerError` | 5xx |
| `ConnectionError`, `TimeoutError` | No answer arrived. |
| `UploadError` | A part couldn't be sent to storage. |
| `WaitError` | A `wait_until_…` helper ran out of time, or the upload failed. |
| `WebhookVerificationError` | A delivery's signature didn't check out. |

All of them derive from `ReedaptError`. The [error codes](https://docs.reedapt.com/errors) are listed in the docs.

## Retries and repeated requests

Network failures, `500`, `502`, `503`, `504` and per-minute rate limits are retried twice, with backoff, honouring `Retry-After`. Creating uploads and dubs always sends an `Idempotency-Key`, so a retry never makes a second dub. Pass your own to make a repeat safe across restarts:

```python
reedapt.dubs.create(..., idempotency_key=f"order-{order.id}")
```

## Configuration

```python
reedapt = Reedapt(
    api_key=os.environ["REEDAPT_API_KEY"],
    timeout=60.0,  # per request, in seconds
    max_retries=2,
)

reedapt.dubs.retrieve(dub_id, timeout=10.0, max_retries=0)
```

Pass `http_client=httpx.Client(...)` to set proxies or transport options. Keep keys on your server. Never put one in an app people can download.

## Links

- [Documentation](https://docs.reedapt.com)
- [API reference](https://docs.reedapt.com/api-reference/introduction)
- [Status](https://status.reedapt.com)
- Support: support@reedapt.com
