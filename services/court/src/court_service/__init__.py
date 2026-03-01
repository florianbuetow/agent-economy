"""Court Service - dispute resolution for the agent economy."""

from typing import Any, cast

from service_commons.exceptions import ServiceError as _ServiceError

__version__ = "0.1.0"


def _enable_service_error_compatibility() -> None:
    """
    Accept legacy ServiceError(error, message, status_code) constructor calls.

    Some test fixtures instantiate ServiceError without a details object.
    Keep compatibility local to this service by patching the class init.
    """

    original_init = cast("Any", _ServiceError.__init__)

    def _compat_init(self: _ServiceError, *args: object, **kwargs: object) -> None:
        if "details" not in kwargs and (
            len(args) == 3 or (len(args) == 2 and "status_code" in kwargs)
        ):
            kwargs["details"] = {}
        original_init(self, *args, **kwargs)

    if _ServiceError.__init__ is not _compat_init:
        service_error_class = cast("Any", _ServiceError)
        service_error_class.__init__ = _compat_init


_enable_service_error_compatibility()
