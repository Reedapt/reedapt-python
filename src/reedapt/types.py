"""Shapes the API returns, as typed dictionaries.

Objects may gain keys over time, so read the ones you need and ignore the rest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from typing_extensions import Literal, NotRequired, TypedDict

UploadStatus = Literal["uploading", "fetching", "uploaded", "used", "failed"]
DubStatus = Literal["queued", "processing", "completed", "failed"]
FileKind = Literal["dub", "dubbed_audio_wav", "voice", "background"]


class PartLink(TypedDict):
    part_number: int
    url: str


class Upload(TypedDict):
    id: str
    object: NotRequired[str]
    filename: str
    size: int
    status: UploadStatus
    error: NotRequired[Optional[str]]
    part_size: int
    part_count: int
    parts: NotRequired[List[PartLink]]
    parts_expire_at: NotRequired[Optional[str]]


class UploadedPart(TypedDict):
    part_number: int
    etag: str


class DubError(TypedDict):
    code: str
    message: str


class Dub(TypedDict):
    id: str
    object: NotRequired[str]
    status: DubStatus
    stage: NotRequired[Optional[str]]
    progress: int
    source_language: Optional[str]
    target_language: Optional[str]
    audio: bool
    test: bool
    preview_seconds: NotRequired[Optional[int]]
    duration_minutes: NotRequired[Optional[float]]
    created_at: str
    started_at: NotRequired[Optional[str]]
    completed_at: NotRequired[Optional[str]]
    error: NotRequired[Optional[DubError]]
    metadata: NotRequired[Dict[str, str]]


class RefusedLanguage(TypedDict):
    target_language: str
    code: str
    message: str


class DubsCreated(TypedDict):
    dubs: List[Dub]
    refused: NotRequired[List[RefusedLanguage]]


class DubList(TypedDict):
    data: List[Dub]
    has_more: bool


class FileLink(TypedDict):
    kind: FileKind
    filename: str
    url: str


class DubFiles(TypedDict):
    files: List[FileLink]
    expires_at: str


class Languages(TypedDict):
    target_languages: List[str]


class Usage(TypedDict):
    plan: NotRequired[Optional[str]]
    key_mode: Literal["live", "test"]
    minutes_remaining: NotRequired[Optional[float]]
    unlimited: bool
    test_preview_seconds: int


class WebhookEndpoint(TypedDict):
    id: str
    object: NotRequired[str]
    url: str
    description: NotRequired[Optional[str]]
    events: List[str]
    created_at: str
    secret: NotRequired[Optional[str]]
    """Only on the endpoint returned by ``create``. Keep it to check signatures."""


class WebhookEndpointList(TypedDict):
    data: List[WebhookEndpoint]


class WebhookDelivery(TypedDict):
    id: str
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_status_code: NotRequired[Optional[int]]
    last_error: NotRequired[Optional[str]]
    next_attempt_at: NotRequired[Optional[str]]
    created_at: str
    delivered_at: NotRequired[Optional[str]]


class WebhookDeliveryList(TypedDict):
    data: List[WebhookDelivery]


class WebhookEvent(TypedDict):
    """``type`` is ``dub.completed``, ``dub.failed``, ``batch.completed`` or
    ``webhook.test``; new types may be added. ``data`` holds ``dub`` or ``batch``."""

    id: str
    type: str
    created_at: str
    data: Dict[str, Any]
