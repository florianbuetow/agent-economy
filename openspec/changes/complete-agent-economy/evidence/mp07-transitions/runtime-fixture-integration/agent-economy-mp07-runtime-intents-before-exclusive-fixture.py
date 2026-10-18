"""T-181 durable caller-owned runtime intent contract."""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import json
import multiprocessing
import os
import stat
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

pytestmark = pytest.mark.unit

_MODULE_NAME = "base_agent.runtime_intents"
_REQUIRED_EXPORTS = {
    "FileTaskIntentStore",
    "FrozenTaskIntent",
    "NewTaskIntent",
    "RuntimeIntentError",
    "RuntimeOwner",
}
_module_spec = importlib.util.find_spec(_MODULE_NAME)
_runtime: Any | None = importlib.import_module(_MODULE_NAME) if _module_spec is not None else None
_missing_exports = (
    sorted(_REQUIRED_EXPORTS.difference(dir(_runtime))) if _runtime is not None else []
)

_TASK_ID = "t-00000000-0000-4000-8000-000000000181"
_OPERATION_ID = "op-00000000-0000-4000-8000-000000000181"
_BID_ID = f"bid-{_OPERATION_ID[3:]}"
_FINGERPRINT = "1" * 64
_URLS = {
    "identity_url": "https://identity.example",
    "bank_url": "https://bank.example",
    "task_board_url": "https://task-board.example",
    "reputation_url": "https://reputation.example",
    "court_url": "https://court.example",
}


def _owner(**overrides: object) -> Any:
    assert _runtime is not None
    values: dict[str, object] = {
        "schema_version": 1,
        "environment_id": "local-integration",
        "role": "math-worker",
        "profile": "mathbot_openai",
        "handle": "worker-alice",
        "registered_agent_id": "a-worker-alice",
        "key_fingerprint": _FINGERPRINT,
        "service_urls": dict(_URLS),
    }
    values.update(overrides)
    return _runtime.RuntimeOwner(**values)


def _owner_for_action(action: str) -> Any:
    if action in {"accept_bid", "approve_task", "dispute_task"}:
        return _owner(
            role="task-feeder",
            profile="task-feeder",
            handle="poster-alice",
            registered_agent_id="a-poster",
            key_fingerprint="2" * 64,
        )
    return _owner()


def _context(
    *,
    operation_id: str = _OPERATION_ID,
    task_id: str = _TASK_ID,
    command: str = "submit_bid",
    source_status: str = "open",
    source_version: int = 7,
    target_status: str = "open",
    target_version: int = 7,
    response_kind: str = "bid",
    result_facts: dict[str, object] | None = None,
    actor_id: str = "a-worker-alice",
) -> dict[str, object]:
    return {
        "response_kind": response_kind,
        "operation_id": operation_id,
        "task_id": task_id,
        "command": command,
        "source_status": source_status,
        "source_lifecycle_version": source_version,
        "target_status": target_status,
        "target_lifecycle_version": target_version,
        "result_facts": (
            {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": "a-worker-alice",
                "amount": 9,
            }
            if result_facts is None
            else result_facts
        ),
        "actor_id": actor_id,
        "frozen_task_facts": {"bidder_id": "a-worker-alice"},
    }


def _intent(**overrides: object) -> Any:
    assert _runtime is not None
    values: dict[str, object] = {
        "task_id": _TASK_ID,
        "slot": "bid",
        "action": "submit_bid",
        "source_status": "open",
        "target_status": "open",
        "operation_id": _OPERATION_ID,
        "expected_lifecycle_version": 7,
        "public_arguments": {
            "task_id": _TASK_ID,
            "amount": 9,
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 7,
        },
        "acknowledgment_context": _context(),
    }
    values.update(overrides)
    return _runtime.NewTaskIntent(**values)


def _store(root: Path, *, owner: Any | None = None, diagnostic_limit: int = 8) -> Any:
    assert _runtime is not None
    return _runtime.FileTaskIntentStore(
        root_directory=root,
        owner=_owner() if owner is None else owner,
        diagnostic_limit=diagnostic_limit,
        max_record_bytes=65_536,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _completed_ack() -> dict[str, object]:
    return {
        "bid_id": _BID_ID,
        "task_id": _TASK_ID,
        "bidder_id": "a-worker-alice",
        "amount": 9,
        "submitted_at": "2026-10-18T12:00:00Z",
    }


def _bid_intent_for(task_id: str, operation_id: str) -> Any:
    return _intent(
        task_id=task_id,
        operation_id=operation_id,
        public_arguments={
            "task_id": task_id,
            "amount": 9,
            "operation_id": operation_id,
            "expected_lifecycle_version": 7,
        },
        acknowledgment_context=_context(operation_id=operation_id, task_id=task_id),
    )


def _bid_ack_for(task_id: str, operation_id: str) -> dict[str, object]:
    return {
        "bid_id": f"bid-{operation_id[3:]}",
        "task_id": task_id,
        "bidder_id": "a-worker-alice",
        "amount": 9,
        "submitted_at": "2026-10-18T12:00:00Z",
    }


def _expected_request_hash(action: str) -> str:
    source = (
        "open"
        if action == "accept_bid"
        else "accepted"
        if action == "submit_deliverable"
        else "submitted"
    )
    if action == "accept_bid":
        actor_id = "a-poster"
        arguments: dict[str, object] = {
            "bid_id": "bid-selected",
            "worker_id": "a-worker-alice",
            "escrow_id": "esc-runtime-181",
            "reward": 100,
            "bid_amount": 90,
        }
    elif action == "submit_deliverable":
        actor_id = "a-worker-alice"
        arguments = {"worker_id": "a-worker-alice"}
    else:
        actor_id = "a-poster"
        arguments = {
            "poster_id": "a-poster",
            "worker_id": "a-worker-alice",
            "escrow_id": "esc-runtime-181",
            "reward": 100,
        }
        if action == "approve_task":
            arguments["source_reference"] = f"task:{_TASK_ID}:approved"
        else:
            arguments["reason"] = "Résultat incomplet ∑"
    return _canonical_hash(
        {
            "protocol_version": 1,
            "task_id": _TASK_ID,
            "command": action,
            "expected_status": source,
            "expected_lifecycle_version": 7,
            "actor_kind": "agent",
            "actor_id": actor_id,
            "arguments": arguments,
        }
    )


def _action_case(action: str) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    operation_id = f"op-00000000-0000-4000-8000-{len(action):012d}"
    common_args: dict[str, object] = {
        "task_id": _TASK_ID,
        "operation_id": operation_id,
        "expected_lifecycle_version": 7,
    }
    cases: dict[str, tuple[str, str, str, dict[str, object], dict[str, object]]] = {
        "submit_bid": (
            "bid",
            "open",
            "open",
            {"amount": 9},
            {
                "bid_id": f"bid-{operation_id[3:]}",
                "bidder_id": "a-worker-alice",
                "amount": 9,
            },
        ),
        "accept_bid": (
            "acceptance",
            "open",
            "accepted",
            {"bid_id": "bid-selected"},
            {
                "bid_id": "bid-selected",
                "worker_id": "a-worker-alice",
                "reward": 100,
                "bid_amount": 90,
            },
        ),
        "submit_deliverable": (
            "submission",
            "accepted",
            "submitted",
            {},
            {"worker_id": "a-worker-alice"},
        ),
        "approve_task": (
            "review-decision",
            "submitted",
            "approved",
            {},
            {
                "worker_id": "a-worker-alice",
                "reward": 100,
                "effect_result_reference": operation_id,
            },
        ),
        "dispute_task": (
            "review-decision",
            "submitted",
            "disputed",
            {"reason": "Résultat incomplet ∑"},
            {
                "worker_id": "a-worker-alice",
                "reason": "Résultat incomplet ∑",
            },
        ),
    }
    slot, source, target, extra_args, result_facts = cases[action]
    public_arguments = {**common_args, **extra_args}
    context = _context(
        operation_id=operation_id,
        command=action,
        source_status=source,
        target_status=target,
        target_version=7 if action == "submit_bid" else 8,
        response_kind="bid" if action == "submit_bid" else "task_mutation",
        result_facts=result_facts,
        actor_id=(
            "a-poster"
            if action in {"accept_bid", "approve_task", "dispute_task"}
            else "a-worker-alice"
        ),
    )
    context["frozen_task_facts"] = (
        {"bidder_id": "a-worker-alice"}
        if action == "submit_bid"
        else {
            "poster_id": "a-poster",
            "worker_id": "a-worker-alice",
            "escrow_id": "esc-runtime-181",
            "accepted_reward": 100,
            "selected_bid_amount": 90,
        }
    )
    descriptor = {
        "task_id": _TASK_ID,
        "slot": slot,
        "action": action,
        "source_status": source,
        "target_status": target,
        "operation_id": operation_id,
        "expected_lifecycle_version": 7,
        "public_arguments": public_arguments,
        "acknowledgment_context": context,
    }
    if action != "submit_bid":
        context["request_sha256"] = _expected_request_hash(action)
    acknowledgment = _ack_for_case(action, descriptor, result_facts)
    return descriptor, public_arguments, acknowledgment


def _ack_for_case(
    action: str,
    descriptor: dict[str, object],
    result_facts: dict[str, object],
) -> dict[str, object]:
    operation_id = str(descriptor["operation_id"])
    if action == "submit_bid":
        return {
            "bid_id": f"bid-{operation_id[3:]}",
            "task_id": _TASK_ID,
            "bidder_id": "a-worker-alice",
            "amount": 9,
            "submitted_at": "2026-10-18T12:00:00Z",
        }
    source = str(descriptor["source_status"])
    target = str(descriptor["target_status"])
    result = {
        "task_id": _TASK_ID,
        "command": action,
        "status": target,
        "lifecycle_version": 8,
        "event_id": 181,
        **result_facts,
    }
    if action == "dispute_task":
        result.update(
            dispute_id="clm-runtime-181",
            effect_result_reference="clm-runtime-181",
        )
    effect_kind = "none"
    effect_state = "none"
    effect_operation_id: str | None = None
    effect_result_reference: str | None = None
    effect_intent: dict[str, object] | None = None
    frozen = {
        "poster_id": "a-poster",
        "worker_id": "a-worker-alice",
        "escrow_id": "esc-runtime-181",
        "reward": 100,
    }
    if action == "accept_bid":
        native_arguments = {
            "bid_id": "bid-selected",
            "worker_id": "a-worker-alice",
            "escrow_id": "esc-runtime-181",
            "reward": 100,
            "bid_amount": 90,
        }
        actor_id = "a-poster"
    elif action == "submit_deliverable":
        native_arguments = {"worker_id": "a-worker-alice"}
        actor_id = "a-worker-alice"
    elif action == "approve_task":
        native_arguments = {**frozen, "source_reference": f"task:{_TASK_ID}:approved"}
        actor_id = "a-poster"
    else:
        native_arguments = {
            **frozen,
            "reason": "Résultat incomplet ∑",
        }
        actor_id = "a-poster"
    native_command = {
        "protocol_version": 1,
        "task_id": _TASK_ID,
        "command": action,
        "expected_status": source,
        "expected_lifecycle_version": 7,
        "actor_kind": "agent",
        "actor_id": actor_id,
        "arguments": native_arguments,
    }
    if action == "approve_task":
        financial = {
            "request_version": 1,
            "operation_kind": "escrow_release",
            "escrow_id": "esc-runtime-181",
            "source_reference": f"task:{_TASK_ID}:approved",
            "recipient_account_id": "a-worker-alice",
        }
        effect_kind, effect_state = "bank_release", "succeeded"
        effect_operation_id = effect_result_reference = operation_id
        effect_intent = {
            "financial_request": financial,
            "financial_request_sha256": _canonical_hash(financial),
            "expected_escrow": {
                "escrow_id": "esc-runtime-181",
                "task_id": _TASK_ID,
                "payer_account_id": "a-poster",
                "amount": 100,
            },
        }
    elif action == "dispute_task":
        effect_kind, effect_state = "court_claim", "succeeded"
        effect_result_reference = "clm-runtime-181"
        effect_intent = {
            "court_claim": {
                "task_id": _TASK_ID,
                "claimant_id": "a-poster",
                "respondent_id": "a-worker-alice",
                "reason": "Résultat incomplet ∑",
            },
            "expected_escrow": {
                "escrow_id": "esc-runtime-181",
                "task_id": _TASK_ID,
                "payer_account_id": "a-poster",
                "amount": 100,
            },
        }
    return {
        "task_id": _TASK_ID,
        "status": target,
        "lifecycle_version": 8,
        "worker_id": "a-worker-alice",
        "operation": {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": _TASK_ID,
            "command": action,
            "request_sha256": _canonical_hash(native_command),
            "operation_state": "completed",
            "source_status": source,
            "source_lifecycle_version": 7,
            "target_status": target,
            "target_lifecycle_version": 8,
            "effect_kind": effect_kind,
            "effect_state": effect_state,
            "effect_operation_id": effect_operation_id,
            "effect_result_reference": effect_result_reference,
            "effect_intent": effect_intent,
            "event_id": 181,
            "result": result,
        },
    }


def _claimed_approval_ack() -> dict[str, object]:
    _descriptor, _arguments, completed = _action_case("approve_task")
    claimed = copy.deepcopy(completed)
    operation = claimed["operation"]
    assert isinstance(operation, dict)
    operation.update(
        operation_state="claimed",
        effect_state="pending",
        effect_result_reference=None,
        event_id=None,
        result=None,
    )
    return claimed


def _lease_attempt(root: str, owner_values: dict[str, object], queue: Any) -> None:
    runtime = importlib.import_module(_MODULE_NAME)
    owner = runtime.RuntimeOwner(**owner_values)
    try:
        with runtime.FileTaskIntentStore(
            root_directory=Path(root),
            owner=owner,
            diagnostic_limit=8,
            max_record_bytes=65_536,
        ):
            queue.put(("acquired", None))
    except runtime.RuntimeIntentError as exc:
        queue.put(("error", exc.reason))


@pytest.fixture(autouse=True)
def _required_runtime_interface(request: pytest.FixtureRequest) -> None:
    """Keep downstream cases required while one capability assertion owns the RED."""
    if request.node.name == "test_runtime_intent_interface_is_available":
        return
    assert _runtime is not None, (
        "Blocked by absent T-181 interface: base_agent.runtime_intents is required"
    )
    assert _missing_exports == [], f"Blocked by incomplete T-181 interface: {_missing_exports}"


def test_runtime_intent_interface_is_available() -> None:
    assert _runtime is not None, (
        "T-181 capability missing: base_agent.runtime_intents has not been implemented"
    )
    assert _missing_exports == [], f"T-181 interface is incomplete: {_missing_exports}"


def test_exact_intent_reopens_with_same_identity_version_terms_and_bytes(tmp_path: Path) -> None:
    intent = _intent()
    with _store(tmp_path) as store:
        frozen = store.create(intent)
        intent_path = store.intent_path(_TASK_ID, "bid")
        original_bytes = intent_path.read_bytes()
        assert frozen.operation_id == _OPERATION_ID
        assert frozen.expected_lifecycle_version == 7
        assert frozen.public_arguments == intent.public_arguments
        assert frozen.acknowledgment_context == intent.acknowledgment_context
        assert frozen.revision == 1
        assert frozen.state == "pending"

    with _store(tmp_path) as reopened:
        loaded = reopened.load(_TASK_ID, "bid")
        repeated = reopened.create(_intent())
        assert loaded == frozen == repeated
        assert reopened.intent_path(_TASK_ID, "bid").read_bytes() == original_bytes


def test_stored_slot_lookup_precedes_fresh_decision_factory(tmp_path: Path) -> None:
    factory = Mock(return_value=_intent())
    with _store(tmp_path) as store:
        first = store.load_or_create(_TASK_ID, "bid", factory)
        assert factory.call_count == 1

    factory.reset_mock()
    with _store(tmp_path) as reopened:
        replay = reopened.load_or_create(_TASK_ID, "bid", factory)
        assert replay == first
        factory.assert_not_called()


def test_changed_terms_in_existing_slot_are_a_local_conflict(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        changed = _intent(
            public_arguments={
                "task_id": _TASK_ID,
                "amount": 8,
                "operation_id": _OPERATION_ID,
                "expected_lifecycle_version": 7,
            }
        )
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.create(changed)
        assert raised.value.reason == "slot_conflict"


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("environment_id", "production"),
        ("role", "task-feeder"),
        ("profile", "mathbot"),
        ("handle", "worker-bob"),
        ("registered_agent_id", "a-worker-bob"),
        ("service_urls", {**_URLS, "court_url": "https://other-court.example"}),
    ],
)
def test_owner_binding_rejects_environment_role_profile_handle_agent_or_url_change(
    tmp_path: Path,
    field: str,
    changed: object,
) -> None:
    with _store(tmp_path):
        pass
    with (
        pytest.raises(_runtime.RuntimeIntentError) as raised,
        _store(tmp_path, owner=_owner(**{field: changed})),
    ):
        pass
    assert raised.value.reason == "owner_conflict"


def test_owner_and_intent_records_are_private_regular_files(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        intent_path = store.intent_path(_TASK_ID, "bid")
        assert _mode(store.identity_directory) == 0o700
        assert _mode(intent_path.parent) == 0o700
        assert _mode(store.owner_path) == 0o600
        assert _mode(store.lock_path) == 0o600
        assert _mode(intent_path) == 0o600
        assert store.owner_path.is_file() and not store.owner_path.is_symlink()
        assert intent_path.is_file() and not intent_path.is_symlink()


def test_owner_record_cannot_rebind_key_fingerprint(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        owner_path = store.owner_path
    payload = json.loads(owner_path.read_text())
    payload["key_fingerprint"] = "2" * 64
    owner_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    owner_path.chmod(0o600)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "owner_conflict"


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": True},
        {"service_urls": {key: value for key, value in _URLS.items() if key != "court_url"}},
        {"service_urls": {**_URLS, "extra_url": "https://extra.example"}},
        {"service_urls": {**_URLS, "court_url": 7}},
    ],
)
def test_runtime_owner_is_strict_and_requires_exact_five_service_urls(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(_runtime.RuntimeIntentError) as raised:
        _owner(**overrides)
    assert raised.value.reason == "invalid_input"


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_lifecycle_version": True},
        {"operation_id": "not-an-operation-id"},
        {"slot": "bid", "action": "approve_task"},
        {"source_status": "submitted"},
        {"target_status": "approved"},
        {"public_arguments": {"task_id": "t-foreign", "amount": 9}},
        {
            "public_arguments": {
                "task_id": _TASK_ID,
                "amount": 9,
                "operation_id": "op-00000000-0000-4000-8000-000000000999",
                "expected_lifecycle_version": 7,
            }
        },
        {
            "public_arguments": {
                "task_id": _TASK_ID,
                "amount": 9,
                "operation_id": _OPERATION_ID,
                "expected_lifecycle_version": 8,
            }
        },
        {"public_arguments": {"payload": {1, 2}}},
        {"public_arguments": {"amount": float("nan")}},
        {"acknowledgment_context": {"value": float("inf")}},
        {"acknowledgment_context": {"operation_id": "foreign"}},
        {"acknowledgment_context": {**_context(), "actor_id": "a-poster"}},
    ],
)
def test_new_intent_rejects_invalid_shape_identity_or_noncanonical_json(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(_runtime.RuntimeIntentError) as raised:
        _intent(**overrides)
    assert raised.value.reason == "invalid_input"


def test_intent_actor_must_match_bound_owner_without_writing_slot(tmp_path: Path) -> None:
    assert _runtime is not None
    descriptor, _arguments, _ack = _action_case("accept_bid")
    intent = _runtime.NewTaskIntent(**descriptor)
    with _store(tmp_path, owner=_owner()) as store:
        path = store.intent_path(_TASK_ID, "acceptance")
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.create(intent)
        assert raised.value.reason == "owner_intent_mismatch"
        assert not path.exists()


def test_operation_identity_cannot_be_reused_across_task_or_slot(tmp_path: Path) -> None:
    assert _runtime is not None
    other_task = "t-00000000-0000-4000-8000-000000000182"
    other_context = _context(task_id=other_task)
    across_task = _intent(
        task_id=other_task,
        public_arguments={
            "task_id": other_task,
            "amount": 9,
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 7,
        },
        acknowledgment_context=other_context,
    )
    submission_descriptor, _arguments, _ack = _action_case("submit_deliverable")
    submission_descriptor["operation_id"] = _OPERATION_ID
    submission_public = submission_descriptor["public_arguments"]
    submission_context = submission_descriptor["acknowledgment_context"]
    assert isinstance(submission_public, dict) and isinstance(submission_context, dict)
    submission_public["operation_id"] = _OPERATION_ID
    submission_context["operation_id"] = _OPERATION_ID
    across_slot = _runtime.NewTaskIntent(**submission_descriptor)
    with _store(tmp_path) as store:
        store.create(_intent())
        for conflicting in (across_task, across_slot):
            with pytest.raises(_runtime.RuntimeIntentError) as raised:
                store.create(conflicting)
            assert raised.value.reason == "operation_conflict"


def test_owner_intent_result_and_diagnostics_are_deeply_immutable(tmp_path: Path) -> None:
    owner = _owner()
    intent = _intent()
    with pytest.raises(TypeError):
        owner.service_urls["court_url"] = "https://mutated.example"
    with pytest.raises(TypeError):
        intent.public_arguments["amount"] = 8
    with pytest.raises(TypeError):
        intent.acknowledgment_context["result_facts"]["amount"] = 8
    with _store(tmp_path, owner=owner) as store:
        pending = store.create(intent)
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body=_completed_ack(),
        )
    with pytest.raises(TypeError):
        completed.result["body"]["amount"] = 8
    with pytest.raises((AttributeError, TypeError)):
        completed.diagnostics.append({"kind": "mutated"})


def test_relative_or_symlinked_runtime_root_is_rejected(tmp_path: Path) -> None:
    relative = Path("relative-runtime-state")
    with pytest.raises(_runtime.RuntimeIntentError) as relative_error:
        _runtime.FileTaskIntentStore(
            root_directory=relative,
            owner=_owner(),
            diagnostic_limit=8,
            max_record_bytes=65_536,
        )
    assert relative_error.value.reason == "unsafe_path"

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(_runtime.RuntimeIntentError) as symlink_error, _store(link):
        pass
    assert symlink_error.value.reason == "unsafe_path"


def test_symlink_or_nonregular_owner_record_is_rejected(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        owner_path = store.owner_path
    owner_path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    owner_path.symlink_to(outside)
    with pytest.raises(_runtime.RuntimeIntentError) as symlink_error, _store(tmp_path):
        pass
    assert symlink_error.value.reason == "unsafe_path"

    owner_path.unlink()
    owner_path.mkdir()
    with pytest.raises(_runtime.RuntimeIntentError) as nonregular_error, _store(tmp_path):
        pass
    assert nonregular_error.value.reason == "unsafe_path"


@pytest.mark.parametrize("record", ["owner", "intent"])
def test_unsafe_record_permissions_are_rejected(tmp_path: Path, record: str) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        path = store.owner_path if record == "owner" else store.intent_path(_TASK_ID, "bid")
    path.chmod(0o644)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path) as reopened:
        reopened.load(_TASK_ID, "bid")
    assert raised.value.reason == "unsafe_mode"


@pytest.mark.parametrize("mutation", ["malformed-json", "semantic-hash"])
def test_corrupt_intent_record_is_rejected(tmp_path: Path, mutation: str) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
    if mutation == "malformed-json":
        path.write_text("{")
    else:
        payload = json.loads(path.read_text())
        payload["semantic_sha256"] = "0" * 64
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    path.chmod(0o600)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path) as reopened:
        reopened.load(_TASK_ID, "bid")
    assert raised.value.reason == "corrupt_record"


@pytest.mark.parametrize("record", ["intent", "lease"])
def test_symlink_or_nonregular_intent_and_lease_records_are_rejected(
    tmp_path: Path,
    record: str,
) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid") if record == "intent" else store.lock_path
    path.unlink()
    outside = tmp_path / f"outside-{record}"
    outside.write_text("{}")
    path.symlink_to(outside)
    with pytest.raises(_runtime.RuntimeIntentError) as symlink_error, _store(tmp_path):
        pass
    assert symlink_error.value.reason == "unsafe_path"
    path.unlink()
    path.mkdir()
    with pytest.raises(_runtime.RuntimeIntentError) as nonregular_error, _store(tmp_path):
        pass
    assert nonregular_error.value.reason == "unsafe_path"


@pytest.mark.parametrize("directory", ["identity", "intents"])
def test_symlinked_identity_or_intent_directory_is_rejected(
    tmp_path: Path,
    directory: str,
) -> None:
    if directory == "identity":
        identities = tmp_path / "identities"
        identities.mkdir()
        path = identities / _FINGERPRINT
    else:
        with _store(tmp_path) as store:
            path = store.intent_path(_TASK_ID, "bid").parent
        path.rmdir()
    outside = tmp_path / f"outside-{directory}"
    outside.mkdir()
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "unsafe_path"


def test_symlinked_root_ancestor_is_rejected_without_outside_writes(tmp_path: Path) -> None:
    outside = tmp_path / "outside-root"
    outside.mkdir()
    linked = tmp_path / "linked-ancestor"
    linked.symlink_to(outside, target_is_directory=True)
    configured = linked / "runtime-state"
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(configured):
        pass
    assert raised.value.reason == "unsafe_path"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("directory", ["identity", "intents"])
def test_preexisting_identity_or_intent_directory_bad_mode_is_rejected(
    tmp_path: Path,
    directory: str,
) -> None:
    if directory == "identity":
        path = tmp_path / "identities" / _FINGERPRINT
        path.mkdir(parents=True, mode=0o755)
        path.chmod(0o755)
    else:
        with _store(tmp_path) as store:
            path = store.intent_path(_TASK_ID, "bid").parent
        path.chmod(0o755)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "unsafe_mode"


@pytest.mark.parametrize("mutation", ["malformed", "extra-field"])
def test_malformed_or_extra_owner_json_is_rejected(tmp_path: Path, mutation: str) -> None:
    with _store(tmp_path) as store:
        owner_path = store.owner_path
    if mutation == "malformed":
        owner_path.write_text("{")
    else:
        payload = json.loads(owner_path.read_text())
        payload["unexpected"] = True
        owner_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    owner_path.chmod(0o600)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "corrupt_record"


def test_intent_directory_swap_during_publication_never_writes_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside-swap"
    outside.mkdir()
    real_replace = os.replace
    with _store(tmp_path) as store:
        intent_dir = store.intent_path(_TASK_ID, "bid").parent
        parked = intent_dir.with_name("intents-parked")

        def swap_then_replace(
            source: object,
            target: object,
            *args: Any,
            **kwargs: Any,
        ) -> None:
            intent_dir.rename(parked)
            intent_dir.symlink_to(outside, target_is_directory=True)
            real_replace(source, target, *args, **kwargs)

        monkeypatch.setattr(os, "replace", swap_then_replace)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.create(_intent())
        assert raised.value.reason in {"unsafe_path", "storage_failure"}
        intent_dir.unlink()
        parked.rename(intent_dir)
    assert list(outside.iterdir()) == []


def test_intent_paths_are_hashed_and_cannot_escape_identity_directory(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        path = store.intent_path(_TASK_ID, "review-decision")
        assert path.parent.parent == store.identity_directory
        assert _TASK_ID not in path.name
        assert path.suffix == ".json"
        assert len(path.stem) == 64
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.intent_path("../../escape", "bid")
        assert raised.value.reason == "invalid_input"


def test_max_record_bytes_rejection_prevents_transport(tmp_path: Path) -> None:
    transport = Mock()
    assert _runtime is not None
    reason = "Résultat incomplet ∑" + "x" * 10_000
    descriptor, _arguments, _acknowledgment = _action_case("dispute_task")
    public_arguments = descriptor["public_arguments"]
    context = descriptor["acknowledgment_context"]
    assert isinstance(public_arguments, dict) and isinstance(context, dict)
    result_facts = context["result_facts"]
    assert isinstance(result_facts, dict)
    public_arguments["reason"] = reason
    result_facts["reason"] = reason
    context["request_sha256"] = _canonical_hash(
        {
            "protocol_version": 1,
            "task_id": _TASK_ID,
            "command": "dispute_task",
            "expected_status": "submitted",
            "expected_lifecycle_version": 7,
            "actor_kind": "agent",
            "actor_id": "a-poster",
            "arguments": {
                "poster_id": "a-poster",
                "worker_id": "a-worker-alice",
                "escrow_id": "esc-runtime-181",
                "reward": 100,
                "reason": reason,
            },
        }
    )
    store = _runtime.FileTaskIntentStore(
        root_directory=tmp_path,
        owner=_owner_for_action("dispute_task"),
        diagnostic_limit=8,
        max_record_bytes=4096,
    )
    with store:
        oversized = _runtime.NewTaskIntent(**descriptor)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            frozen = store.create(oversized)
            transport(frozen.public_arguments)
        assert raised.value.reason == "record_too_large"
        transport.assert_not_called()
        assert not store.intent_path(_TASK_ID, "review-decision").exists()


def test_file_fsync_failure_prevents_transport_and_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Mock()
    with _store(tmp_path) as store:
        path = store.intent_path(_TASK_ID, "bid")

        def fail_fsync(_file_descriptor: int) -> None:
            raise OSError("injected file fsync failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            frozen = store.create(_intent())
            transport(frozen.public_arguments)
        assert raised.value.reason == "storage_failure"
        transport.assert_not_called()
        assert not path.exists()


@pytest.mark.parametrize("fault", ["exclusive-link", "file-fsync"])
def test_initial_owner_publication_fault_leaves_no_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    owner_path = tmp_path / "identities" / _FINGERPRINT / "owner.json"
    if fault == "exclusive-link":

        def fail_link(*_args: object, **_kwargs: object) -> None:
            raise OSError("owner link failure")

        monkeypatch.setattr(os, "link", fail_link)
    else:
        monkeypatch.setattr(
            os,
            "fsync",
            lambda _descriptor: (_ for _ in ()).throw(OSError("owner fsync failure")),
        )
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "storage_failure"
    assert not owner_path.exists()


def test_owner_directory_fsync_failure_reopens_surviving_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    owner_path = tmp_path / "identities" / _FINGERPRINT / "owner.json"
    injected = False

    def fail_published_owner_directory_fsync(file_descriptor: int) -> None:
        nonlocal injected
        opened = os.fstat(file_descriptor)
        if owner_path.is_file() and stat.S_ISDIR(opened.st_mode):
            parent = owner_path.parent.stat()
            if (opened.st_dev, opened.st_ino) == (parent.st_dev, parent.st_ino):
                injected = True
        if injected:
            raise OSError("owner directory fsync failure")
        real_fsync(file_descriptor)

    monkeypatch.setattr(os, "fsync", fail_published_owner_directory_fsync)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(tmp_path):
        pass
    assert raised.value.reason == "storage_failure"
    assert injected
    assert owner_path.is_file()
    monkeypatch.setattr(os, "fsync", real_fsync)
    with _store(tmp_path):
        pass


def test_foreign_owner_winning_initial_exclusive_link_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    with _store(foreign_root, owner=_owner(profile="foreign-profile")) as foreign_store:
        foreign_bytes = foreign_store.owner_path.read_bytes()
    target_root = tmp_path / "target"
    target_root.mkdir()

    owner_path = target_root / "identities" / _FINGERPRINT / "owner.json"

    def foreign_owner_wins(*_args: object, **_kwargs: object) -> None:
        owner_path.write_bytes(foreign_bytes)
        owner_path.chmod(0o600)
        raise FileExistsError("foreign owner won publication race")

    monkeypatch.setattr(os, "link", foreign_owner_wins)
    with pytest.raises(_runtime.RuntimeIntentError) as raised, _store(target_root):
        pass
    assert raised.value.reason == "owner_conflict"
    assert owner_path.read_bytes() == foreign_bytes


def test_stale_revision_observed_by_open_store_is_rejected(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        first = store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
        old_bytes = path.read_bytes()
        second = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=first.revision,
            status_code=201,
            body={"malformed": True},
        )
        assert second.revision == 2
        path.write_bytes(old_bytes)
        path.chmod(0o600)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.load(_TASK_ID, "bid")
        assert raised.value.reason == "stale_revision"


def test_replace_failure_prevents_transport_and_leaves_no_final_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Mock()
    with _store(tmp_path) as store:
        intent_path = store.intent_path(_TASK_ID, "bid")

        def fail_replace(_source: object, _target: object) -> None:
            raise OSError("injected replace failure")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            frozen = store.create(_intent())
            transport(frozen.public_arguments)
        assert raised.value.reason == "storage_failure"
        transport.assert_not_called()
        assert not intent_path.exists()


def test_directory_fsync_failure_prevents_transport_but_reopen_reuses_visible_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = Mock()
    real_fsync = os.fsync
    calls = 0
    with _store(tmp_path) as store:
        intent_path = store.intent_path(_TASK_ID, "bid")

        def fail_second_fsync(file_descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected directory fsync failure")
            real_fsync(file_descriptor)

        monkeypatch.setattr(os, "fsync", fail_second_fsync)
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            frozen = store.create(_intent())
            transport(frozen.public_arguments)
        assert raised.value.reason == "storage_failure"
        transport.assert_not_called()
        assert intent_path.is_file()

    monkeypatch.setattr(os, "fsync", real_fsync)
    with _store(tmp_path) as reopened:
        surviving = reopened.load(_TASK_ID, "bid")
        assert surviving is not None
        assert surviving.operation_id == _OPERATION_ID


def test_second_process_cannot_acquire_same_identity_lease(tmp_path: Path) -> None:
    owner_values = {
        "schema_version": 1,
        "environment_id": "local-integration",
        "role": "math-worker",
        "profile": "mathbot_openai",
        "handle": "worker-alice",
        "registered_agent_id": "a-worker-alice",
        "key_fingerprint": _FINGERPRINT,
        "service_urls": dict(_URLS),
    }
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    with _store(tmp_path):
        process = context.Process(
            target=_lease_attempt,
            args=(str(tmp_path.resolve()), owner_values, queue),
        )
        process.start()
        process.join(timeout=10)
        assert not process.is_alive()
        assert process.exitcode == 0
        assert queue.get(timeout=2) == ("error", "lease_busy")


def test_matching_completed_acknowledgment_marks_intent_completed(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body=_completed_ack(),
        )
    assert completed.state == "completed"
    assert completed.result == {"status_code": 201, "body": _completed_ack()}
    assert completed.operation_id == pending.operation_id
    assert completed.public_arguments == pending.public_arguments
    assert completed.acknowledgment_context == pending.acknowledgment_context


def test_repeated_completed_acknowledgment_is_idempotent(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body=_completed_ack(),
        )
        path = store.intent_path(_TASK_ID, "bid")
        completed_bytes = path.read_bytes()
        replay = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=completed.revision,
            status_code=201,
            body=_completed_ack(),
        )
        assert replay == completed
        assert path.read_bytes() == completed_bytes


def test_mutation_completion_persists_only_status_and_immutable_operation(tmp_path: Path) -> None:
    assert _runtime is not None
    descriptor, _arguments, acknowledgment = _action_case("accept_bid")
    intent = _runtime.NewTaskIntent(**descriptor)
    with _store(tmp_path, owner=_owner_for_action("accept_bid")) as store:
        pending = store.create(intent)
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="acceptance",
            expected_revision=pending.revision,
            status_code=200,
            body=acknowledgment,
        )
        path = store.intent_path(_TASK_ID, "acceptance")
        completed_bytes = path.read_bytes()
        later_view = dict(acknowledgment)
        later_view.update(status="approved", lifecycle_version=12, ruling_summary="later")
        replay = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="acceptance",
            expected_revision=completed.revision,
            status_code=200,
            body=later_view,
        )
    assert completed.result == {
        "status_code": 200,
        "operation": acknowledgment["operation"],
    }
    assert replay == completed
    assert path.read_bytes() == completed_bytes


@pytest.mark.parametrize("observation", ["pending", "error", "malformed", "foreign"])
def test_completed_intent_never_downgrades(observation: str, tmp_path: Path) -> None:
    assert _runtime is not None
    descriptor, _arguments, acknowledgment = _action_case("approve_task")
    intent = _runtime.NewTaskIntent(**descriptor)
    operation = acknowledgment["operation"]
    assert isinstance(operation, dict)
    if observation == "pending":
        changed = dict(operation)
        changed.update(operation_state="claimed", event_id=None, result=None)
        status_code, body = 202, {"task_id": _TASK_ID, "operation": changed}
    elif observation == "error":
        status_code, body = 502, {"error": "task_board_unavailable", "details": {}}
    elif observation == "foreign":
        changed = dict(operation)
        changed["operation_id"] = "op-00000000-0000-4000-8000-000000000999"
        status_code, body = 200, {**acknowledgment, "operation": changed}
    else:
        status_code, body = 200, {"malformed": True}
    with _store(tmp_path, owner=_owner_for_action("approve_task")) as store:
        pending = store.create(intent)
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=pending.revision,
            status_code=200,
            body=acknowledgment,
        )
        path = store.intent_path(_TASK_ID, "review-decision")
        completed_bytes = path.read_bytes()
        replay = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=completed.revision,
            status_code=status_code,
            body=body,
        )
    assert replay == completed
    assert path.read_bytes() == completed_bytes


@pytest.mark.parametrize(
    ("family", "malformed"),
    [("bid", False), ("bid", True), ("mutation", False), ("mutation", True)],
)
def test_foreign_or_malformed_success_is_never_completion(
    tmp_path: Path,
    family: str,
    malformed: bool,
) -> None:
    assert _runtime is not None
    if family == "bid":
        intent = _intent()
        body = _completed_ack()
        if malformed:
            body.pop("submitted_at")
        else:
            body["bid_id"] = "bid-foreign"
        status_code, slot = 201, "bid"
    else:
        descriptor, _arguments, body = _action_case("accept_bid")
        intent = _runtime.NewTaskIntent(**descriptor)
        operation = body["operation"]
        assert isinstance(operation, dict)
        if malformed:
            operation["target_lifecycle_version"] = True
        else:
            operation["operation_id"] = "op-00000000-0000-4000-8000-000000000999"
        status_code, slot = 200, "acceptance"
    owner = _owner_for_action("accept_bid") if family == "mutation" else _owner()
    with _store(tmp_path, owner=owner) as store:
        pending = store.create(intent)
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot=slot,
            expected_revision=pending.revision,
            status_code=status_code,
            body=body,
        )
    assert observed.state == ("pending" if malformed else "attention_required")
    assert observed.result is None
    assert observed.operation_id == pending.operation_id


@pytest.mark.parametrize(
    "mutation",
    ["request-hash", "worker", "escrow", "reward", "bid-amount"],
)
def test_internally_rehashed_changed_acknowledgment_facts_cannot_complete(
    tmp_path: Path,
    mutation: str,
) -> None:
    assert _runtime is not None
    action = "accept_bid" if mutation == "bid-amount" else "approve_task"
    descriptor, _arguments, base = _action_case(action)
    body = copy.deepcopy(base)
    operation = body["operation"]
    assert isinstance(operation, dict)
    result = operation["result"]
    assert isinstance(result, dict)
    if mutation == "request-hash":
        operation["request_sha256"] = "0" * 64
    elif mutation == "bid-amount":
        result["bid_amount"] = 89
    else:
        intent = operation["effect_intent"]
        assert isinstance(intent, dict)
        financial = intent["financial_request"]
        escrow = intent["expected_escrow"]
        assert isinstance(financial, dict) and isinstance(escrow, dict)
        if mutation == "worker":
            result["worker_id"] = "a-worker-foreign"
            financial["recipient_account_id"] = "a-worker-foreign"
        elif mutation == "escrow":
            financial["escrow_id"] = "esc-foreign"
            escrow["escrow_id"] = "esc-foreign"
        else:
            result["reward"] = 101
            escrow["amount"] = 101
        intent["financial_request_sha256"] = _canonical_hash(financial)
    with _store(tmp_path, owner=_owner_for_action(action)) as store:
        pending = store.create(_runtime.NewTaskIntent(**descriptor))
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot=str(descriptor["slot"]),
            expected_revision=pending.revision,
            status_code=200,
            body=body,
        )
    assert observed.state == "attention_required"
    assert observed.result is None


def test_typed_definitive_nonapplication_retains_original_identity(tmp_path: Path) -> None:
    error_body = {
        "error": "task_version_conflict",
        "message": "Task lifecycle version differs",
        "details": {
            "expected_lifecycle_version": 7,
            "actual_lifecycle_version": 8,
            "current_status": "open",
        },
    }
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        rejected = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body=error_body,
        )
    assert rejected.state == "definitive_rejection"
    assert rejected.operation_id == _OPERATION_ID
    assert rejected.expected_lifecycle_version == 7
    assert rejected.result == {"status_code": 409, "error": error_body}


@pytest.mark.parametrize("conflict", ["operation", "task", "worker"])
def test_foreign_202_or_caller_integrity_conflict_requires_attention(
    tmp_path: Path,
    conflict: str,
) -> None:
    assert _runtime is not None
    descriptor, _arguments, _completed = _action_case("approve_task")
    body = _claimed_approval_ack()
    operation = body["operation"]
    assert isinstance(operation, dict)
    if conflict == "operation":
        operation["operation_id"] = "op-00000000-0000-4000-8000-000000000999"
    elif conflict == "task":
        operation["task_id"] = "t-foreign"
    else:
        body["worker_id"] = "a-worker-foreign"
    with _store(tmp_path, owner=_owner_for_action("approve_task")) as store:
        pending = store.create(_runtime.NewTaskIntent(**descriptor))
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=pending.revision,
            status_code=202,
            body=body,
        )
    assert observed.state == "attention_required"
    assert observed.result is None


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (202, {"malformed": True}),
        (502, {"error": "task_board_unavailable", "message": "Unavailable", "details": {}}),
        (409, {"error": "task_version_conflict"}),
    ],
    ids=["malformed-202", "typed-502", "malformed-error"],
)
def test_uncertain_or_malformed_error_response_remains_pending(
    tmp_path: Path,
    status_code: int,
    body: object,
) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=status_code,
            body=body,
        )
    assert observed.state == "pending"
    assert observed.result is None
    assert observed.diagnostics[-1]["kind"] == "pending_unknown"


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (202, _claimed_approval_ack()),
        (200, {"task_id": _TASK_ID, "status": "approved", "lifecycle_version": 8}),
        (200, {"malformed": True}),
    ],
    ids=["matching-pending", "current-target-only", "malformed"],
)
def test_pending_current_target_or_malformed_acknowledgment_is_not_completion(
    tmp_path: Path,
    status_code: int,
    body: object,
) -> None:
    assert _runtime is not None
    descriptor, _arguments, _acknowledgment = _action_case("approve_task")
    with _store(tmp_path, owner=_owner_for_action("approve_task")) as store:
        pending = store.create(_runtime.NewTaskIntent(**descriptor))
        observed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="review-decision",
            expected_revision=pending.revision,
            status_code=status_code,
            body=body,
        )
    assert observed.state == "pending"
    assert observed.result is None
    assert observed.operation_id == descriptor["operation_id"]
    expected_kind = "remote_pending" if status_code == 202 else "pending_unknown"
    assert observed.diagnostics[-1]["kind"] == expected_kind


def test_foreign_pending_owner_is_blocked_and_records_both_operation_ids(tmp_path: Path) -> None:
    foreign_id = "op-00000000-0000-4000-8000-000000000999"
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        blocked = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=409,
            body={
                "error": "transition_pending",
                "message": "Another task transition owns the task",
                "details": {"winning_operation_id": foreign_id},
            },
        )
    assert blocked.state == "blocked"
    assert blocked.result is None
    assert blocked.diagnostics[-1] == {
        "kind": "foreign_pending_owner",
        "operation_id": _OPERATION_ID,
        "winning_operation_id": foreign_id,
    }


def test_acknowledgment_update_requires_current_revision(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        pending = store.create(_intent())
        updated = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot="bid",
            expected_revision=pending.revision,
            status_code=201,
            body={"malformed": True},
        )
        with pytest.raises(_runtime.RuntimeIntentError) as raised:
            store.record_acknowledgment(
                task_id=_TASK_ID,
                slot="bid",
                expected_revision=pending.revision,
                status_code=201,
                body=_completed_ack(),
            )
    assert updated.revision == 2
    assert raised.value.reason == "stale_revision"


def test_diagnostics_are_bounded_by_explicit_store_limit(tmp_path: Path) -> None:
    with _store(tmp_path, diagnostic_limit=3) as store:
        current = store.create(_intent())
        for sequence in range(6):
            current = store.record_acknowledgment(
                task_id=_TASK_ID,
                slot="bid",
                expected_revision=current.revision,
                status_code=201,
                body={"malformed_sequence": sequence},
            )
    assert len(current.diagnostics) == 3


def test_unfinished_reopens_and_discovers_owned_work_in_deterministic_order(
    tmp_path: Path,
) -> None:
    tasks = {
        state: (
            f"t-00000000-0000-4000-8000-{index:012d}",
            f"op-00000000-0000-4000-8000-{index:012d}",
        )
        for index, state in enumerate(
            ["pending", "blocked", "attention_required", "completed", "definitive_rejection"],
            start=301,
        )
    }
    with _store(tmp_path) as store:
        frozen = {
            state: store.create(_bid_intent_for(task_id, operation_id))
            for state, (task_id, operation_id) in tasks.items()
        }
        blocked_task, _blocked_operation = tasks["blocked"]
        frozen["blocked"] = store.record_acknowledgment(
            task_id=blocked_task,
            slot="bid",
            expected_revision=frozen["blocked"].revision,
            status_code=409,
            body={
                "error": "transition_pending",
                "message": "Another operation owns the task",
                "details": {"winning_operation_id": "op-00000000-0000-4000-8000-000000000999"},
            },
        )
        attention_task, attention_operation = tasks["attention_required"]
        foreign_bid = _bid_ack_for(attention_task, attention_operation)
        foreign_bid["bid_id"] = "bid-foreign"
        frozen["attention_required"] = store.record_acknowledgment(
            task_id=attention_task,
            slot="bid",
            expected_revision=frozen["attention_required"].revision,
            status_code=201,
            body=foreign_bid,
        )
        completed_task, completed_operation = tasks["completed"]
        frozen["completed"] = store.record_acknowledgment(
            task_id=completed_task,
            slot="bid",
            expected_revision=frozen["completed"].revision,
            status_code=201,
            body=_bid_ack_for(completed_task, completed_operation),
        )
        rejected_task, _rejected_operation = tasks["definitive_rejection"]
        frozen["definitive_rejection"] = store.record_acknowledgment(
            task_id=rejected_task,
            slot="bid",
            expected_revision=frozen["definitive_rejection"].revision,
            status_code=409,
            body={
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs",
                "details": {
                    "expected_lifecycle_version": 7,
                    "actual_lifecycle_version": 8,
                    "current_status": "open",
                },
            },
        )

    network = Mock()
    with _store(tmp_path) as reopened:
        unfinished = reopened.unfinished()
    network.assert_not_called()
    assert [(item.task_id, item.slot) for item in unfinished] == sorted(
        (tasks[state][0], "bid") for state in ("pending", "blocked", "attention_required")
    )
    assert {item.state for item in unfinished} == {"pending", "blocked", "attention_required"}


def test_unfinished_fails_closed_on_corrupt_owned_record(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.create(_intent())
        path = store.intent_path(_TASK_ID, "bid")
    path.write_text("{")
    path.chmod(0o600)
    with _store(tmp_path) as reopened, pytest.raises(_runtime.RuntimeIntentError) as raised:
        reopened.unfinished()
    assert raised.value.reason == "corrupt_record"


@pytest.mark.parametrize(
    "action",
    ["submit_bid", "accept_bid", "submit_deliverable", "approve_task", "dispute_task"],
)
def test_all_native_runtime_slots_freeze_public_arguments_separately_from_ack_context(
    tmp_path: Path,
    action: str,
) -> None:
    assert _runtime is not None
    descriptor, public_arguments, acknowledgment = _action_case(action)
    intent = _runtime.NewTaskIntent(**descriptor)
    identity_root = tmp_path / action
    identity_root.mkdir()
    with _store(identity_root, owner=_owner_for_action(action)) as store:
        frozen = store.create(intent)
        completed = store.record_acknowledgment(
            task_id=_TASK_ID,
            slot=str(descriptor["slot"]),
            expected_revision=frozen.revision,
            status_code=201 if action == "submit_bid" else 200,
            body=acknowledgment,
        )
    assert frozen.public_arguments == public_arguments
    assert frozen.acknowledgment_context == descriptor["acknowledgment_context"]
    assert "result_facts" not in frozen.public_arguments
    assert frozen.public_arguments is not frozen.acknowledgment_context
    if action != "submit_bid":
        operation = acknowledgment["operation"]
        context = descriptor["acknowledgment_context"]
        assert isinstance(operation, dict) and isinstance(context, dict)
        assert operation["request_sha256"] == context["request_sha256"]
    assert completed.state == "completed"
    assert completed.operation_id == frozen.operation_id
