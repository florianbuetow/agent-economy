"""WP-06.5 / plan 3.1-3.2: MockJudge findings and evidence limits come from config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from court_service.config import (
    EvidenceConfig,
    JudgeConfig,
    JudgesConfig,
    PlatformConfig,
    Settings,
)
from court_service.core.lifespan import _build_judges
from court_service.judges.base import MockJudge


def _settings_with(mock_worker_pct: int, max_file_bytes: int) -> Settings:
    return Settings(
        service={"name": "court", "version": "0.1.0"},
        server={"host": "127.0.0.1", "port": 8005, "log_level": "info"},
        telemetry={
            "enabled": False,
            "otlp_endpoint": "http://127.0.0.1:4318",
            "sample_ratio": 1.0,
            "excluded_urls": "health",
        },
        logging={"level": "WARNING", "directory": "data/logs"},
        platform=PlatformConfig(agent_id="a-platform", agent_config_path="agents.yaml"),
        disputes={
            "rebuttal_deadline_seconds": 86400,
            "max_claim_length": 10000,
            "max_rebuttal_length": 10000,
            "feedback_extremely_satisfied_cutoff": 80,
            "feedback_satisfied_cutoff": 40,
            "feedback_comment_max_length": 256,
            "judging_lease_seconds": 60,
            "reclaim_interval_seconds": 3600,
        },
        judges=JudgesConfig(
            panel_size=1,
            mock_worker_pct=mock_worker_pct,
            mock_spec_quality="extremely_satisfied",
            mock_delivery_quality="dissatisfied",
            judges=[JudgeConfig(id="judge-0", provider="mock", model="mock-judge")],
        ),
        evidence=EvidenceConfig(
            max_total_bytes=262144,
            max_file_bytes=max_file_bytes,
            allowed_extensions=[".txt"],
            allowed_content_types=["text/plain"],
        ),
        request={"max_body_size": 1048576},
        db_gateway={
            "url": "http://127.0.0.1:8007",
            "timeout_seconds": 10,
            "signing": {
                "kid": "gateway-court",
                "private_key_path": "gateway-court.key",
                "audience": "db-gateway",
                "token_ttl_seconds": 300,
            },
        },
    )


@pytest.mark.unit
def test_mock_judge_uses_configured_worker_pct() -> None:
    """_build_judges must source the MockJudge fixed worker_pct from config."""
    settings = _settings_with(mock_worker_pct=37, max_file_bytes=65536)
    judges = _build_judges(settings)
    assert len(judges) == 1
    judge = judges[0]
    assert isinstance(judge, MockJudge)
    assert judge._findings.worker_pct == 37
    assert judge._findings.spec_quality == "extremely_satisfied"
    assert judge._findings.delivery_quality == "dissatisfied"


@pytest.mark.unit
def test_evidence_limits_are_required_config() -> None:
    """evidence.max_file_bytes is a required config value."""
    settings = _settings_with(mock_worker_pct=50, max_file_bytes=4096)
    assert settings.evidence.max_file_bytes == 4096


@pytest.mark.unit
@pytest.mark.parametrize("rating", ["ok", "", "Satisfied"])
def test_unknown_mock_rating_is_rejected(rating: str) -> None:
    """Mock findings must use the Reputation rating vocabulary."""
    with pytest.raises(ValueError, match="mock_spec_quality"):
        JudgesConfig(
            panel_size=1,
            mock_worker_pct=50,
            mock_spec_quality=rating,
            mock_delivery_quality="satisfied",
            judges=[JudgeConfig(id="judge-0", provider="mock", model="mock-judge")],
        )


@pytest.mark.unit
def test_shipped_profile_is_a_three_judge_openai_compatible_panel() -> None:
    """Plan 3.2 / rule 5: the shipped config.yaml is three OpenAI-compatible judges."""
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    settings = Settings.model_validate(yaml.safe_load(config_path.read_text()))

    assert settings.judges.panel_size == 3
    assert len({judge.id for judge in settings.judges.judges}) == 3
    for judge in settings.judges.judges:
        assert judge.provider == "llm"
        assert judge.model.startswith("openai/")
        assert judge.api_base is not None
        assert judge.api_key_env is not None
        assert judge.temperature is not None
