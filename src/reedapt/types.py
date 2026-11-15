"""Shapes the API returns, as typed dictionaries.

Objects may gain keys over time, so read the ones you need and ignore the rest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from typing_extensions import Literal, NotRequired, Required, TypedDict

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


class TranscriptLine(TypedDict):
    start_ms: int
    end_ms: int
    speaker: NotRequired[Optional[str]]
    """The same label throughout a dub, such as ``SPEAKER_00``."""
    original_text: str
    text: str
    """The line in the dub's language."""


class Transcript(TypedDict):
    """A completed dub's lines in time order, edits made in the editor included."""

    dub_id: str
    source_language: NotRequired[Optional[str]]
    target_language: NotRequired[Optional[str]]
    lines: List[TranscriptLine]


QualityFix = Literal["shortened", "revoiced"]
RunStatus = Literal["done", "needs_input", "applied", "declined", "failed"]
RunChoice = Literal["apply", "decline"]
Autonomy = Literal["ask", "decide"]


class FlaggedLine(TypedDict):
    line: int
    start_ms: int
    checks: List[str]
    """What the checks found, such as ``fit`` (the line doesn't fit its time)."""
    fixed: Optional[QualityFix]
    """How Reedapt dealt with it, or ``None`` if it's left for you to review."""


class Quality(TypedDict):
    """Reedapt's check of a finished dub. ``checked`` is False until the measurement checks have run."""

    dub_id: str
    checked: bool
    listened: bool
    lines: int
    fixed: int
    to_review: int
    flagged: List[FlaggedLine]


class RunProposal(TypedDict):
    kind: str
    summary: str
    lines: int
    """How many lines it would change."""


class RunOption(TypedDict):
    id: RunChoice
    label: str


class Run(TypedDict):
    """A request for changes to a dub, and what Reedapt did or proposes to do about it."""

    id: str
    object: NotRequired[str]
    dub_id: str
    status: RunStatus
    request: str
    reply: str
    proposal: NotRequired[Optional[RunProposal]]
    """Present while ``status`` is ``needs_input``."""
    options: NotRequired[Optional[List[RunOption]]]
    applied_lines: NotRequired[Optional[int]]
    """How many lines were changed, once the run is ``applied``. They are voiced again in the background."""
    created_at: str


class DubLine(TypedDict):
    line: int
    """The line's number in time order, starting at 1."""
    start_ms: int
    end_ms: int
    speaker: NotRequired[Optional[str]]
    original_text: str
    text: str
    """The line in the dub's language."""
    delivery_style: NotRequired[Optional[str]]
    voice_instructions: NotRequired[Optional[str]]


class DubLines(TypedDict):
    dub_id: str
    lines: List[DubLine]


class LineEdit(TypedDict, total=False):
    line: Required[int]
    text: str
    """New wording, in the dub's language."""
    delivery_style: Optional[str]
    """For example ``angry`` or ``whisper``. ``None`` clears it."""
    voice_instructions: Optional[str]
    """How to say it, in plain words. Up to 300 characters."""


class LinesEdited(TypedDict):
    dub_id: str
    lines: List[int]
    """The lines being voiced again."""
    status: Literal["revoicing"]


class Languages(TypedDict):
    target_languages: List[str]


class Usage(TypedDict):
    plan: NotRequired[Optional[str]]
    key_mode: Literal["live", "test"]
    minutes_remaining: NotRequired[Optional[float]]
    unlimited: bool
    test_preview_seconds: int


class DubCounts(TypedDict):
    completed: int
    in_progress: int
    failed: int
    """Failed or cancelled. Their minutes were returned."""


class KeyUsage(TypedDict):
    key_id: Optional[str]
    mode: Literal["live", "test"]
    minutes: float
    minutes_in_progress: float
    dubs: DubCounts


class LanguageUsage(TypedDict):
    target_language: Optional[str]
    minutes: float
    minutes_in_progress: float
    dubs: DubCounts


class UsageReport(TypedDict):
    """One month of the account's API usage. A dub counts in the month it started (UTC).
    ``minutes`` are completed live dubs; test dubs are free."""

    month: str
    period_start: str
    period_end: str
    minutes: float
    minutes_in_progress: float
    dubs: DubCounts
    keys: List[KeyUsage]
    languages: List[LanguageUsage]


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
    """``type`` is ``dub.completed``, ``dub.failed``, ``dub.checked``, ``batch.completed``
    or ``webhook.test``; new types may be added, so ignore the ones you don't know.
    ``data`` holds ``dub`` (and ``quality`` for ``dub.checked``) or ``batch``."""

    id: str
    type: str
    created_at: str
    data: Dict[str, Any]
