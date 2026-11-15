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
    instructions="Keep the speaker's name, Adaeze, as it is. Warm, informal tone.",  # paid plans
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
reedapt.dubs.transcript("job_3f9a2c7e1b4d")  # every line, with timing, speaker and translation
reedapt.dubs.subtitles("job_3f9a2c7e1b4d", format="vtt")  # SRT (default) or WebVTT text
reedapt.dubs.quality("job_3f9a2c7e1b4d")  # Reedapt's check: lines fixed and lines left to review

reedapt.languages.list()  # {"target_languages": [...]}
reedapt.usage.retrieve()  # plan, key mode and minutes left
reedapt.usage.report(month="2026-10")  # minutes and dubs per key and per language
```

Results are plain dictionaries, typed with `TypedDict`s in `reedapt.types`.

## Ask Reedapt for changes

Early access: this is released to some paid accounts first, and other accounts get a `403` with the code `NOT_AVAILABLE`. Describe a change in plain words and Reedapt either answers, proposes a change, or makes it. With `autonomy="ask"` (the default) nothing changes until you say so:

```python
run = reedapt.dubs.requests.create(
    "job_3f9a2c7e1b4d",
    text="Is line 3 natural? If not, make it sound calmer.",
    autonomy="ask",
)
print(run["reply"])

if run["status"] == "needs_input":
    print(run["proposal"]["summary"], run["proposal"]["lines"])  # lines = how many it would change
    run = reedapt.runs.answer(run["id"], choice="apply")  # or "decline"
# run["status"] is done, applied, declined or failed; run.get("applied_lines") counts the lines changed.
reedapt.runs.retrieve(run["id"])
```

You can also read and edit lines yourself. Edited lines are voiced again in the background:

```python
lines = reedapt.dubs.lines.list("job_3f9a2c7e1b4d")["lines"]
reedapt.dubs.lines.edit(
    "job_3f9a2c7e1b4d",
    lines=[{"line": 3, "text": "Bienvenue chez nous.", "delivery_style": "warm"}],
)  # {"dub_id": ..., "lines": [3], "status": "revoicing"}
```

A request counts against your plan's allowance for these requests; when it's used up you get `AGENT_ALLOWANCE_USED` (429) until it renews. The asyncio client has the same methods.

## Webhooks

Add an endpoint under **Developers**, or with `reedapt.webhook_endpoints.create(url=...)`, and keep its `whsec_` secret. `reedapt.webhook_endpoints.resend_delivery(endpoint_id, delivery_id)` sends a past delivery again. Check every delivery against the raw body:

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
    elif event["type"] == "dub.checked":
        # A few minutes later, on plans where Reedapt checks dubs:
        quality = event["data"]["quality"]  # lines it fixed and lines left to review
    # Ignore types you don't know; every endpoint gets every event.
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
