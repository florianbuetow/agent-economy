"""Errors the feeder loops treat as a temporarily unavailable dependency."""

from __future__ import annotations

import httpx
from service_commons.exceptions import ServiceError

RECOVERABLE_DEPENDENCY_ERRORS: tuple[type[Exception], ...] = (httpx.HTTPError, ServiceError)
