from __future__ import annotations

from typing import Any, List, Mapping, Optional

import httpx

__all__ = [
    "ReedaptError",
    "APIError",
    "AuthenticationError",
    "NotFoundError",
    "ConflictError",
    "InvalidRequestError",
    "RateLimitError",
    "ServerError",
    "ConnectionError",
    "TimeoutError",
    "UploadError",
    "WaitError",
    "WebhookVerificationError",
]


class ReedaptError(Exception):
    """Base class for everything the library raises."""


class APIError(ReedaptError):
    """The API answered with an error. Branch on ``code``, not on the message."""

    status: int
    code: str
    message: str
    request_id: Optional[str]
    fields: Optional[List[Mapping[str, str]]]
    headers: httpx.Headers

    def __init__(self, status: int, body: Optional[Mapping[str, Any]], headers: httpx.Headers) -> None:
        body = body or {}
        self.status = status
        self.code = str(body.get("code") or f"HTTP_{status}")
        self.message = str(body.get("message") or f"The API answered {status}.")
        self.request_id = body.get("request_id") or headers.get("request-id")
        self.fields = body.get("fields")
        self.headers = headers
        super().__init__(f"{self.code}: {self.message}")


class AuthenticationError(APIError):
    """401: the key is missing, wrong or revoked."""


class NotFoundError(APIError):
    """404: nothing with this id belongs to the account."""


class ConflictError(APIError):
    """409: the request conflicts with the object's state."""


class InvalidRequestError(APIError):
    """4xx: the request can't be done as asked. Fix it before sending it again."""


class RateLimitError(APIError):
    """429: too many requests, or a limit such as dubs running at once."""

    @property
    def retry_after(self) -> Optional[float]:
        """Seconds to wait, from the ``Retry-After`` header, when the API sent one."""
        try:
            value = float(self.headers.get("retry-after", ""))
        except ValueError:
            return None
        return value if value >= 0 else None


class ServerError(APIError):
    """5xx: something went wrong on Reedapt's side."""


class ConnectionError(ReedaptError):
    """The request never got an answer."""


class TimeoutError(ConnectionError):
    """The request took longer than its timeout."""


class UploadError(ReedaptError):
    """An upload part could not be sent to storage."""


class WaitError(ReedaptError):
    """A ``wait_until_…`` helper ran out of time, or the thing it waited on failed."""


class WebhookVerificationError(ReedaptError):
    """A webhook's signature didn't match, or its timestamp was too old."""


def error_for(status: int, body: Optional[Mapping[str, Any]], headers: httpx.Headers) -> APIError:
    if status == 401:
        kind: type[APIError] = AuthenticationError
    elif status == 404:
        kind = NotFoundError
    elif status == 409:
        kind = ConflictError
    elif status in (400, 411, 413, 422):
        kind = InvalidRequestError
    elif status == 429:
        kind = RateLimitError
    elif status >= 500:
        kind = ServerError
    else:
        kind = APIError
    return kind(status, body, headers)
