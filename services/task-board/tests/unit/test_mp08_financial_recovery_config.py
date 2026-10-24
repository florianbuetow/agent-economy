"""MP-08 explicit financial-recovery configuration acceptance contract."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def _config_type() -> type[Any]:
    module = importlib.import_module("task_board_service.config")
    value = getattr(module, "FinancialEffectRecoveryConfig", None)
    if not isinstance(value, type):
        pytest.fail("Missing FinancialEffectRecoveryConfig capability")
    return value


def _values() -> dict[str, object]:
    return {
        "interval_seconds": 2.0,
        "max_delivery_attempts_per_cycle": 10,
    }


def test_operational_values_are_explicit_and_preserved_exactly() -> None:
    config = _config_type().model_validate(_values())

    assert config.model_dump() == _values()


@pytest.mark.parametrize("field", list(_values()))
def test_no_recovery_setting_has_an_implicit_default(field: str) -> None:
    values = _values()
    del values[field]

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("interval_seconds", 0),
        ("interval_seconds", float("inf")),
        ("max_delivery_attempts_per_cycle", 0),
    ],
)
def test_nonpositive_or_nonfinite_values_fail_fast(field: str, value: object) -> None:
    values = _values()
    values[field] = value

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


@pytest.mark.parametrize(
    "field",
    ["max_delivery_attempts_per_cycle"],
)
@pytest.mark.parametrize("value", [True, "10", 1.5])
def test_integer_settings_reject_bools_strings_and_fractions(
    field: str,
    value: object,
) -> None:
    values = _values()
    values[field] = value

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


@pytest.mark.parametrize("value", [True, "2.0", float("nan"), float("inf")])
def test_interval_rejects_bools_strings_and_nonfinite_values(value: object) -> None:
    values = _values()
    values["interval_seconds"] = value

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


def test_extra_recovery_configuration_is_rejected() -> None:
    values = _values()
    values["hidden_default"] = 1

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


@pytest.mark.parametrize("cycle_limit", [9, 11])
def test_production_cycle_limit_is_exactly_ten(cycle_limit: int) -> None:
    values = _values()
    values["max_delivery_attempts_per_cycle"] = cycle_limit

    with pytest.raises(ValidationError):
        _config_type().model_validate(values)


def test_shipped_config_has_explicit_board_bounds_and_dependency_timeouts() -> None:
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    document = yaml.safe_load(config_path.read_text())

    assert document["financial_effect_recovery"] == _values()
    assert document["db_gateway"]["timeout_seconds"] == 10
    assert document["central_bank"]["timeout_seconds"] == 10
