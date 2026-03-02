"""Base HTTP client with shared lifecycle, timeout, and error handling."""

from __future__ import annotations

from typing import Any, cast

import httpx
from service_commons.exceptions import ServiceError


class BaseServiceClient:
    """Base class for all inter-service HTTP clients."""

    def __init__(self, base_url: str, timeout_seconds: int, service_name: str) -> None:
        self._base_url = base_url
        self._service_name = service_name
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        expected_status: int,
    ) -> dict[str, Any]:
        """Send POST request and return a JSON object response."""
        response = await self._post_raw(path, payload)
        if response.status_code != expected_status:
            raise self._status_error(path, response)
        return self._response_dict(response)

    async def _get(
        self,
        path: str,
        *,
        expected_status: int,
        not_found_returns_none: bool,
    ) -> dict[str, Any] | None:
        """Send GET request and return a JSON object response."""
        response = await self._get_raw(path)
        if response.status_code == 404 and not_found_returns_none:
            return None
        if response.status_code != expected_status:
            raise self._status_error(path, response)
        return self._response_dict(response)

    async def _post_raw(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        """Send POST request and return the raw response."""
        return await self._request("POST", path, payload)

    async def _get_raw(self, path: str) -> httpx.Response:
        """Send GET request and return the raw response."""
        return await self._request("GET", path, None)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> httpx.Response:
        try:
            if method == "POST":
                return await self._client.post(path, json=payload)
            return await self._client.get(path)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"Cannot reach {self._service_name}",
                status_code=502,
                details={"base_url": self._base_url, "path": path},
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"HTTP error from {self._service_name}",
                status_code=502,
                details={
                    "base_url": self._base_url,
                    "path": path,
                    "exception": str(exc),
                },
            ) from exc

    def _status_error(self, path: str, response: httpx.Response) -> ServiceError:
        error_body = self._safe_json_object(response)
        return ServiceError(
            error=str(error_body.get("error", f"{self._service_name}_error")),
            message=str(
                error_body.get(
                    "message",
                    f"Unexpected status {response.status_code} from {self._service_name}",
                )
            ),
            status_code=response.status_code,
            details=self._coerce_details(
                error_body.get(
                    "details",
                    {"base_url": self._base_url, "path": path, "status_code": response.status_code},
                )
            ),
        )

    def _safe_json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {}
        if isinstance(payload, dict):
            return cast("dict[str, Any]", payload)
        return {}

    def _response_dict(self, response: httpx.Response) -> dict[str, Any]:
        payload = self._safe_json_object(response)
        return payload

    def _coerce_details(self, raw: object) -> dict[str, object]:
        if isinstance(raw, dict):
            return cast("dict[str, object]", raw)
        return {}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
