# Changelog

## 0.2.0

`instructions` on `dubs.create()` and `dub()`: notes for the dub, followed on paid plans.

`dubs.quality()` returns Reedapt's check of a finished dub, and webhooks gain the `dub.checked` event.

Early access: `dubs.requests.create()`, `runs.retrieve()` and `runs.answer()` to ask Reedapt for changes, and `dubs.lines.list()` and `dubs.lines.edit()` to read and edit lines. The asyncio client has them too.

## 0.1.0

`dubs.transcript()`, `dubs.subtitles()` (SRT or WebVTT) and `webhook_endpoints.resend_delivery()`.

`usage.report()` for a month of minutes and dubs per key and per language.

First release: sync and asyncio clients for uploads in parts and from links, dubs, languages, usage, webhook endpoints and webhook signature checks, with retries and idempotency keys.
