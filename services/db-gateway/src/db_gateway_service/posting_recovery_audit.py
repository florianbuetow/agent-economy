"""Read-only MP-05 posting-journal recovery audit."""

from __future__ import annotations

import copy
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from service_auth.posting_journal import PostingJournalError, audit_posting_ledger
from service_commons.recovery_contract import (
    CapturedRegistrationState,
    KeypairRole,
    RecoveryInventoryV1,
    RegistrationRequirement,
)

_SERVICE_URL_FIELDS = frozenset(
    {"identity_url", "bank_url", "task_board_url", "reputation_url", "court_url"}
)
_OWNER_FIELDS = frozenset(
    {
        "schema_version",
        "environment_id",
        "role",
        "profile",
        "handle",
        "registered_agent_id",
        "key_fingerprint",
        "service_urls",
    }
)
_RECORD_SUFFIX = ".json"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_FINDING_CODES = frozenset(
    {
        "posting_owner_unverifiable",
        "posting_owner_binding_mismatch",
        "posting_limits_invalid",
        "posting_journal_invalid",
        "posting_permissions_invalid",
        "posting_file_linked",
    }
)
_READY_STATES = frozenset({"empty", "unbound_source", "ready", "adoption_required"})
OwnerStatus = Literal["verified", "unverifiable", "mismatch"]
LedgerState = Literal["empty", "unbound_source", "ready", "adoption_required", "invalid"]


class PostingRecoveryAdmissionError(RuntimeError):
    """A journal path cannot be inspected without losing topology evidence."""

    def __init__(self, reason: Literal["posting_permissions_invalid", "posting_file_linked"]):
        self.reason = reason
        super().__init__(reason)


class PinnedDirectory(Protocol):
    """Minimal recovery-owned pinned directory surface used by this audit."""

    descriptor: int

    def verify(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PostingLedgerRecoveryResult:
    """Operational status of one retained posting ledger."""

    fingerprint: str
    owner_status: OwnerStatus
    state: LedgerState
    finding_codes: tuple[str, ...]

    def model_dump(self, *, mode: str) -> dict[str, object]:
        if mode != "json":
            raise ValueError("PostingLedgerRecoveryResult supports only mode='json'")
        return {
            "fingerprint": self.fingerprint,
            "owner_status": self.owner_status,
            "state": self.state,
            "finding_codes": list(self.finding_codes),
        }


def _validate_ledger_coherence(*, owner_status: str, state: str, finding_codes: list[str]) -> None:
    if state in _READY_STATES:
        if owner_status != "verified" or finding_codes:
            raise ValueError("posting recovery ledger state is invalid")
    elif not finding_codes:
        raise ValueError("posting recovery ledger state is invalid")
    if owner_status == "mismatch" and "posting_owner_binding_mismatch" not in finding_codes:
        raise ValueError("posting recovery owner evidence is invalid")
    if owner_status == "unverifiable" and "posting_owner_unverifiable" not in finding_codes:
        raise ValueError("posting recovery owner evidence is invalid")


def _parse_ledger_result(raw: object) -> PostingLedgerRecoveryResult:
    if not isinstance(raw, dict) or set(raw.keys()) != {
        "fingerprint",
        "owner_status",
        "state",
        "finding_codes",
    }:
        raise ValueError("posting recovery ledger is invalid")
    fingerprint = raw["fingerprint"]
    owner_status = raw["owner_status"]
    state = raw["state"]
    codes = raw["finding_codes"]
    scalar_fields_valid = (
        isinstance(fingerprint, str)
        and _FINGERPRINT.fullmatch(fingerprint) is not None
        and isinstance(owner_status, str)
        and owner_status in {"verified", "unverifiable", "mismatch"}
        and isinstance(state, str)
        and state in {*_READY_STATES, "invalid"}
    )
    codes_valid = (
        isinstance(codes, list)
        and all(isinstance(code, str) for code in codes)
        and codes == sorted(set(codes))
        and all(code in _FINDING_CODES for code in codes)
    )
    if not scalar_fields_valid or not codes_valid:
        raise ValueError("posting recovery ledger is invalid")
    typed_codes = cast("list[str]", codes)
    _validate_ledger_coherence(
        owner_status=cast("str", owner_status),
        state=cast("str", state),
        finding_codes=typed_codes,
    )
    return PostingLedgerRecoveryResult(
        fingerprint=cast("str", fingerprint),
        owner_status=cast("OwnerStatus", owner_status),
        state=cast("LedgerState", state),
        finding_codes=tuple(typed_codes),
    )


@dataclass(frozen=True, slots=True)
class PostingRecoveryResult:
    """Additive recovery metadata computed from retained journal bytes."""

    applicability: Literal["not_applicable", "applicable"]
    operational_ready: bool
    ledgers: tuple[PostingLedgerRecoveryResult, ...]

    @classmethod
    def not_applicable(cls) -> PostingRecoveryResult:
        return cls(applicability="not_applicable", operational_ready=True, ledgers=())

    @classmethod
    def from_json(cls, value: object) -> PostingRecoveryResult:
        if not isinstance(value, dict) or set(value.keys()) != {
            "applicability",
            "operational_ready",
            "ledgers",
        }:
            raise ValueError("posting recovery result is invalid")
        applicability = value["applicability"]
        ready = value["operational_ready"]
        raw_ledgers = value["ledgers"]
        if (
            not isinstance(applicability, str)
            or applicability not in {"not_applicable", "applicable"}
            or type(ready) is not bool
        ):
            raise ValueError("posting recovery result is invalid")
        if not isinstance(raw_ledgers, list):
            raise ValueError("posting recovery ledgers are invalid")
        ledgers = [_parse_ledger_result(raw) for raw in raw_ledgers]
        fingerprints = [ledger.fingerprint for ledger in ledgers]
        if fingerprints != sorted(fingerprints) or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("posting recovery ledgers are not ordered")
        result = cls(
            applicability=cast("Literal['not_applicable', 'applicable']", applicability),
            operational_ready=ready,
            ledgers=tuple(ledgers),
        )
        if (result.applicability == "not_applicable") != (not result.ledgers):
            raise ValueError("posting recovery applicability is invalid")
        if result.applicability == "not_applicable" and not result.operational_ready:
            raise ValueError("posting recovery readiness is invalid")
        calculated_ready = all(
            ledger.owner_status == "verified"
            and ledger.state in _READY_STATES
            and not ledger.finding_codes
            for ledger in result.ledgers
        )
        if result.operational_ready != calculated_ready:
            raise ValueError("posting recovery readiness is invalid")
        return result

    @property
    def finding_codes(self) -> tuple[str, ...]:
        return tuple(sorted({code for ledger in self.ledgers for code in ledger.finding_codes}))

    def model_dump(self, *, mode: str) -> dict[str, object]:
        if mode != "json":
            raise ValueError("PostingRecoveryResult supports only mode='json'")
        return {
            "applicability": self.applicability,
            "operational_ready": self.operational_ready,
            "ledgers": [ledger.model_dump(mode="json") for ledger in self.ledgers],
        }


@dataclass(frozen=True, slots=True)
class _AuditLimits:
    source: int
    record: int
    diagnostics: int


@dataclass(frozen=True, slots=True)
class _ExpectedOwner:
    value: dict[str, object] | None
    limits: _AuditLimits | None


def _strict_positive(mapping: Mapping[str, object], field: str) -> int | None:
    value = mapping.get(field)
    return value if type(value) is int and value > 0 else None


def _audit_limits(
    feeder: Mapping[str, object], runtime: Mapping[str, object]
) -> _AuditLimits | None:
    source_limit = _strict_positive(feeder, "source_max_bytes")
    record_limit = _strict_positive(runtime, "max_record_bytes")
    diagnostic_limit = _strict_positive(runtime, "diagnostic_limit")
    if source_limit is None or record_limit is None or diagnostic_limit is None:
        return None
    return _AuditLimits(source_limit, record_limit, diagnostic_limit)


def _owner_context(
    *,
    feeder: Mapping[str, object],
    runtime: Mapping[str, object],
    platform_value: object,
    roster_agents_value: object,
) -> tuple[str, str, dict[str, object]] | None:
    if not isinstance(platform_value, dict) or not isinstance(roster_agents_value, dict):
        return None
    platform = cast("dict[str, object]", platform_value)
    roster_agents = cast("dict[str, object]", roster_agents_value)
    handle = feeder.get("handle")
    environment_id = runtime.get("environment_id")
    roster_entry = roster_agents.get(handle) if isinstance(handle, str) else None
    valid = (
        isinstance(handle, str)
        and bool(handle)
        and isinstance(environment_id, str)
        and isinstance(roster_entry, dict)
        and roster_entry.get("type") == "poster"
        and frozenset(platform) == _SERVICE_URL_FIELDS
        and all(isinstance(value, str) and bool(value) for value in platform.values())
    )
    if not valid:
        return None
    return cast("str", handle), cast("str", environment_id), platform


def _verified_key_binding(
    *,
    inventory: RecoveryInventoryV1,
    handle: str,
    captured_keypairs: Mapping[str, Mapping[str, object]],
    identity_findings: Sequence[Mapping[str, object]],
) -> tuple[str, str] | None:
    matches = tuple(keypair for keypair in inventory.keypairs if keypair.handle == handle)
    evidence = captured_keypairs.get(handle)
    if len(matches) != 1 or not isinstance(evidence, Mapping) or identity_findings:
        return None
    keypair = matches[0]
    fingerprint = evidence.get("fingerprint")
    agent_id = keypair.expected_registered_agent_id
    if not (
        keypair.role is KeypairRole.ECONOMIC
        and keypair.registration_requirement is RegistrationRequirement.REQUIRED
        and keypair.captured_registration_state is CapturedRegistrationState.REGISTERED
        and isinstance(agent_id, str)
        and agent_id
        and isinstance(fingerprint, str)
        and fingerprint == keypair.expected_public_fingerprint_sha256
    ):
        return None
    return agent_id, fingerprint


def _trusted_owner_value(
    *,
    inventory: RecoveryInventoryV1,
    feeder: Mapping[str, object],
    runtime: Mapping[str, object],
    platform_value: object,
    roster_agents_value: object,
    captured_keypairs: Mapping[str, Mapping[str, object]],
    identity_findings: Sequence[Mapping[str, object]],
) -> dict[str, object] | None:
    context = _owner_context(
        feeder=feeder,
        runtime=runtime,
        platform_value=platform_value,
        roster_agents_value=roster_agents_value,
    )
    if context is None:
        return None
    handle, environment_id, platform = context
    key_binding = _verified_key_binding(
        inventory=inventory,
        handle=handle,
        captured_keypairs=captured_keypairs,
        identity_findings=identity_findings,
    )
    if key_binding is None:
        return None
    agent_id, fingerprint = key_binding
    return {
        "schema_version": 1,
        "environment_id": environment_id,
        "role": "task-feeder",
        "profile": handle,
        "handle": handle,
        "registered_agent_id": agent_id,
        "key_fingerprint": fingerprint,
        "service_urls": copy.deepcopy(platform),
    }


def _expected_owner(
    *,
    inventory: RecoveryInventoryV1,
    agent_config: Mapping[str, object],
    roster: Mapping[str, object],
    captured_keypairs: Mapping[str, Mapping[str, object]],
    identity_findings: Sequence[Mapping[str, object]],
) -> _ExpectedOwner:
    feeder_value = agent_config.get("task_feeder")
    runtime_value = agent_config.get("runtime_state")
    if not isinstance(feeder_value, dict) or not isinstance(runtime_value, dict):
        return _ExpectedOwner(None, None)
    feeder = cast("dict[str, object]", feeder_value)
    runtime = cast("dict[str, object]", runtime_value)
    limits = _audit_limits(feeder, runtime)
    owner = _trusted_owner_value(
        inventory=inventory,
        feeder=feeder,
        runtime=runtime,
        platform_value=agent_config.get("platform"),
        roster_agents_value=roster.get("agents"),
        captured_keypairs=captured_keypairs,
        identity_findings=identity_findings,
    )
    return _ExpectedOwner(owner, limits)


@dataclass(slots=True)
class _PinnedChild:
    parent: PinnedDirectory
    name: str
    descriptor: int
    device: int
    inode: int
    mode: int

    def verify(self) -> None:
        self.parent.verify()
        opened = os.fstat(self.descriptor)
        visible = os.stat(self.name, dir_fd=self.parent.descriptor, follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or opened.st_dev != self.device
            or opened.st_ino != self.inode
            or visible.st_dev != self.device
            or visible.st_ino != self.inode
            or stat.S_IMODE(opened.st_mode) != self.mode
            or stat.S_IMODE(visible.st_mode) != self.mode
        ):
            raise PostingRecoveryAdmissionError("posting_permissions_invalid")


def _open_child(
    parent: PinnedDirectory,
    name: str,
    *,
    mode: int,
    missing_ok: bool,
) -> _PinnedChild | None:
    parent.verify()
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
    except FileNotFoundError:
        if missing_ok:
            parent.verify()
            return None
        raise PostingRecoveryAdmissionError("posting_permissions_invalid") from None
    except OSError as exc:
        raise PostingRecoveryAdmissionError("posting_permissions_invalid") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != mode:
        os.close(descriptor)
        raise PostingRecoveryAdmissionError("posting_permissions_invalid")
    child = _PinnedChild(parent, name, descriptor, metadata.st_dev, metadata.st_ino, mode)
    child.verify()
    return child


def _read_file(
    parent: PinnedDirectory,
    name: str,
    *,
    limit: int | None,
) -> tuple[bytes | None, str | None, bool]:
    parent.verify()
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent.descriptor,
        )
    except FileNotFoundError:
        parent.verify()
        return None, None, False
    except OSError:
        return None, "posting_permissions_invalid", True
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None, "posting_permissions_invalid", True
        if before.st_nlink != 1:
            raise PostingRecoveryAdmissionError("posting_file_linked")
        finding = None
        if stat.S_IMODE(before.st_mode) != 0o600:
            finding = "posting_permissions_invalid"
        data: bytes | None = None
        if limit is not None:
            chunks: list[bytes] = []
            offset = 0
            remaining = limit + 1
            while remaining:
                chunk = os.pread(descriptor, min(1024 * 1024, remaining), offset)
                if not chunk:
                    break
                chunks.append(chunk)
                offset += len(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
        after = os.fstat(descriptor)
        visible = os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)
        if after.st_nlink != 1 or visible.st_nlink != 1:
            raise PostingRecoveryAdmissionError("posting_file_linked")
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or after.st_dev != visible.st_dev
            or after.st_ino != visible.st_ino
            or before.st_size != after.st_size
            or before.st_mode != after.st_mode
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or after.st_mode != visible.st_mode
            or after.st_size != visible.st_size
            or after.st_mtime_ns != visible.st_mtime_ns
            or after.st_ctime_ns != visible.st_ctime_ns
        ):
            raise PostingRecoveryAdmissionError("posting_permissions_invalid")
        parent.verify()
        return data, finding, True
    finally:
        os.close(descriptor)


def _json_owner(data: bytes | None) -> dict[str, object] | None:
    if data is None:
        return None
    try:
        value: object = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or frozenset(value) != _OWNER_FIELDS:
        return None
    return cast("dict[str, object]", value)


@dataclass(frozen=True, slots=True)
class _LedgerPayload:
    source_bytes: bytes | None
    head_bytes: bytes | None
    record_bytes: dict[str, bytes]
    finding_codes: frozenset[str]


def _read_ledger_payload(
    postings: _PinnedChild,
    records: _PinnedChild,
    *,
    limits: _AuditLimits | None,
) -> _LedgerPayload:
    with os.scandir(postings.descriptor) as entries:
        namespace_names = sorted(entry.name for entry in entries)
    unexpected_names = set(namespace_names) - {"source.jsonl", "head.json", "records"}
    findings = {"posting_journal_invalid"} if unexpected_names else set()
    for unexpected_name in sorted(unexpected_names):
        _data, mode_finding, _present = _read_file(postings, unexpected_name, limit=None)
        if mode_finding is not None:
            findings.add(mode_finding)
    source_limit = limits.source if limits is not None else None
    record_limit = limits.record if limits is not None else None
    source_bytes, source_mode_finding, _source_present = _read_file(
        postings, "source.jsonl", limit=source_limit
    )
    head_bytes, head_mode_finding, _head_present = _read_file(
        postings, "head.json", limit=record_limit
    )
    findings.update(
        finding for finding in (source_mode_finding, head_mode_finding) if finding is not None
    )
    record_bytes: dict[str, bytes] = {}
    with os.scandir(records.descriptor) as entries:
        record_names = sorted(entry.name for entry in entries)
    for name in record_names:
        if not name.endswith(_RECORD_SUFFIX):
            findings.add("posting_journal_invalid")
            continue
        data, mode_finding, present = _read_file(records, name, limit=record_limit)
        if not present or (record_limit is not None and data is None):
            findings.add("posting_journal_invalid")
        elif data is not None:
            record_bytes[name] = data
        if mode_finding is not None:
            findings.add(mode_finding)
    records.verify()
    with os.scandir(postings.descriptor) as entries:
        if sorted(entry.name for entry in entries) != namespace_names:
            raise PostingRecoveryAdmissionError("posting_permissions_invalid")
    postings.verify()
    return _LedgerPayload(source_bytes, head_bytes, record_bytes, frozenset(findings))


def _owner_assessment(
    owner: dict[str, object] | None,
    *,
    fingerprint: str,
    expected: _ExpectedOwner,
) -> tuple[OwnerStatus, set[str]]:
    if expected.value is None or owner is None:
        return "unverifiable", {"posting_owner_unverifiable"}
    if owner != expected.value or fingerprint != expected.value["key_fingerprint"]:
        return "mismatch", {"posting_owner_binding_mismatch"}
    return "verified", set()


def _finalize_ledger_result(
    *,
    fingerprint: str,
    expected: _ExpectedOwner,
    owner: dict[str, object] | None,
    owner_mode_finding: str | None,
    payload: _LedgerPayload,
) -> PostingLedgerRecoveryResult:
    owner_status, findings = _owner_assessment(
        owner,
        fingerprint=fingerprint,
        expected=expected,
    )
    findings.update(payload.finding_codes)
    if owner_mode_finding is not None:
        findings.add(owner_mode_finding)
    if expected.limits is None:
        findings.add("posting_limits_invalid")
    state: LedgerState = "invalid"
    can_audit = owner_status == "verified" and owner is not None and expected.limits is not None
    if can_audit:
        assert owner is not None and expected.limits is not None
        try:
            audited = audit_posting_ledger(
                owner_json=owner,
                source_bytes=payload.source_bytes,
                head_bytes=payload.head_bytes,
                record_bytes=payload.record_bytes,
                max_source_bytes=expected.limits.source,
                max_record_bytes=expected.limits.record,
                diagnostic_limit=expected.limits.diagnostics,
            )
        except PostingJournalError:
            findings.add("posting_journal_invalid")
        else:
            if audited.findings:
                findings.add("posting_journal_invalid")
            else:
                state = audited.state
    if findings:
        state = "invalid"
    return PostingLedgerRecoveryResult(
        fingerprint=fingerprint,
        owner_status=owner_status,
        state=state,
        finding_codes=tuple(sorted(findings)),
    )


def _audit_identity(
    identities: PinnedDirectory,
    *,
    fingerprint: str,
    expected: _ExpectedOwner,
) -> PostingLedgerRecoveryResult | None:
    if _FINGERPRINT.fullmatch(fingerprint) is None:
        raise PostingRecoveryAdmissionError("posting_permissions_invalid")
    identity = _open_child(identities, fingerprint, mode=0o700, missing_ok=False)
    assert identity is not None
    postings: _PinnedChild | None = None
    records: _PinnedChild | None = None
    try:
        postings = _open_child(identity, "postings", mode=0o700, missing_ok=True)
        if postings is None:
            return None
        records = _open_child(postings, "records", mode=0o700, missing_ok=False)
        assert records is not None
        owner_limit = (
            expected.limits.record
            if expected.limits is not None
            else len(json.dumps(expected.value, separators=(",", ":"), sort_keys=True).encode()) + 1
            if expected.value is not None
            else None
        )
        owner_bytes, owner_mode_finding, _owner_present = _read_file(
            identity, "owner.json", limit=owner_limit
        )
        owner = _json_owner(owner_bytes)
        payload = _read_ledger_payload(postings, records, limits=expected.limits)
        return _finalize_ledger_result(
            fingerprint=fingerprint,
            expected=expected,
            owner=owner,
            owner_mode_finding=owner_mode_finding,
            payload=payload,
        )
    finally:
        if records is not None:
            os.close(records.descriptor)
        if postings is not None:
            os.close(postings.descriptor)
        os.close(identity.descriptor)


def audit_posting_recovery(
    *,
    runtime_directory: PinnedDirectory | None,
    inventory: RecoveryInventoryV1,
    agent_config: Mapping[str, object] | None,
    roster: Mapping[str, object] | None,
    captured_keypairs: Mapping[str, Mapping[str, object]],
    identity_findings: Sequence[Mapping[str, object]],
) -> PostingRecoveryResult:
    """Audit every retained posting namespace without mutating journal bytes."""

    if runtime_directory is None:
        return PostingRecoveryResult.not_applicable()
    identities = _open_child(runtime_directory, "identities", mode=0o700, missing_ok=True)
    if identities is None:
        return PostingRecoveryResult.not_applicable()
    try:
        with os.scandir(identities.descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        expected = (
            _expected_owner(
                inventory=inventory,
                agent_config=agent_config,
                roster=roster,
                captured_keypairs=captured_keypairs,
                identity_findings=identity_findings,
            )
            if agent_config is not None and roster is not None
            else _ExpectedOwner(None, None)
        )
        audited = (
            _audit_identity(
                identities,
                fingerprint=name,
                expected=expected,
            )
            for name in names
        )
        ledgers = tuple(ledger for ledger in audited if ledger is not None)
        with os.scandir(identities.descriptor) as entries:
            if sorted(entry.name for entry in entries) != names:
                raise PostingRecoveryAdmissionError("posting_permissions_invalid")
        identities.verify()
    finally:
        os.close(identities.descriptor)
    if not ledgers:
        return PostingRecoveryResult.not_applicable()
    return PostingRecoveryResult(
        applicability="applicable",
        operational_ready=all(not ledger.finding_codes for ledger in ledgers),
        ledgers=ledgers,
    )


def admit_posting_recovery_source(
    runtime_directory: PinnedDirectory | None,
) -> None:
    """Reject source topology that an ordinary byte copy would erase."""

    if runtime_directory is None:
        return
    identities = _open_child(runtime_directory, "identities", mode=0o700, missing_ok=True)
    if identities is None:
        return
    try:
        with os.scandir(identities.descriptor) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            identity = _open_child(identities, name, mode=0o700, missing_ok=False)
            assert identity is not None
            postings: _PinnedChild | None = None
            records: _PinnedChild | None = None
            try:
                postings = _open_child(identity, "postings", mode=0o700, missing_ok=True)
                if postings is None:
                    continue
                records = _open_child(postings, "records", mode=0o700, missing_ok=False)
                assert records is not None
                with os.scandir(postings.descriptor) as entries:
                    namespace_names = sorted(entry.name for entry in entries)
                _read_file(identity, "owner.json", limit=None)
                _read_file(postings, "source.jsonl", limit=None)
                _read_file(postings, "head.json", limit=None)
                for unexpected_name in set(namespace_names) - {
                    "source.jsonl",
                    "head.json",
                    "records",
                }:
                    _read_file(postings, unexpected_name, limit=None)
                with os.scandir(records.descriptor) as entries:
                    record_names = sorted(entry.name for entry in entries)
                for record_name in record_names:
                    _read_file(records, record_name, limit=None)
                records.verify()
                with os.scandir(postings.descriptor) as entries:
                    if sorted(entry.name for entry in entries) != namespace_names:
                        raise PostingRecoveryAdmissionError("posting_permissions_invalid")
                postings.verify()
            finally:
                if records is not None:
                    os.close(records.descriptor)
                if postings is not None:
                    os.close(postings.descriptor)
                os.close(identity.descriptor)
        with os.scandir(identities.descriptor) as entries:
            if sorted(entry.name for entry in entries) != names:
                raise PostingRecoveryAdmissionError("posting_permissions_invalid")
        identities.verify()
    finally:
        os.close(identities.descriptor)


__all__ = [
    "PinnedDirectory",
    "PostingLedgerRecoveryResult",
    "PostingRecoveryAdmissionError",
    "PostingRecoveryResult",
    "admit_posting_recovery_source",
    "audit_posting_recovery",
]
