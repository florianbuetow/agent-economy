"""Tests for configuration loading."""

from __future__ import annotations

import pytest

from reputation_service.config import get_settings


@pytest.mark.unit
class TestSettingsLoad:
    """Test that settings load correctly from config.yaml."""

    def test_settings_load_from_config_yaml(self) -> None:
        """Settings should load successfully from config.yaml."""
        settings = get_settings()
        assert settings is not None

    def test_service_section_exists(self) -> None:
        """Settings must have a service section with name and version."""
        settings = get_settings()
        assert settings.service is not None
        assert isinstance(settings.service.name, str)
        assert isinstance(settings.service.version, str)

    def test_server_section_exists(self) -> None:
        """Settings must have a server section with host, port, log_level."""
        settings = get_settings()
        assert settings.server is not None
        assert isinstance(settings.server.host, str)
        assert isinstance(settings.server.port, int)
        assert isinstance(settings.server.log_level, str)

    def test_logging_section_exists(self) -> None:
        """Settings must have a logging section with level and directory."""
        settings = get_settings()
        assert settings.logging is not None
        assert isinstance(settings.logging.level, str)
        assert isinstance(settings.logging.directory, str)

    def test_feedback_section_exists(self) -> None:
        """Settings must have a feedback section."""
        settings = get_settings()
        assert settings.feedback is not None

    def test_feedback_has_reveal_timeout_seconds(self) -> None:
        """Feedback config must have reveal_timeout_seconds."""
        settings = get_settings()
        assert isinstance(settings.feedback.reveal_timeout_seconds, int)
        assert settings.feedback.reveal_timeout_seconds > 0

    def test_feedback_has_max_comment_length(self) -> None:
        """Feedback config must have max_comment_length."""
        settings = get_settings()
        assert isinstance(settings.feedback.max_comment_length, int)
        assert settings.feedback.max_comment_length > 0

    def test_identity_section_exists(self) -> None:
        """Settings must have an identity section."""
        settings = get_settings()
        assert settings.identity is not None
        assert isinstance(settings.identity.base_url, str)
        assert isinstance(settings.identity.verify_jws_path, str)
        assert isinstance(settings.identity.timeout_seconds, int)

    def test_request_section_exists(self) -> None:
        """Settings must have a request section with max_body_size."""
        settings = get_settings()
        assert settings.request is not None
        assert isinstance(settings.request.max_body_size, int)
        assert settings.request.max_body_size > 0

    def test_settings_are_cached(self) -> None:
        """Calling get_settings twice returns the same object."""
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_service_name_is_reputation(self) -> None:
        """Service name should be 'reputation'."""
        settings = get_settings()
        assert settings.service.name == "reputation"

    def test_server_port_is_8004(self) -> None:
        """Server port should be 8004."""
        settings = get_settings()
        assert settings.server.port == 8004
