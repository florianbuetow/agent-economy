"""MP-09 H1 pure canonical contract for migrated legacy asset evidence."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import operator
from dataclasses import FrozenInstanceError
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING, Any, Final
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SQLITE_MAX_INTEGER

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

pytestmark = pytest.mark.unit

_MODULE: Final[str] = "db_gateway_service.services.legacy_asset_observation"
_PLAN_ID: Final[str] = "a" * 64
_BODY: Final[bytes] = b"legacy evidence\n"
_OBSERVED_SHA: Final[str] = hashlib.sha256(_BODY).hexdigest()
_OBSERVED_US: Final[int] = 4_070_995_200_123_456
_SET_UUID_A: Final[str] = str(uuid5(NAMESPACE_URL, "a"))
_SET_UUID_Z: Final[str] = str(uuid5(NAMESPACE_URL, "z"))
_SET_KEY_A: Final[str] = f"live/{_SET_UUID_A[:2]}/{_SET_UUID_A}.blob"
_SET_KEY_Z: Final[str] = f"live/{_SET_UUID_Z[:2]}/{_SET_UUID_Z}.blob"
_SOURCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "asset_id",
        "task_id",
        "uploader_id",
        "filename",
        "content_type",
        "size_bytes",
        "storage_path",
        "content_hash",
        "uploaded_at",
    }
)
_SOURCE_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "asset_id",
    "task_id",
    "uploader_id",
    "filename",
    "content_type",
    "storage_path",
    "uploaded_at",
)
_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "format_version",
        "migration_plan_id",
        "legacy_asset_id",
        "source_row",
        "source_row_sha256",
        "assigned_worker_id",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "observed_size_bytes",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
        "observed_at_us",
    }
)
_ROW_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "observation_id",
        "observation_contract_version",
        "migration_plan_id",
        "legacy_asset_id",
        "task_id",
        "uploader_id",
        "assigned_worker_id",
        "source_row_sha256",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "canonical_observation_json",
        "observation_sha256",
        "observed_at_us",
        "observed_size_bytes",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
    }
)


def _api() -> Any:
    spec = importlib.util.find_spec(_MODULE)
    assert spec is not None, f"Missing H1 capability: {_MODULE}"
    module = importlib.import_module(_MODULE)
    for symbol in (
        "CanonicalLegacyAssetSourceRow",
        "CanonicalLegacyAssetSourceContext",
        "MigratedArtifactIdentity",
        "CanonicalLegacyAssetObservation",
        "CanonicalAssetSet",
        "parse_legacy_asset_source_row",
        "validate_legacy_asset_source_context",
        "derive_legacy_asset_observation_id",
        "derive_migrated_artifact_identity",
        "parse_legacy_asset_observation",
        "validate_legacy_asset_observation_row",
        "canonical_asset_set",
    ):
        assert hasattr(module, symbol), f"Missing H1 capability: {symbol}"
    return module


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _observation_id(asset_id: str = "asset-opaque-世界") -> str:
    return "obs-" + str(
        uuid5(
            NAMESPACE_URL,
            f"urn:agent-economy:board-legacy-asset-observation:v1:{_PLAN_ID}:{asset_id}",
        )
    )


def _artifact_identity(asset_id: str = "asset-opaque-世界") -> tuple[str, str]:
    artifact_id = str(
        uuid5(
            NAMESPACE_URL,
            f"urn:agent-economy:board-artifact-v6:{_PLAN_ID}:{asset_id}",
        )
    )
    return artifact_id, f"live/{artifact_id[:2]}/{artifact_id}.blob"


def _source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "asset_id": "asset-opaque-世界",
        "task_id": "task-legacy",
        "uploader_id": "worker-legacy",
        "filename": "Résumé 世界.txt",
        "content_type": "application/x-historical",
        "size_bytes": len(_BODY),
        "storage_path": "historical/opaque/path.bin",
        "content_hash": None,
        "uploaded_at": "2099-01-02T00:00:00.123456+00:00",
    }
    value.update(overrides)
    assert frozenset(value) == _SOURCE_FIELDS
    return value


def _observation(**overrides: object) -> dict[str, object]:
    source = _source()
    source_json = _canonical(source)
    artifact_id, object_key = _artifact_identity()
    value: dict[str, object] = {
        "format_version": 1,
        "migration_plan_id": _PLAN_ID,
        "legacy_asset_id": source["asset_id"],
        "source_row": source,
        "source_row_sha256": _sha(source_json),
        "assigned_worker_id": source["uploader_id"],
        "attribution_status": "verified_worker",
        "byte_status": "verified",
        "declared_hash_status": "absent",
        "copy_status": "installed",
        "observed_size_bytes": len(_BODY),
        "observed_sha256": _OBSERVED_SHA,
        "generated_artifact_id": artifact_id,
        "generated_object_key": object_key,
        "observed_at_us": _OBSERVED_US,
    }
    value.update(overrides)
    assert frozenset(value) == _OBSERVATION_FIELDS
    return value


def _row(observation: Mapping[str, object] | None = None, **overrides: object) -> dict[str, object]:
    value = dict(_observation() if observation is None else observation)
    canonical = _canonical(value)
    row: dict[str, object] = {
        "observation_id": _observation_id(str(value["legacy_asset_id"])),
        "observation_contract_version": 1,
        "migration_plan_id": value["migration_plan_id"],
        "legacy_asset_id": value["legacy_asset_id"],
        "task_id": value["source_row"]["task_id"],
        "uploader_id": value["source_row"]["uploader_id"],
        "assigned_worker_id": value["assigned_worker_id"],
        "source_row_sha256": value["source_row_sha256"],
        "attribution_status": value["attribution_status"],
        "byte_status": value["byte_status"],
        "declared_hash_status": value["declared_hash_status"],
        "copy_status": value["copy_status"],
        "canonical_observation_json": canonical,
        "observation_sha256": _sha(canonical),
        "observed_at_us": value["observed_at_us"],
        "observed_size_bytes": value["observed_size_bytes"],
        "observed_sha256": value["observed_sha256"],
        "generated_artifact_id": value["generated_artifact_id"],
        "generated_object_key": value["generated_object_key"],
    }
    row.update(overrides)
    assert frozenset(row) == _ROW_FIELDS
    return row


def _valid_set_entry(kind: str) -> list[object]:
    if kind == "source_rows":
        return ["asset", "a" * 64]
    if kind == "asset_observations":
        return ["obs-" + _SET_UUID_A, "a" * 64]
    if kind == "observed_bytes":
        return ["asset", len(_BODY), "a" * 64]
    assert kind == "generated_copies"
    return [_SET_UUID_A, _SET_KEY_A, len(_BODY), "a" * 64]


def _invalid(call: Callable[[], object], error: str = "legacy_asset_observation_invalid") -> None:
    with pytest.raises(ServiceError) as raised:
        call()
    assert (raised.value.status_code, raised.value.error) == (500, error)


def _set_attribute(value: object, name: str, replacement: object) -> None:
    setattr(value, name, replacement)


def _valid_context(api: Any, source_value: dict[str, object] | None = None) -> Any:
    source = api.parse_legacy_asset_source_row(_source() if source_value is None else source_value)
    return api.validate_legacy_asset_source_context(
        source,
        task={"task_id": source.values["task_id"], "worker_id": source.values["uploader_id"]},
        uploader={"agent_id": source.values["uploader_id"]},
        worker={"agent_id": source.values["uploader_id"]},
    )


def test_source_parser_returns_exact_canonical_immutable_copy() -> None:
    api = _api()
    source = _source()
    expected = _canonical(source)
    parsed = api.parse_legacy_asset_source_row(source)
    assert type(parsed) is api.CanonicalLegacyAssetSourceRow
    assert not hasattr(parsed, "__dict__")
    assert type(parsed.values) is MappingProxyType
    assert dict(parsed.values) == source
    assert parsed.canonical_json == expected
    assert parsed.source_row_sha256 == _sha(expected)
    assert "Résumé 世界.txt" in expected and "\\u00e9" not in expected
    source["filename"] = "changed"
    assert parsed.values["filename"] == "Résumé 世界.txt"
    with pytest.raises(TypeError):
        operator.setitem(parsed.values, "filename", "denied")
    with pytest.raises(FrozenInstanceError):
        _set_attribute(parsed, "canonical_json", "changed")


def test_source_parser_preserves_unicode_without_normalizing_equivalent_text() -> None:
    api = _api()
    parsed_composed = api.parse_legacy_asset_source_row(_source(filename="Résumé.txt"))
    parsed_decomposed = api.parse_legacy_asset_source_row(
        _source(filename="Re\u0301sume\u0301.txt")
    )
    assert parsed_composed.values["filename"] == "Résumé.txt"
    assert parsed_decomposed.values["filename"] == "Re\u0301sume\u0301.txt"
    assert parsed_composed.canonical_json != parsed_decomposed.canonical_json
    assert parsed_composed.source_row_sha256 != parsed_decomposed.source_row_sha256


@pytest.mark.parametrize("defect", ["not_mapping", "missing", "extra"])
def test_source_parser_requires_one_exact_nine_field_mapping(defect: str) -> None:
    api = _api()
    value: object = _source()
    if defect == "not_mapping":
        value = []
    elif defect == "missing":
        del value["filename"]
    else:
        value["extension"] = "forbidden"
    _invalid(lambda: api.parse_legacy_asset_source_row(value), "source_asset_row_invalid")


@pytest.mark.parametrize("field", _SOURCE_TEXT_FIELDS[:-1])
@pytest.mark.parametrize("replacement", [None, 1, ""])
def test_every_required_source_text_rejects_null_nontext_and_empty(
    field: str,
    replacement: object,
) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(**{field: replacement})),
        "source_asset_row_invalid",
    )


@pytest.mark.parametrize("replacement", [None, 1, ""])
def test_source_uploaded_at_rejects_null_nontext_and_empty(replacement: object) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(uploaded_at=replacement)),
        "source_asset_row_invalid",
    )


@pytest.mark.parametrize("field", _SOURCE_TEXT_FIELDS)
def test_source_text_rejects_non_utf8_surrogates(field: str) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(**{field: "bad\ud800text"})),
        "source_asset_row_invalid",
    )


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", SQLITE_MAX_INTEGER + 1])
def test_source_size_rejects_bool_coercion_negative_float_string_and_overflow(
    value: object,
) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(size_bytes=value)),
        "source_asset_row_invalid",
    )


@pytest.mark.parametrize("value", [0, SQLITE_MAX_INTEGER])
def test_source_size_accepts_exact_nonnegative_sqlite_boundaries(value: int) -> None:
    api = _api()
    parsed = api.parse_legacy_asset_source_row(_source(size_bytes=value))
    assert parsed.values["size_bytes"] == value


@pytest.mark.parametrize("value", [None, "0" * 64, "a1" * 32])
def test_source_hash_accepts_null_or_lowercase_hex(value: str | None) -> None:
    api = _api()
    parsed = api.parse_legacy_asset_source_row(_source(content_hash=value))
    assert parsed.values["content_hash"] == value


@pytest.mark.parametrize("value", [1, "A" * 64, "a" * 63, "g" * 64])
def test_source_hash_rejects_wrong_type_case_length_and_alphabet(value: object) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(content_hash=value)),
        "source_asset_row_invalid",
    )


@pytest.mark.parametrize(
    "value",
    [
        "2099-01-02T00:00:00Z",
        "2099-01-02T00:00:00+00:00",
        "2099-01-02T00:00:00.1Z",
        "2099-01-02T00:00:00.123456+00:00",
    ],
)
def test_source_utc_accepts_exact_supported_forms_and_preserves_text(value: str) -> None:
    api = _api()
    parsed = api.parse_legacy_asset_source_row(_source(uploaded_at=value))
    assert parsed.values["uploaded_at"] == value
    assert f'"uploaded_at":"{value}"' in parsed.canonical_json


@pytest.mark.parametrize(
    "value",
    [
        "2099-01-02 00:00:00Z",
        "2099-01-02t00:00:00Z",
        "2099-01-02T00:00:00",
        "2099-01-02T00:00:00+01:00",
        "2099-01-02T00:00:00.1234567Z",
        "2099-02-30T00:00:00Z",
        "1969-12-31T23:59:59.999999Z",
    ],
)
def test_source_utc_rejects_noncanonical_invalid_or_out_of_range(value: str) -> None:
    api = _api()
    _invalid(
        lambda: api.parse_legacy_asset_source_row(_source(uploaded_at=value)),
        "source_asset_row_invalid",
    )


def test_source_context_derives_verified_unassigned_and_mismatch_authority() -> None:
    api = _api()
    source = api.parse_legacy_asset_source_row(_source())
    verified = api.validate_legacy_asset_source_context(
        source,
        task={"task_id": "task-legacy", "worker_id": "worker-legacy"},
        uploader={"agent_id": "worker-legacy"},
        worker={"agent_id": "worker-legacy"},
    )
    assert type(verified) is api.CanonicalLegacyAssetSourceContext
    assert not hasattr(verified, "__dict__")
    assert verified.source is source
    assert verified.assigned_worker_id == "worker-legacy"
    assert verified.attribution_status == "verified_worker"
    unassigned = api.validate_legacy_asset_source_context(
        source,
        task={"task_id": "task-legacy", "worker_id": None},
        uploader={"agent_id": "worker-legacy"},
        worker=None,
    )
    assert type(unassigned) is api.CanonicalLegacyAssetSourceContext
    assert not hasattr(unassigned, "__dict__")
    assert unassigned.assigned_worker_id is None
    assert unassigned.attribution_status == "worker_unassigned"
    mismatch = api.validate_legacy_asset_source_context(
        source,
        task={"task_id": "task-legacy", "worker_id": "other-worker"},
        uploader={"agent_id": "worker-legacy"},
        worker={"agent_id": "other-worker"},
    )
    assert type(mismatch) is api.CanonicalLegacyAssetSourceContext
    assert not hasattr(mismatch, "__dict__")
    assert mismatch.assigned_worker_id == "other-worker"
    assert mismatch.attribution_status == "uploader_mismatch"
    with pytest.raises(FrozenInstanceError):
        _set_attribute(verified, "assigned_worker_id", "changed")


@pytest.mark.parametrize(
    ("case", "task", "uploader", "worker"),
    [
        (
            "missing_task",
            None,
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "missing_uploader",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            None,
            {"agent_id": "worker-legacy"},
        ),
        (
            "foreign_task",
            {"task_id": "other-task", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "extra_task_field",
            {"task_id": "task-legacy", "worker_id": "worker-legacy", "extra": 1},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "worker_present_for_unassigned_task",
            {"task_id": "task-legacy", "worker_id": None},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "missing_required_worker",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            None,
        ),
        (
            "foreign_worker",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "other-worker"},
        ),
        (
            "matching_nontext_worker_ids",
            {"task_id": "task-legacy", "worker_id": 1},
            {"agent_id": "worker-legacy"},
            {"agent_id": 1},
        ),
        (
            "matching_empty_worker_ids",
            {"task_id": "task-legacy", "worker_id": ""},
            {"agent_id": "worker-legacy"},
            {"agent_id": ""},
        ),
        (
            "matching_non_utf8_worker_ids",
            {"task_id": "task-legacy", "worker_id": "bad\ud800worker"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "bad\ud800worker"},
        ),
        (
            "empty_uploader_id",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": ""},
            {"agent_id": "worker-legacy"},
        ),
        (
            "nontext_uploader_id",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": 1},
            {"agent_id": "worker-legacy"},
        ),
        (
            "non_utf8_uploader_id",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "bad\ud800uploader"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "foreign_uploader_id",
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "different-uploader"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "nontext_task_id",
            {"task_id": 1, "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "empty_task_id",
            {"task_id": "", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
        (
            "non_utf8_task_id",
            {"task_id": "bad\ud800task", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
        ),
    ],
)
def test_source_context_rejects_missing_foreign_malformed_and_contradictory_references(
    case: str,
    task: object,
    uploader: object,
    worker: object,
) -> None:
    api = _api()
    assert case
    source = api.parse_legacy_asset_source_row(_source())
    _invalid(
        lambda: api.validate_legacy_asset_source_context(
            source,
            task=task,
            uploader=uploader,
            worker=worker,
        ),
        "source_asset_reference_invalid",
    )


@pytest.mark.parametrize(
    ("task", "uploader", "worker"),
    [
        ([], {"agent_id": "worker-legacy"}, {"agent_id": "worker-legacy"}),
        ({"task_id": "task-legacy"}, {"agent_id": "worker-legacy"}, None),
        ({"task_id": "task-legacy", "worker_id": "worker-legacy"}, [], None),
        (
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy", "extra": 1},
            {"agent_id": "worker-legacy"},
        ),
        (
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            [],
        ),
        (
            {"task_id": "task-legacy", "worker_id": "worker-legacy"},
            {"agent_id": "worker-legacy"},
            {"agent_id": "worker-legacy", "extra": 1},
        ),
    ],
)
def test_source_context_requires_exact_named_reference_mappings(
    task: object,
    uploader: object,
    worker: object,
) -> None:
    api = _api()
    source = api.parse_legacy_asset_source_row(_source())
    _invalid(
        lambda: api.validate_legacy_asset_source_context(
            source,
            task=task,
            uploader=uploader,
            worker=worker,
        ),
        "source_asset_reference_invalid",
    )


@pytest.mark.parametrize(
    "source",
    [
        _source(),
        SimpleNamespace(
            values=MappingProxyType(_source()),
            canonical_json=_canonical(_source()),
            source_row_sha256=_sha(_canonical(_source())),
        ),
    ],
    ids=["raw_mapping", "attribute_lookalike"],
)
def test_source_context_rejects_unparsed_source_mapping_and_lookalike(source: object) -> None:
    api = _api()
    _invalid(
        lambda: api.validate_legacy_asset_source_context(
            source,
            task={"task_id": "task-legacy", "worker_id": "worker-legacy"},
            uploader={"agent_id": "worker-legacy"},
            worker={"agent_id": "worker-legacy"},
        ),
        "source_asset_reference_invalid",
    )


def test_derived_ids_use_exact_uuid5_names_and_lowercase_string_rendering() -> None:
    api = _api()
    assert (
        api.derive_legacy_asset_observation_id(
            migration_plan_id=_PLAN_ID,
            legacy_asset_id="asset-opaque-世界",
        )
        == _observation_id()
    )
    identity = api.derive_migrated_artifact_identity(
        migration_plan_id=_PLAN_ID,
        legacy_asset_id="asset-opaque-世界",
    )
    assert type(identity) is api.MigratedArtifactIdentity
    assert not hasattr(identity, "__dict__")
    expected_id, expected_key = _artifact_identity()
    assert identity.artifact_id == expected_id
    assert identity.object_key == expected_key
    assert str(UUID(identity.artifact_id)) == identity.artifact_id
    with pytest.raises(FrozenInstanceError):
        _set_attribute(identity, "artifact_id", str(uuid5(NAMESPACE_URL, "changed")))


@pytest.mark.parametrize(
    ("plan_id", "asset_id"),
    [
        (None, "asset-opaque-世界"),
        (1, "asset-opaque-世界"),
        ("", "asset-opaque-世界"),
        ("A" * 64, "asset-opaque-世界"),
        ("a" * 63, "asset-opaque-世界"),
        ("g" * 64, "asset-opaque-世界"),
        (_PLAN_ID, ""),
        (_PLAN_ID, 1),
        (_PLAN_ID, "bad\ud800asset"),
    ],
)
def test_derived_ids_reject_invalid_plan_and_asset_identity(
    plan_id: object, asset_id: object
) -> None:
    api = _api()
    _invalid(
        lambda: api.derive_legacy_asset_observation_id(
            migration_plan_id=plan_id,
            legacy_asset_id=asset_id,
        )
    )
    _invalid(
        lambda: api.derive_migrated_artifact_identity(
            migration_plan_id=plan_id,
            legacy_asset_id=asset_id,
        )
    )


@pytest.mark.parametrize(
    ("assigned_worker_id", "attribution_status"),
    [(None, "worker_unassigned"), ("other-worker", "uploader_mismatch")],
)
def test_observation_preserves_verified_bytes_independent_of_worker_eligibility(
    assigned_worker_id: str | None,
    attribution_status: str,
) -> None:
    api = _api()
    value = _observation(
        assigned_worker_id=assigned_worker_id,
        attribution_status=attribution_status,
    )
    expected_json = _canonical(value)
    parsed = api.parse_legacy_asset_observation(value)
    assert type(parsed) is api.CanonicalLegacyAssetObservation
    assert dict(parsed.values) == value
    assert parsed.observation_id == _observation_id()
    assert parsed.canonical_json == expected_json
    assert parsed.observation_sha256 == _sha(expected_json)


def test_observation_accepts_installed_bytes_with_a_matching_declared_hash() -> None:
    api = _api()
    source = _source(content_hash=_OBSERVED_SHA)
    value = _observation(
        source_row=source,
        source_row_sha256=_sha(_canonical(source)),
        declared_hash_status="matched",
    )
    expected_json = _canonical(value)
    parsed = api.parse_legacy_asset_observation(value)
    assert dict(parsed.values) == value
    assert parsed.observation_id == _observation_id()
    assert parsed.canonical_json == expected_json
    assert parsed.observation_sha256 == _sha(expected_json)


@pytest.mark.parametrize(
    ("byte_status", "declared_hash_status", "content_hash", "observed_size", "observed_hash"),
    [
        ("unsafe_path", "absent", None, None, None),
        ("unsafe_path", "unobserved", "b" * 64, None, None),
        ("shared_source_conflict", "absent", None, None, None),
        ("shared_source_conflict", "unobserved", "b" * 64, None, None),
        ("missing", "absent", None, None, None),
        ("missing", "unobserved", "b" * 64, None, None),
        ("symlink", "absent", None, None, None),
        ("symlink", "unobserved", "b" * 64, None, None),
        ("non_regular", "absent", None, None, None),
        ("non_regular", "unobserved", "b" * 64, None, None),
        ("size_mismatch", "absent", None, len(_BODY) + 1, _OBSERVED_SHA),
        ("size_mismatch", "matched", _OBSERVED_SHA, len(_BODY) + 1, _OBSERVED_SHA),
        ("size_mismatch", "mismatch", "b" * 64, len(_BODY) + 1, _OBSERVED_SHA),
        ("hash_mismatch", "mismatch", "b" * 64, len(_BODY), _OBSERVED_SHA),
    ],
)
def test_observation_accepts_every_unavailable_classification_shape(
    byte_status: str,
    declared_hash_status: str,
    content_hash: str | None,
    observed_size: int | None,
    observed_hash: str | None,
) -> None:
    api = _api()
    source = _source(content_hash=content_hash)
    value = _observation(
        source_row=source,
        source_row_sha256=_sha(_canonical(source)),
        byte_status=byte_status,
        declared_hash_status=declared_hash_status,
        copy_status="unavailable",
        observed_size_bytes=observed_size,
        observed_sha256=observed_hash,
        generated_artifact_id=None,
        generated_object_key=None,
    )
    expected_json = _canonical(value)
    parsed = api.parse_legacy_asset_observation(value)
    assert dict(parsed.values) == value
    assert parsed.observation_id == _observation_id()
    assert parsed.canonical_json == expected_json
    assert parsed.observation_sha256 == _sha(expected_json)


@pytest.mark.parametrize(
    ("byte_status", "declared_hash_status", "content_hash", "observed_size", "observed_hash"),
    [
        *[
            (status, "matched", _OBSERVED_SHA, len(_BODY), _OBSERVED_SHA)
            for status in (
                "unsafe_path",
                "shared_source_conflict",
                "missing",
                "symlink",
                "non_regular",
            )
        ],
        *[
            (status, "mismatch", "b" * 64, len(_BODY), _OBSERVED_SHA)
            for status in (
                "unsafe_path",
                "shared_source_conflict",
                "missing",
                "symlink",
                "non_regular",
            )
        ],
        ("size_mismatch", "unobserved", "b" * 64, len(_BODY) + 1, _OBSERVED_SHA),
        ("hash_mismatch", "absent", None, len(_BODY), _OBSERVED_SHA),
        ("hash_mismatch", "unobserved", "b" * 64, len(_BODY), _OBSERVED_SHA),
    ],
)
def test_observation_rejects_every_forbidden_byte_and_declared_hash_classification_pair(
    byte_status: str,
    declared_hash_status: str,
    content_hash: str | None,
    observed_size: int,
    observed_hash: str,
) -> None:
    api = _api()
    source = _source(content_hash=content_hash)
    value = _observation(
        source_row=source,
        source_row_sha256=_sha(_canonical(source)),
        byte_status=byte_status,
        declared_hash_status=declared_hash_status,
        copy_status="unavailable",
        observed_size_bytes=observed_size,
        observed_sha256=observed_hash,
        generated_artifact_id=None,
        generated_object_key=None,
    )
    _invalid(lambda: api.parse_legacy_asset_observation(value))


@pytest.mark.parametrize(
    ("content_hash", "declared_hash_status", "observed_size", "observed_hash"),
    [
        (None, "unobserved", None, None),
        ("b" * 64, "absent", None, None),
        (_OBSERVED_SHA, "mismatch", len(_BODY) + 1, _OBSERVED_SHA),
        ("b" * 64, "matched", len(_BODY) + 1, _OBSERVED_SHA),
    ],
    ids=[
        "null_source_hash_cannot_be_unobserved",
        "non_null_source_hash_cannot_be_absent",
        "equal_observed_hash_cannot_be_mismatch",
        "unequal_observed_hash_cannot_be_matched",
    ],
)
def test_observation_rejects_declared_hash_status_that_contradicts_actual_hash_facts(
    content_hash: str | None,
    declared_hash_status: str,
    observed_size: int | None,
    observed_hash: str | None,
) -> None:
    api = _api()
    source = _source(content_hash=content_hash)
    no_observed_pair = observed_size is None
    value = _observation(
        source_row=source,
        source_row_sha256=_sha(_canonical(source)),
        byte_status="missing" if no_observed_pair else "size_mismatch",
        declared_hash_status=declared_hash_status,
        copy_status="unavailable",
        observed_size_bytes=observed_size,
        observed_sha256=observed_hash,
        generated_artifact_id=None,
        generated_object_key=None,
    )
    _invalid(lambda: api.parse_legacy_asset_observation(value))


@pytest.mark.parametrize(
    ("defect", "replacement"),
    [
        ("format_version", 2),
        ("format_version", True),
        ("format_version", 1.0),
        ("format_version", "1"),
        ("migration_plan_id", None),
        ("migration_plan_id", 1),
        ("migration_plan_id", ""),
        ("migration_plan_id", "A" * 64),
        ("migration_plan_id", "a" * 63),
        ("legacy_asset_id", None),
        ("legacy_asset_id", 1),
        ("legacy_asset_id", ""),
        ("legacy_asset_id", "bad\ud800asset"),
        ("legacy_asset_id", "other-asset"),
        ("source_row_sha256", None),
        ("source_row_sha256", 1),
        ("source_row_sha256", "A" * 64),
        ("source_row_sha256", "b" * 63),
        ("source_row_sha256", "b" * 64),
        ("assigned_worker_id", 1),
        ("assigned_worker_id", ""),
        ("assigned_worker_id", "bad\ud800worker"),
        ("attribution_status", None),
        ("attribution_status", 1),
        ("attribution_status", ""),
        ("attribution_status", "unknown"),
        ("byte_status", None),
        ("byte_status", 1),
        ("byte_status", ""),
        ("byte_status", "unknown"),
        ("declared_hash_status", None),
        ("declared_hash_status", 1),
        ("declared_hash_status", ""),
        ("declared_hash_status", "unknown"),
        ("copy_status", None),
        ("copy_status", 1),
        ("copy_status", ""),
        ("copy_status", "unknown"),
        ("observed_at_us", None),
        ("observed_at_us", True),
        ("observed_at_us", 1.0),
        ("observed_at_us", "1"),
        ("observed_at_us", -1),
        ("observed_at_us", SQLITE_MAX_INTEGER + 1),
        ("observed_size_bytes", True),
        ("observed_size_bytes", 1.0),
        ("observed_size_bytes", "1"),
        ("observed_size_bytes", -1),
        ("observed_size_bytes", SQLITE_MAX_INTEGER + 1),
        ("observed_sha256", 1),
        ("observed_sha256", "A" * 64),
        ("observed_sha256", "a" * 63),
        ("generated_artifact_id", 1),
        ("generated_artifact_id", ""),
        ("generated_artifact_id", str(uuid5(NAMESPACE_URL, "wrong")).upper()),
        ("generated_artifact_id", "00000000-0000-4000-8000-000000000000"),
        ("generated_artifact_id", str(uuid5(NAMESPACE_URL, "wrong"))),
        ("generated_object_key", 1),
        ("generated_object_key", ""),
        ("generated_object_key", "live/ff/wrong.blob"),
    ],
)
def test_observation_rejects_invalid_projection_type_enum_hash_and_derived_identity(
    defect: str,
    replacement: object,
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_asset_observation(_observation(**{defect: replacement})))


@pytest.mark.parametrize("value", [0, SQLITE_MAX_INTEGER])
@pytest.mark.parametrize("field", ["observed_at_us", "observed_size_bytes"])
def test_observation_accepts_exact_nonnegative_sqlite_boundaries(field: str, value: int) -> None:
    api = _api()
    overrides: dict[str, object] = {field: value}
    if field == "observed_size_bytes" and value != len(_BODY):
        overrides.update(
            byte_status="size_mismatch",
            copy_status="unavailable",
            generated_artifact_id=None,
            generated_object_key=None,
        )
    parsed = api.parse_legacy_asset_observation(_observation(**overrides))
    assert parsed.values[field] == value


@pytest.mark.parametrize("defect", ["not_mapping", "missing", "extra"])
def test_observation_requires_an_exact_nested_source_mapping(defect: str) -> None:
    api = _api()
    source: object = _source()
    if defect == "not_mapping":
        source = []
    elif defect == "missing":
        del source["uploaded_at"]
    else:
        source["extra"] = "forbidden"
    _invalid(lambda: api.parse_legacy_asset_observation(_observation(source_row=source)))


@pytest.mark.parametrize("defect", ["not_mapping", "missing", "extra"])
def test_observation_requires_exact_fifteen_field_mapping(defect: str) -> None:
    api = _api()
    value: object = _observation()
    if defect == "not_mapping":
        value = []
    elif defect == "missing":
        del value["source_row_sha256"]
    else:
        value["extension"] = "forbidden"
    _invalid(lambda: api.parse_legacy_asset_observation(value))


@pytest.mark.parametrize(
    "overrides",
    [
        {"assigned_worker_id": None, "attribution_status": "verified_worker"},
        {"assigned_worker_id": "worker-legacy", "attribution_status": "worker_unassigned"},
        {"assigned_worker_id": "worker-legacy", "attribution_status": "uploader_mismatch"},
        {
            "byte_status": "verified",
            "copy_status": "unavailable",
            "generated_artifact_id": None,
            "generated_object_key": None,
        },
        {
            "byte_status": "missing",
            "declared_hash_status": "absent",
            "copy_status": "installed",
            "observed_size_bytes": None,
            "observed_sha256": None,
        },
        {
            "byte_status": "missing",
            "observed_size_bytes": 0,
            "observed_sha256": _OBSERVED_SHA,
            "copy_status": "unavailable",
            "generated_artifact_id": None,
            "generated_object_key": None,
        },
        {"byte_status": "verified", "declared_hash_status": "unobserved"},
        {"byte_status": "verified", "declared_hash_status": "mismatch"},
        {
            "byte_status": "hash_mismatch",
            "declared_hash_status": "matched",
            "copy_status": "unavailable",
            "generated_artifact_id": None,
            "generated_object_key": None,
        },
        {"generated_object_key": None},
        {"observed_sha256": None},
    ],
)
def test_observation_rejects_every_illegal_attribution_byte_hash_and_copy_combination(
    overrides: dict[str, object],
) -> None:
    api = _api()
    _invalid(lambda: api.parse_legacy_asset_observation(_observation(**overrides)))


def test_observation_returns_exact_recursive_immutable_canonical_copy() -> None:
    api = _api()
    value = _observation()
    expected_json = _canonical(value)
    parsed = api.parse_legacy_asset_observation(value)
    assert type(parsed) is api.CanonicalLegacyAssetObservation
    assert not hasattr(parsed, "__dict__")
    assert type(parsed.values) is MappingProxyType
    assert type(parsed.values["source_row"]) is MappingProxyType
    assert parsed.observation_id == _observation_id()
    assert parsed.canonical_json == expected_json
    assert parsed.observation_sha256 == _sha(expected_json)
    value["source_row"]["filename"] = "changed"
    assert parsed.values["source_row"]["filename"] == "Résumé 世界.txt"
    with pytest.raises(TypeError):
        operator.setitem(parsed.values["source_row"], "filename", "denied")
    with pytest.raises(FrozenInstanceError):
        _set_attribute(parsed, "observation_id", "changed")


def test_row_validator_accepts_exact_nineteen_column_projection_and_returns_observation() -> None:
    api = _api()
    context = _valid_context(api)
    row = _row()
    parsed = api.validate_legacy_asset_observation_row(context, row)
    assert type(parsed) is api.CanonicalLegacyAssetObservation
    assert not hasattr(parsed, "__dict__")
    assert parsed.observation_id == row["observation_id"]
    assert parsed.observation_sha256 == row["observation_sha256"]
    assert parsed.values["source_row"] == context.source.values


def test_row_validator_rejects_coherent_hash_over_noncanonical_json_bytes() -> None:
    api = _api()
    context = _valid_context(api)
    value = _observation()
    noncanonical = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    assert noncanonical != _canonical(value)
    row = _row(
        value,
        canonical_observation_json=noncanonical,
        observation_sha256=_sha(noncanonical),
    )
    _invalid(lambda: api.validate_legacy_asset_observation_row(context, row))


@pytest.mark.parametrize(
    ("task", "worker", "attribution"),
    [
        ({"task_id": "task-legacy", "worker_id": None}, None, "worker_unassigned"),
        (
            {"task_id": "task-legacy", "worker_id": "other-worker"},
            {"agent_id": "other-worker"},
            "uploader_mismatch",
        ),
    ],
)
def test_row_validator_accepts_authoritative_unassigned_and_mismatched_contexts(
    task: dict[str, object],
    worker: dict[str, object] | None,
    attribution: str,
) -> None:
    api = _api()
    source = api.parse_legacy_asset_source_row(_source())
    context = api.validate_legacy_asset_source_context(
        source,
        task=task,
        uploader={"agent_id": "worker-legacy"},
        worker=worker,
    )
    assigned = task["worker_id"]
    observation = _observation(
        assigned_worker_id=assigned,
        attribution_status=attribution,
    )
    parsed = api.validate_legacy_asset_observation_row(context, _row(observation))
    assert parsed.values["assigned_worker_id"] == assigned
    assert parsed.values["attribution_status"] == attribution


@pytest.mark.parametrize("defect", ["not_mapping", "missing", "extra"])
def test_row_validator_requires_exact_nineteen_column_named_mapping(defect: str) -> None:
    api = _api()
    context = _valid_context(api)
    row: object = _row()
    if defect == "not_mapping":
        row = []
    elif defect == "missing":
        del row["observation_sha256"]
    else:
        row["extra"] = "forbidden"
    _invalid(lambda: api.validate_legacy_asset_observation_row(context, row))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("observation_id", None),
        ("observation_id", 1),
        ("observation_id", ""),
        ("observation_id", _observation_id().upper()),
        ("observation_id", _observation_id().removeprefix("obs-")),
        ("observation_id", "obs-00000000-0000-4000-8000-000000000000"),
        ("observation_contract_version", True),
        ("observation_contract_version", 1.0),
        ("observation_contract_version", "1"),
        ("migration_plan_id", None),
        ("migration_plan_id", 1),
        ("migration_plan_id", "A" * 64),
        ("migration_plan_id", "a" * 63),
        ("legacy_asset_id", None),
        ("legacy_asset_id", 1),
        ("legacy_asset_id", ""),
        ("task_id", None),
        ("task_id", 1),
        ("task_id", ""),
        ("uploader_id", None),
        ("uploader_id", 1),
        ("uploader_id", ""),
        ("assigned_worker_id", 1),
        ("assigned_worker_id", ""),
        ("source_row_sha256", None),
        ("source_row_sha256", 1),
        ("source_row_sha256", "A" * 64),
        ("source_row_sha256", "a" * 63),
        ("attribution_status", None),
        ("attribution_status", 1),
        ("attribution_status", "unknown"),
        ("byte_status", None),
        ("byte_status", 1),
        ("byte_status", "unknown"),
        ("declared_hash_status", None),
        ("declared_hash_status", 1),
        ("declared_hash_status", "unknown"),
        ("copy_status", None),
        ("copy_status", 1),
        ("copy_status", "unknown"),
        ("canonical_observation_json", None),
        ("canonical_observation_json", 1),
        ("canonical_observation_json", "not-json"),
        ("canonical_observation_json", "[]"),
        ("observation_sha256", None),
        ("observation_sha256", 1),
        ("observation_sha256", "A" * 64),
        ("observation_sha256", "a" * 63),
        ("observed_at_us", None),
        ("observed_at_us", True),
        ("observed_at_us", 1.0),
        ("observed_at_us", "1"),
        ("observed_at_us", -1),
        ("observed_at_us", SQLITE_MAX_INTEGER + 1),
        ("observed_size_bytes", True),
        ("observed_size_bytes", 1.0),
        ("observed_size_bytes", "1"),
        ("observed_size_bytes", -1),
        ("observed_size_bytes", SQLITE_MAX_INTEGER + 1),
        ("observed_sha256", 1),
        ("observed_sha256", "A" * 64),
        ("observed_sha256", "a" * 63),
        ("generated_artifact_id", 1),
        ("generated_artifact_id", ""),
        ("generated_artifact_id", _SET_UUID_A.upper()),
        ("generated_artifact_id", "00000000-0000-4000-8000-000000000000"),
        ("generated_object_key", 1),
        ("generated_object_key", ""),
        ("generated_object_key", "live/ff/wrong.blob"),
    ],
)
def test_row_validator_rejects_invalid_stored_domain(field: str, replacement: object) -> None:
    api = _api()
    _invalid(
        lambda: api.validate_legacy_asset_observation_row(
            _valid_context(api),
            _row(**{field: replacement}),
        )
    )


@pytest.mark.parametrize("defect", sorted(_ROW_FIELDS))
def test_row_validator_rejects_every_changed_nineteen_column_projection(defect: str) -> None:
    api = _api()
    context = _valid_context(api)
    replacements: dict[str, object] = {
        "observation_id": "obs-00000000-0000-5000-8000-000000000000",
        "observation_contract_version": 2,
        "migration_plan_id": "b" * 64,
        "legacy_asset_id": "other-asset",
        "task_id": "other-task",
        "uploader_id": "other-uploader",
        "assigned_worker_id": "other-worker",
        "source_row_sha256": "b" * 64,
        "attribution_status": "uploader_mismatch",
        "byte_status": "missing",
        "declared_hash_status": "unobserved",
        "copy_status": "unavailable",
        "canonical_observation_json": "{}",
        "observation_sha256": "b" * 64,
        "observed_at_us": _OBSERVED_US + 1,
        "observed_size_bytes": len(_BODY) + 1,
        "observed_sha256": "b" * 64,
        "generated_artifact_id": str(uuid5(NAMESPACE_URL, "other")),
        "generated_object_key": "live/ff/00000000-0000-5000-8000-000000000000.blob",
    }
    _invalid(
        lambda: api.validate_legacy_asset_observation_row(
            context,
            _row(**{defect: replacements[defect]}),
        )
    )


def test_row_validator_rejects_coherent_forged_worker_authority() -> None:
    api = _api()
    context = _valid_context(api)
    forged = _observation(
        assigned_worker_id="other-worker",
        attribution_status="uploader_mismatch",
    )
    _invalid(lambda: api.validate_legacy_asset_observation_row(context, _row(forged)))


@pytest.mark.parametrize(
    ("kind", "entries", "expected"),
    [
        (
            "source_rows",
            [["z-asset", "b" * 64], ["a-asset", "a" * 64]],
            (("a-asset", "a" * 64), ("z-asset", "b" * 64)),
        ),
        (
            "asset_observations",
            [
                ["obs-" + str(uuid5(NAMESPACE_URL, "z")), "b" * 64],
                ["obs-" + str(uuid5(NAMESPACE_URL, "a")), "a" * 64],
            ],
            tuple(
                sorted(
                    (
                        ("obs-" + str(uuid5(NAMESPACE_URL, "z")), "b" * 64),
                        ("obs-" + str(uuid5(NAMESPACE_URL, "a")), "a" * 64),
                    ),
                    key=lambda item: item[0].encode("utf-8"),
                )
            ),
        ),
        (
            "observed_bytes",
            [["z-asset", 2, "b" * 64], ["a-asset", 1, "a" * 64]],
            (("a-asset", 1, "a" * 64), ("z-asset", 2, "b" * 64)),
        ),
        (
            "generated_copies",
            [
                [
                    _SET_UUID_Z,
                    _SET_KEY_Z,
                    2,
                    "b" * 64,
                ],
                [
                    _SET_UUID_A,
                    _SET_KEY_A,
                    1,
                    "a" * 64,
                ],
            ],
            tuple(
                sorted(
                    (
                        (
                            _SET_UUID_Z,
                            _SET_KEY_Z,
                            2,
                            "b" * 64,
                        ),
                        (
                            _SET_UUID_A,
                            _SET_KEY_A,
                            1,
                            "a" * 64,
                        ),
                    ),
                    key=lambda item: item[0].encode("utf-8"),
                )
            ),
        ),
    ],
)
def test_each_set_kind_sorts_without_mutating_input_and_hashes_exact_array(
    kind: str,
    entries: list[list[object]],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    api = _api()
    original = [list(entry) for entry in entries]
    result = api.canonical_asset_set(kind, entries)
    assert type(result) is api.CanonicalAssetSet
    assert not hasattr(result, "__dict__")
    expected_json = _canonical([list(entry) for entry in expected])
    assert result.entries == expected
    assert result.canonical_json == expected_json
    assert result.sha256 == _sha(expected_json)
    assert entries == original
    assert all(isinstance(entry, tuple) for entry in result.entries)
    with pytest.raises(FrozenInstanceError):
        _set_attribute(result, "sha256", "changed")


@pytest.mark.parametrize(
    "kind", ["source_rows", "asset_observations", "observed_bytes", "generated_copies"]
)
def test_each_set_kind_has_exact_empty_array_hash(kind: str) -> None:
    api = _api()
    result = api.canonical_asset_set(kind, [])
    assert result.entries == ()
    assert result.canonical_json == "[]"
    assert result.sha256 == "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"


@pytest.mark.parametrize("kind", ["observed_bytes", "generated_copies"])
def test_asset_sets_accept_exact_nonnegative_sqlite_integer_boundaries(kind: str) -> None:
    api = _api()
    if kind == "observed_bytes":
        entries = (("zero", 0, "a" * 64), ("max", SQLITE_MAX_INTEGER, "b" * 64))
    else:
        entries = (
            (_SET_UUID_A, _SET_KEY_A, 0, "a" * 64),
            (_SET_UUID_Z, _SET_KEY_Z, SQLITE_MAX_INTEGER, "b" * 64),
        )
    result = api.canonical_asset_set(kind, entries)
    expected = tuple(sorted(entries, key=lambda item: str(item[0]).encode("utf-8")))
    assert result.entries == expected


@pytest.mark.parametrize(
    "kind", ["source_rows", "asset_observations", "observed_bytes", "generated_copies"]
)
def test_every_set_kind_rejects_duplicate_primary_identity(kind: str) -> None:
    api = _api()
    entry = _valid_set_entry(kind)
    duplicate = list(entry)
    duplicate[-1] = "b" * 64
    _invalid(lambda: api.canonical_asset_set(kind, [entry, duplicate]))


@pytest.mark.parametrize(
    "kind", ["source_rows", "asset_observations", "observed_bytes", "generated_copies"]
)
@pytest.mark.parametrize("entry", ["not-an-entry", {"not": "an entry"}, None])
def test_every_set_kind_requires_list_or_tuple_entries(kind: str, entry: object) -> None:
    api = _api()
    _invalid(lambda: api.canonical_asset_set(kind, [entry]))


@pytest.mark.parametrize(
    "kind", ["source_rows", "asset_observations", "observed_bytes", "generated_copies"]
)
@pytest.mark.parametrize("replacement", [None, 1, "A" * 64, "a" * 63, "g" * 64])
def test_every_set_kind_rejects_invalid_hash_type_case_length_and_alphabet(
    kind: str,
    replacement: object,
) -> None:
    api = _api()
    entry = _valid_set_entry(kind)
    entry[-1] = replacement
    _invalid(lambda: api.canonical_asset_set(kind, [entry]))


@pytest.mark.parametrize("kind", ["observed_bytes", "generated_copies"])
@pytest.mark.parametrize("replacement", [True, 1.0, "1", -1, SQLITE_MAX_INTEGER + 1])
def test_every_set_integer_position_rejects_bool_float_string_negative_and_overflow(
    kind: str,
    replacement: object,
) -> None:
    api = _api()
    entry = _valid_set_entry(kind)
    integer_index = 1 if kind == "observed_bytes" else 2
    entry[integer_index] = replacement
    _invalid(lambda: api.canonical_asset_set(kind, [entry]))


@pytest.mark.parametrize(
    ("kind", "replacement"),
    [
        ("source_rows", None),
        ("source_rows", 1),
        ("source_rows", ""),
        ("source_rows", "bad\ud800asset"),
        ("observed_bytes", None),
        ("observed_bytes", 1),
        ("observed_bytes", ""),
        ("observed_bytes", "bad\ud800asset"),
        ("asset_observations", _SET_UUID_A),
        ("asset_observations", "obs-" + _SET_UUID_A.upper()),
        ("asset_observations", "obs-00000000-0000-4000-8000-000000000000"),
        ("asset_observations", "obs-{" + _SET_UUID_A + "}"),
        ("generated_copies", _SET_UUID_A.upper()),
        ("generated_copies", "00000000-0000-4000-8000-000000000000"),
        ("generated_copies", "{" + _SET_UUID_A + "}"),
    ],
)
def test_set_primary_identity_requires_exact_type_prefix_uuid_version_and_spelling(
    kind: str,
    replacement: object,
) -> None:
    api = _api()
    entry = _valid_set_entry(kind)
    entry[0] = replacement
    _invalid(lambda: api.canonical_asset_set(kind, [entry]))


@pytest.mark.parametrize("replacement", [None, 1, "", _SET_KEY_A.upper(), "live/ff/wrong.blob"])
def test_generated_copy_set_requires_exact_object_key_type_spelling_and_shard(
    replacement: object,
) -> None:
    api = _api()
    entry = _valid_set_entry("generated_copies")
    entry[1] = replacement
    _invalid(lambda: api.canonical_asset_set("generated_copies", [entry]))


@pytest.mark.parametrize(
    ("kind", "entries"),
    [
        ("unknown", []),
        ("source_rows", None),
        ("source_rows", {"asset": "a" * 64}),
        ("source_rows", [["asset"]]),
        ("source_rows", [["asset", "a" * 64, "extra"]]),
        ("source_rows", [["", "a" * 64]]),
        ("source_rows", [["asset", "A" * 64]]),
        ("source_rows", [["asset", "a" * 64], ["asset", "b" * 64]]),
        ("asset_observations", [[str(uuid5(NAMESPACE_URL, "a")), "a" * 64]]),
        ("observed_bytes", [["asset", True, "a" * 64]]),
        ("observed_bytes", [["asset", -1, "a" * 64]]),
        ("observed_bytes", [["asset", SQLITE_MAX_INTEGER + 1, "a" * 64]]),
        (
            "generated_copies",
            [[str(uuid5(NAMESPACE_URL, "a")), "live/ff/wrong.blob", 1, "a" * 64]],
        ),
        ("generated_copies", [[None, None, None, None]]),
    ],
)
def test_asset_sets_reject_invalid_kind_container_shape_type_duplicate_and_key(
    kind: object,
    entries: object,
) -> None:
    api = _api()
    _invalid(lambda: api.canonical_asset_set(kind, entries))
