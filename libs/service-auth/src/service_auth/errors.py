"""Typed API errors raised by the agent SDK."""

from __future__ import annotations

from typing import Any

import httpx


class EconomyApiError(httpx.HTTPStatusError):
    """An economy service rejected a request with its structured error body.

    Subclasses ``httpx.HTTPStatusError`` so callers that catch the transport error
    keep working, while exposing the service's ``error`` code, ``message`` and
    ``details`` directly.
    """

    def __init__(
        self,
        *,
        status_code: int,
        error: str,
        message: str,
        details: dict[str, Any],
        request: httpx.Request,
        response: httpx.Response,
    ) -> None:
        super().__init__(f"{status_code} {error}: {message}", request=request, response=response)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.details = details


def raise_for_api_error(response: httpx.Response) -> None:
    """Raise ``EconomyApiError`` for a structured error body, else the httpx error."""
    if response.status_code < 400:
        return
    body: object
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        details = body.get("details")
        raise EconomyApiError(
            status_code=response.status_code,
            error=body["error"],
            message=str(body.get("message", "")),
            details=details if isinstance(details, dict) else {},
            request=response.request,
            response=response,
        )
    response.raise_for_status()
