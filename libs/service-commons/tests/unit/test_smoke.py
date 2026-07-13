"""Smoke tests: keep pytest from failing with exit code 5 (no tests collected)
and prove the package's public modules are importable.

service_commons had no CI/test wiring at all before WP-11; this is the minimal
scaffold, not acceptance coverage of its behavior (that is consumed and
exercised indirectly by every service that depends on this library).
"""

from __future__ import annotations

import pytest

import service_commons
from service_commons import config, exceptions
from service_commons import logging as service_logging


@pytest.mark.unit
def test_package_importable() -> None:
    """The service_commons package imports cleanly."""
    assert service_commons is not None


@pytest.mark.unit
def test_exceptions_module_exports() -> None:
    """The exceptions module exposes its documented public API."""
    assert exceptions.ServiceError is not None
    assert callable(exceptions.create_exception_handlers)
    assert callable(exceptions.register_exception_handlers)
    assert callable(exceptions.middleware_error_response)


@pytest.mark.unit
def test_logging_module_exports() -> None:
    """The logging module exposes its documented public API."""
    assert callable(service_logging.setup_logging)
    assert callable(service_logging.get_service_logger)
    assert callable(service_logging.get_named_logger)


@pytest.mark.unit
def test_config_module_exports() -> None:
    """The config module exposes its documented public API."""
    assert callable(config.create_settings_loader)
    assert callable(config.get_config_path)
    assert callable(config.get_safe_model_config)
