"""Strict validation for additive MP-05 recovery metadata."""

from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import posting_recovery_audit
from db_gateway_service.posting_recovery_audit import (
    PostingRecoveryAdmissionError,
    PostingRecoveryResult,
)
from db_gateway_service.recovery import _open_pinned_directory
from tests.fakes.mp05_posting_recovery_fixtures import FEEDER_HANDLE, Fixture, make_fixture

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _valid() -> dict[str, object]:
    return {
        "applicability": "applicable",
        "operational_ready": True,
        "ledgers": [
            {
                "fingerprint": "a" * 64,
                "owner_status": "verified",
                "state": "ready",
                "finding_codes": [],
            }
        ],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("applicability", []),
        ("operational_ready", 1),
    ],
)
def test_top_level_scalar_types_fail_closed(field: str, value: object) -> None:
    metadata = _valid()
    metadata[field] = value

    with pytest.raises(ValueError, match="posting recovery"):
        PostingRecoveryResult.from_json(metadata)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fingerprint", "A" * 64),
        ("fingerprint", "a" * 63),
        ("owner_status", []),
        ("state", {}),
        ("finding_codes", ["unknown_finding"]),
    ],
)
def test_ledger_fields_are_strict_and_safe(field: str, value: object) -> None:
    metadata = _valid()
    ledger = copy.deepcopy(metadata["ledgers"])[0]  # type: ignore[index]
    ledger[field] = value
    metadata["ledgers"] = [ledger]

    with pytest.raises(ValueError, match="posting recovery"):
        PostingRecoveryResult.from_json(metadata)


def test_duplicate_fingerprints_are_rejected() -> None:
    metadata = _valid()
    ledger = copy.deepcopy(metadata["ledgers"])[0]  # type: ignore[index]
    metadata["ledgers"] = [ledger, copy.deepcopy(ledger)]

    with pytest.raises(ValueError, match="posting recovery"):
        PostingRecoveryResult.from_json(metadata)


@pytest.mark.parametrize(
    ("owner_status", "state", "finding_codes"),
    [
        ("mismatch", "invalid", []),
        ("unverifiable", "invalid", []),
        ("verified", "invalid", []),
        ("verified", "ready", ["posting_journal_invalid"]),
        ("mismatch", "ready", ["posting_owner_binding_mismatch"]),
    ],
)
def test_ledger_state_requires_coherent_blocking_evidence(
    owner_status: str,
    state: str,
    finding_codes: list[str],
) -> None:
    metadata = _valid()
    metadata["operational_ready"] = not finding_codes
    ledger = copy.deepcopy(metadata["ledgers"])[0]  # type: ignore[index]
    ledger.update(
        owner_status=owner_status,
        state=state,
        finding_codes=finding_codes,
    )
    metadata["ledgers"] = [ledger]

    with pytest.raises(ValueError, match="posting recovery"):
        PostingRecoveryResult.from_json(metadata)


def test_invalid_state_cannot_be_marked_operationally_ready() -> None:
    metadata = _valid()
    ledger = copy.deepcopy(metadata["ledgers"])[0]  # type: ignore[index]
    ledger.update(state="invalid", finding_codes=["posting_journal_invalid"])
    metadata["ledgers"] = [ledger]

    with pytest.raises(ValueError, match="posting recovery"):
        PostingRecoveryResult.from_json(metadata)


def _direct_audit(fixture: Fixture) -> PostingRecoveryResult:
    inventory = fixture.inventory
    config_path = next(
        config.captured_path for config in inventory.configs if config.logical_id == "agents"
    )
    roster_path = next(
        resource.captured_path
        for resource in inventory.resources
        if resource.logical_id == "agents.roster"
    )
    runtime = _open_pinned_directory(
        fixture.runtime_root,
        label="posting audit test runtime",
    )
    try:
        return posting_recovery_audit.audit_posting_recovery(
            runtime_directory=runtime,
            inventory=inventory,
            agent_config=json.loads(config_path.read_bytes()),
            roster=json.loads(roster_path.read_bytes()),
            captured_keypairs={FEEDER_HANDLE: {"fingerprint": fixture.fingerprint}},
            identity_findings=(),
        )
    finally:
        runtime.close(primary_error=None)


def test_missing_limits_do_not_read_source_or_record_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_fixture(tmp_path, omit_limit="source_max_bytes")
    reads: list[tuple[str, int | None]] = []
    original = posting_recovery_audit._read_file

    def tracked(parent: object, name: str, *, limit: int | None):
        reads.append((name, limit))
        return original(parent, name, limit=limit)  # type: ignore[arg-type]

    monkeypatch.setattr(posting_recovery_audit, "_read_file", tracked)
    result = _direct_audit(fixture)
    fixture.connection.close()

    assert result.finding_codes == ("posting_limits_invalid",)
    assert all(
        limit is None
        for name, limit in reads
        if name in {"source.jsonl", "head.json", "00000000000000000000.json"}
    )


def test_replaced_postings_directory_is_detected_during_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_fixture(tmp_path)
    original = posting_recovery_audit._read_file
    replaced = False

    def replace_after_head(parent: object, name: str, *, limit: int | None):
        nonlocal replaced
        result = original(parent, name, limit=limit)  # type: ignore[arg-type]
        if name == "head.json" and not replaced:
            replaced = True
            postings = fixture.identity / "postings"
            postings.rename(fixture.identity / "postings-replaced")
            postings.mkdir(mode=0o700)
        return result

    monkeypatch.setattr(posting_recovery_audit, "_read_file", replace_after_head)
    with pytest.raises(PostingRecoveryAdmissionError):
        _direct_audit(fixture)
    fixture.connection.close()
