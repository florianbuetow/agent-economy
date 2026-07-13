"""WP-06.5 de-hardcode: MockJudge worker_pct and deliverable cap come from config."""

from __future__ import annotations

import pytest

from court_service.config import (
    JudgeConfig,
    JudgesConfig,
    PlatformConfig,
    Settings,
)
from court_service.core.lifespan import _build_judges
from court_service.judges.base import MockJudge


def _settings_with(mock_worker_pct: int, max_deliverable_bytes: int) -> Settings:
    return Settings(
        service={"name": "court", "version": "0.1.0"},
        server={"host": "127.0.0.1", "port": 8005, "log_level": "info"},
        logging={"level": "WARNING", "directory": "data/logs"},
        platform=PlatformConfig(agent_id="a-platform"),
        disputes={
            "rebuttal_deadline_seconds": 86400,
            "max_claim_length": 10000,
            "max_rebuttal_length": 10000,
            "feedback_extremely_satisfied_cutoff": 80,
            "feedback_satisfied_cutoff": 40,
            "feedback_comment_max_length": 256,
        },
        judges=JudgesConfig(
            panel_size=1,
            mock_worker_pct=mock_worker_pct,
            max_deliverable_bytes=max_deliverable_bytes,
            judges=[JudgeConfig(id="judge-0", provider="mock", model="mock-judge")],
        ),
        request={"max_body_size": 1048576},
        db_gateway={"url": "http://127.0.0.1:8007", "timeout_seconds": 10},
    )


@pytest.mark.unit
def test_mock_judge_uses_configured_worker_pct() -> None:
    """_build_judges must source the MockJudge fixed worker_pct from config."""
    settings = _settings_with(mock_worker_pct=37, max_deliverable_bytes=65536)
    judges = _build_judges(settings)
    assert len(judges) == 1
    judge = judges[0]
    assert isinstance(judge, MockJudge)
    assert judge._fixed_worker_pct == 37


@pytest.mark.unit
def test_max_deliverable_bytes_is_required_config() -> None:
    """max_deliverable_bytes is a required judges config value."""
    settings = _settings_with(mock_worker_pct=50, max_deliverable_bytes=4096)
    assert settings.judges.max_deliverable_bytes == 4096
