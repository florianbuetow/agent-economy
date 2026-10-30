"""MP-05 durable Task Feeder posting acceptance tests."""

from __future__ import annotations

import hashlib
import importlib
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import task_feeder.reader as reader_module
from base_agent.runtime_config import RuntimeStateConfig
from base_agent.runtime_intents import FileTaskIntentStore, RuntimeIntentError
from base_agent.runtime_task_commands import runtime_owner_from_agent
from task_feeder.config import TaskFeederConfig
from task_feeder.loop import TaskFeederLoop
from tests.fakes.mp05_posting_fixtures import posting_intent, posting_response

from ._runtime_fixtures import POSTER_ID, make_agent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from service_auth import TaskPostingIntent, TaskPostingResponse

    from base_agent.agent import BaseAgent


_FIRST_TASK: dict[str, object] = {
    "title": "Keep  spaces and é",
    "spec": "Return the exact term:  x + y ",
    "solutions": ["x + y", " y + x"],
    "level": 2,
    "problem_type": "exact_text",
    "solution_note": None,
}
_SECOND_TASK: dict[str, object] = {
    "title": "Second task",
    "spec": "Calculate 2 + 3",
    "solutions": ["5"],
    "level": 4,
    "problem_type": "addition",
    "solution_note": "Keep this note.",
}
_SOURCE_MAX_BYTES = 8_388_608


def _line(task: dict[str, object], terminator: bytes = b"\n") -> bytes:
    return (
        json.dumps(
            task,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + terminator
    )


def _write_source(path: Path, tasks: tuple[dict[str, object], ...]) -> None:
    path.write_bytes(b"".join(_line(task) for task in tasks))


def _config(path: Path, *, shuffle: bool = False, base_reward: int = 10) -> TaskFeederConfig:
    return TaskFeederConfig(
        handle="feeder-fixture-reconciliation",
        tasks_file=str(path),
        source_max_bytes=_SOURCE_MAX_BYTES,
        feed_interval_seconds=15,
        max_open_tasks=5,
        bidding_deadline_seconds=120,
        acceptance_window_seconds=180,
        execution_deadline_seconds=300,
        review_deadline_seconds=240,
        review_interval_seconds=30,
        retry_backoff_seconds=1,
        auto_approve_on_error=False,
        base_reward=base_reward,
        reward_per_level=10,
        shuffle=shuffle,
    )


def _posting_types() -> tuple[type[object], type[object]]:
    posting_store_type = importlib.import_module("base_agent.runtime_postings").FileTaskPostingStore
    coordinator_type = importlib.import_module("task_feeder.posting").TaskFeederPostingCoordinator
    return cast("type[object]", posting_store_type), cast("type[object]", coordinator_type)


@contextmanager
def _posting_session(tmp_path: Path, agent: MagicMock) -> Iterator[Any]:
    posting_store_type, _coordinator_type = _posting_types()
    runtime_config = RuntimeStateConfig(
        environment_id="mp05-feeder-posting",
        directory=tmp_path,
        diagnostic_limit=8,
        max_record_bytes=65_536,
    )
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=cast("BaseAgent", agent),
        role="task-feeder",
        profile="feeder-fixture-reconciliation",
        handle="feeder-fixture-reconciliation",
    )
    with (
        FileTaskIntentStore(
            root_directory=tmp_path,
            owner=owner,
            diagnostic_limit=8,
            max_record_bytes=65_536,
        ) as intent_store,
        posting_store_type.attach(
            intent_store,
            max_source_bytes=_SOURCE_MAX_BYTES,
        ) as postings,
    ):
        yield postings


def _configure_prepare(agent: MagicMock, *, start_ordinal: int = 1) -> list[TaskPostingIntent]:
    prepared: list[TaskPostingIntent] = []

    def prepare(
        title: str,
        spec: str,
        reward: int,
        bidding_deadline_seconds: int,
        acceptance_window_seconds: int,
        execution_deadline_seconds: int,
        review_deadline_seconds: int,
    ) -> TaskPostingIntent:
        ordinal = start_ordinal + len(prepared)
        intent = posting_intent(
            operation_id=f"op-00000000-0000-4000-8000-{ordinal:012d}",
            task_id=f"t-00000000-0000-4000-8000-{ordinal:012d}",
            actor_id=POSTER_ID,
            title=title,
            spec=spec,
            reward=reward,
            bidding_deadline_seconds=bidding_deadline_seconds,
            acceptance_window_seconds=acceptance_window_seconds,
            execution_deadline_seconds=execution_deadline_seconds,
            review_deadline_seconds=review_deadline_seconds,
        )
        prepared.append(intent)
        return intent

    agent.prepare_task_posting = MagicMock(side_effect=prepare)
    return prepared


def _published_response(intent: TaskPostingIntent) -> TaskPostingResponse:
    return posting_response(intent, "published")


def _awaiting_response(intent: TaskPostingIntent) -> TaskPostingResponse:
    return posting_response(intent, "awaiting_lock")


def _status_response(intent: TaskPostingIntent, state: str) -> TaskPostingResponse:
    return posting_response(
        intent,
        cast("Any", state),
        status_observation=True,
    )


def _not_found(operation_id: str) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET", f"https://task-board.feeder-fixture.test/task-publications/{operation_id}"
    )
    response = httpx.Response(
        404,
        request=request,
        json={
            "error": "publication_not_found",
            "message": "Task publication was not found",
            "details": {},
        },
    )
    return httpx.HTTPStatusError("not found", request=request, response=response)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.mark.unit
def test_snapshot_preserves_bytes_duplicates_physical_lines_and_immutable_text(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    duplicate = _line(_FIRST_TASK, b"\r\n")
    source_bytes = b"\n" + duplicate + b"  \n" + duplicate
    source_path.write_bytes(source_bytes)

    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)

    assert snapshot.source_path == source_path.resolve()
    assert snapshot.source_snapshot_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert snapshot.source_size_bytes == len(source_bytes)
    assert snapshot.source_bytes == source_bytes
    assert isinstance(snapshot.source_bytes, bytes)
    assert snapshot.source_entry_count == 2
    assert isinstance(snapshot.entries, tuple)
    first, second = snapshot.entries
    assert (first.index, first.line_number) == (0, 2)
    assert (second.index, second.line_number) == (1, 4)
    assert first.line_sha256 == hashlib.sha256(duplicate).hexdigest()
    assert second.line_sha256 == first.line_sha256
    assert first.raw_task_sha256 == second.raw_task_sha256
    assert first.entry_id != second.entry_id
    assert first.raw_task.solutions == ("x + y", " y + x")
    assert first.raw_task.as_json() == _FIRST_TASK

    exported = first.selection_json()
    assert set(exported) == {
        "source_entry_index",
        "source_line_number",
        "source_line_sha256",
        "raw_task",
    }
    cast("dict[str, object]", exported["raw_task"])["title"] = "mutated"
    assert first.raw_task.title == "Keep  spaces and é"
    assert first.selection_json()["raw_task"] == _FIRST_TASK
    with pytest.raises((AttributeError, TypeError)):
        first.raw_task.solutions[0] = "changed"  # type: ignore[index]


@pytest.mark.unit
@pytest.mark.parametrize(
    "source_bytes",
    (
        b"\n  \r\n",
        b"not-json\n",
        b'[{"title":"wrong top level"}]\n',
        b'{"title":"missing exact fields"}\n',
        b"\xff\n",
    ),
)
def test_snapshot_rejects_empty_malformed_or_non_utf8_input(
    tmp_path: Path, source_bytes: bytes
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    source_path.write_bytes(source_bytes)

    with pytest.raises((UnicodeError, ValueError)):
        reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)


@pytest.mark.unit
def test_snapshot_rejects_source_before_parsing_when_it_exceeds_the_explicit_limit(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    source_bytes = _line(_FIRST_TASK)
    source_path.write_bytes(source_bytes)

    with pytest.raises(ValueError, match=r"limit|large|bytes"):
        reader_module.load_task_snapshot(source_path, max_bytes=len(source_bytes) - 1)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shuffle_selection_is_deterministic_for_each_complete_cycle(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    tasks = (
        _FIRST_TASK,
        _SECOND_TASK,
        {**_SECOND_TASK, "title": "Third task", "level": 6},
    )
    _write_source(source_path, tasks)
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path, shuffle=True)
    agent = make_agent()
    prepared = _configure_prepare(agent)
    agent.submit_task_posting = AsyncMock(side_effect=_published_response)
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, agent) as postings:
        coordinator = cast("Any", coordinator_type)(agent, postings)
        coordinator.bind_current_source(snapshot, config)
        for _ in range(snapshot.source_entry_count * 2):
            record = await coordinator.reserve_and_submit_next()
            assert record.state == "published"

        records = postings.records()
        head = postings.head()

    assert head is not None
    binding_sha256 = head["source_binding_sha256"]
    expected_indexes: list[int] = []
    for cycle_index in range(2):
        cycle_id = _canonical_sha256(
            {
                "source_binding_sha256": binding_sha256,
                "cycle_index": cycle_index,
            }
        )
        ordered = sorted(
            snapshot.entries,
            key=lambda entry: (
                _canonical_sha256(
                    {
                        "cycle_id": cycle_id,
                        "source_entry_id": entry.entry_id,
                    }
                ),
                entry.index,
            ),
        )
        expected_indexes.extend(entry.index for entry in ordered)

    assert [record.source_entry_index for record in records] == expected_indexes
    assert [record.selection_sequence for record in records] == list(range(6))
    assert [record.cycle_index for record in records] == [0, 0, 0, 1, 1, 1]
    assert [record.cycle_position for record in records] == [0, 1, 2, 0, 1, 2]
    assert len({record.selection_id for record in records}) == 6
    assert len(prepared) == 6


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lost_submit_response_replays_saved_intent_when_current_source_is_missing(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    first_agent = make_agent()
    prepared = _configure_prepare(first_agent)
    request = httpx.Request("POST", "https://task-board.feeder-fixture.test/tasks")
    first_agent.submit_task_posting = AsyncMock(
        side_effect=httpx.ReadTimeout("response lost", request=request)
    )
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, first_agent) as postings:
        first = cast("Any", coordinator_type)(first_agent, postings)
        first.bind_current_source(snapshot, config)
        await first.reserve_and_submit_next()
        saved = postings.records()[0]

    assert len(prepared) == 1
    assert saved.intent == prepared[0]
    source_path.unlink()
    assert not source_path.exists()
    second_agent = make_agent()
    second_agent.prepare_task_posting = MagicMock(
        side_effect=AssertionError("restart must not allocate replacement identities")
    )
    second_agent.get_task_posting = AsyncMock(side_effect=_not_found(saved.intent.operation_id))

    async def publish_same(intent: TaskPostingIntent) -> TaskPostingResponse:
        assert intent == saved.intent
        return _published_response(intent)

    second_agent.submit_task_posting = AsyncMock(side_effect=publish_same)
    with _posting_session(tmp_path, second_agent) as postings:
        restarted = cast("Any", coordinator_type)(second_agent, postings)
        observed = await restarted.resume_one()
        persisted = postings.records()[0]

    assert observed is not None
    assert observed.state == "published"
    assert persisted.intent == saved.intent
    second_agent.prepare_task_posting.assert_not_called()
    second_agent.get_task_posting.assert_awaited_once_with(saved.intent.operation_id)
    second_agent.submit_task_posting.assert_awaited_once_with(saved.intent)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_awaiting_ack_uses_status_and_never_prepares_or_resubmits_after_restart(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    first_agent = make_agent()
    prepared = _configure_prepare(first_agent)
    first_agent.submit_task_posting = AsyncMock(side_effect=_awaiting_response)
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, first_agent) as postings:
        first = cast("Any", coordinator_type)(first_agent, postings)
        first.bind_current_source(snapshot, config)
        await first.reserve_and_submit_next()
        awaiting = postings.records()[0]

    assert awaiting.state == "awaiting_lock"
    second_agent = make_agent()
    second_agent.prepare_task_posting = MagicMock(
        side_effect=AssertionError("saved sequence must not be prepared again")
    )
    second_agent.get_task_posting = AsyncMock(
        return_value=_status_response(awaiting.intent, "published")
    )
    second_agent.submit_task_posting = AsyncMock(
        side_effect=AssertionError("published status must not be submitted again")
    )
    with _posting_session(tmp_path, second_agent) as postings:
        restarted = cast("Any", coordinator_type)(second_agent, postings)
        observed = await restarted.resume_one()

    assert observed is not None
    assert observed.state == "published"
    assert observed.intent == prepared[0]
    second_agent.prepare_task_posting.assert_not_called()
    second_agent.submit_task_posting.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_invalid_untyped_ack_is_attention_and_recovers_by_status_only(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    agent = make_agent()
    prepared = _configure_prepare(agent)
    agent.submit_task_posting = AsyncMock(return_value={"task_id": "looks-successful"})
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, agent) as postings:
        coordinator = cast("Any", coordinator_type)(agent, postings)
        coordinator.bind_current_source(snapshot, config)
        await coordinator.reserve_and_submit_next()
        attention = postings.records()[0]
        assert attention.state == "attention_required"
        assert attention.intent == prepared[0]
        assert attention.publication is None
        assert attention.diagnostics[-1]["code"] == "invalid_acknowledgment"

        agent.get_task_posting = AsyncMock(
            return_value=_status_response(attention.intent, "published")
        )
        agent.submit_task_posting.reset_mock()
        recovered = await coordinator.resume_one()

    assert recovered is not None
    assert recovered.state == "published"
    agent.get_task_posting.assert_awaited_once_with(attention.intent.operation_id)
    agent.submit_task_posting.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_attention_pending_status_returns_without_submit_in_the_same_attempt(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    agent = make_agent()
    prepared = _configure_prepare(agent)
    agent.submit_task_posting = AsyncMock(return_value={"task_id": "not-a-typed-ack"})
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, agent) as postings:
        coordinator = cast("Any", coordinator_type)(agent, postings)
        coordinator.bind_current_source(snapshot, config)
        await coordinator.reserve_and_submit_next()
        attention = postings.records()[0]
        assert attention.state == "attention_required"

        agent.get_task_posting = AsyncMock(
            return_value=_status_response(attention.intent, "awaiting_lock")
        )
        agent.submit_task_posting.reset_mock()
        observed = await coordinator.resume_one()

    assert observed is not None
    assert observed.state == "awaiting_lock"
    assert observed.intent == prepared[0]
    agent.get_task_posting.assert_awaited_once_with(attention.intent.operation_id)
    agent.submit_task_posting.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_blocked_record_is_not_polled_or_dispatched_and_blocks_fresh_selection(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    agent = make_agent()
    prepared = _configure_prepare(agent)
    agent.submit_task_posting = AsyncMock(
        side_effect=lambda intent: posting_response(intent, "blocked_integrity")
    )
    agent.get_task_posting = AsyncMock()
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, agent) as postings:
        coordinator = cast("Any", coordinator_type)(agent, postings)
        coordinator.bind_current_source(snapshot, config)
        blocked = await coordinator.reserve_and_submit_next()
        assert blocked.state == "blocked_integrity"
        assert blocked.intent == prepared[0]

        agent.submit_task_posting.reset_mock()
        assert await coordinator.resume_one() is None
        with pytest.raises(RuntimeIntentError, match=r"blocked|unresolved"):
            await coordinator.reserve_and_submit_next()

    assert len(prepared) == 1
    agent.get_task_posting.assert_not_awaited()
    agent.submit_task_posting.assert_not_awaited()


@pytest.mark.unit
def test_coordinator_rejects_an_sdk_identity_that_is_not_the_store_owner(
    tmp_path: Path,
) -> None:
    owner_agent = make_agent()
    wrong_agent = make_agent()
    wrong_agent.agent_id = "a-different-poster"
    _posting_store_type, coordinator_type = _posting_types()

    with (
        _posting_session(tmp_path, owner_agent) as postings,
        pytest.raises(RuntimeIntentError, match="owner") as error,
    ):
        cast("Any", coordinator_type)(wrong_agent, postings)

    assert error.value.reason == "owner_conflict"
    wrong_agent.prepare_task_posting.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ("source", "config"))
async def test_changed_source_or_posting_terms_rebuilds_old_map_but_blocks_fresh_selection(
    tmp_path: Path, mismatch: str
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    original_snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    original_config = _config(source_path)
    first_agent = make_agent()
    _configure_prepare(first_agent)
    first_agent.submit_task_posting = AsyncMock(side_effect=_published_response)
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, first_agent) as postings:
        first = cast("Any", coordinator_type)(first_agent, postings)
        first.bind_current_source(original_snapshot, original_config)
        published = await first.reserve_and_submit_next()
        assert published.state == "published"

    if mismatch == "source":
        _write_source(source_path, (_SECOND_TASK,))
        current_snapshot = reader_module.load_task_snapshot(
            source_path, max_bytes=_SOURCE_MAX_BYTES
        )
        current_config = original_config
    else:
        current_snapshot = original_snapshot
        current_config = _config(source_path, base_reward=11)

    restarted_agent = make_agent()
    restarted_agent.prepare_task_posting = MagicMock(
        side_effect=AssertionError("mismatch must block new selection")
    )
    with _posting_session(tmp_path, restarted_agent) as postings:
        restarted = cast("Any", coordinator_type)(restarted_agent, postings)
        assert await restarted.replay_unfinished() == ()
        rebuilt = restarted.rebuild_task_map()
        assert rebuilt == {published.intent.task_id: original_snapshot.entries[0].raw_task}
        assert restarted.published_count == 1

        with pytest.raises(RuntimeIntentError, match="source"):
            restarted.bind_current_source(current_snapshot, current_config)
        assert restarted.source_ready is False
        with pytest.raises(RuntimeIntentError):
            await restarted.reserve_and_submit_next()

    restarted_agent.prepare_task_posting.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_identical_relocated_source_keeps_original_binding_and_allows_fresh_selection(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "original" / "tasks.jsonl"
    original_path.parent.mkdir()
    _write_source(original_path, (_FIRST_TASK,))
    original_snapshot = reader_module.load_task_snapshot(original_path, max_bytes=_SOURCE_MAX_BYTES)
    original_config = _config(original_path)
    first_agent = make_agent()
    _configure_prepare(first_agent)
    first_agent.submit_task_posting = AsyncMock(side_effect=_published_response)
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, first_agent) as postings:
        first = cast("Any", coordinator_type)(first_agent, postings)
        first.bind_current_source(original_snapshot, original_config)
        await first.reserve_and_submit_next()
        original_head = postings.head()
        original_record = postings.records()[0].as_json()

    relocated_path = tmp_path / "restored" / "tasks.jsonl"
    relocated_path.parent.mkdir()
    relocated_path.write_bytes(original_snapshot.source_bytes)
    relocated_snapshot = reader_module.load_task_snapshot(
        relocated_path, max_bytes=_SOURCE_MAX_BYTES
    )
    relocated_config = _config(relocated_path)
    restarted_agent = make_agent()
    _configure_prepare(restarted_agent, start_ordinal=2)
    restarted_agent.submit_task_posting = AsyncMock(side_effect=_published_response)
    with _posting_session(tmp_path, restarted_agent) as postings:
        restarted = cast("Any", coordinator_type)(restarted_agent, postings)
        restarted.bind_current_source(relocated_snapshot, relocated_config)
        assert restarted.source_ready is True
        assert postings.head() == original_head
        assert postings.head()["source_binding"]["source_path"] == str(original_path.resolve())
        assert postings.records()[0].as_json() == original_record

        second = await restarted.reserve_and_submit_next()

    assert second.state == "published"
    assert second.selection_sequence == 1
    assert second.source_entry_index == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rebuild_task_map_recovers_a_published_ack_after_process_crash(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    snapshot = reader_module.load_task_snapshot(source_path, max_bytes=_SOURCE_MAX_BYTES)
    config = _config(source_path)
    first_agent = make_agent()
    _configure_prepare(first_agent)
    first_agent.submit_task_posting = AsyncMock(side_effect=_published_response)
    _posting_store_type, coordinator_type = _posting_types()

    with _posting_session(tmp_path, first_agent) as postings:
        first = cast("Any", coordinator_type)(first_agent, postings)
        first.bind_current_source(snapshot, config)
        published = await first.reserve_and_submit_next()
        assert published.state == "published"
        # Simulate immediate process loss: no ephemeral task-map update is retained.

    restarted_agent = make_agent()
    restarted_agent.prepare_task_posting = MagicMock()
    restarted_agent.submit_task_posting = AsyncMock()
    with _posting_session(tmp_path, restarted_agent) as postings:
        restarted = cast("Any", coordinator_type)(restarted_agent, postings)
        assert await restarted.resume_one() is None
        rebuilt = restarted.rebuild_task_map()
        rebuilt[published.intent.task_id] = cast("Any", _SECOND_TASK)
        rebuilt_again = restarted.rebuild_task_map()

    assert rebuilt_again == {published.intent.task_id: snapshot.entries[0].raw_task}
    assert restarted.published_count == 1
    restarted_agent.prepare_task_posting.assert_not_called()
    restarted_agent.get_task_posting.assert_not_awaited()
    restarted_agent.submit_task_posting.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_feed_once_replays_before_capacity_and_lookup_failure_forbids_fresh_selection(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    _write_source(source_path, (_FIRST_TASK,))
    config = _config(source_path)
    events: list[str] = []
    agent = make_agent()

    async def lookup(**_kwargs: object) -> list[object]:
        events.append("capacity")
        raise httpx.ReadTimeout(
            "capacity lookup unavailable",
            request=httpx.Request("GET", "https://task-board.feeder-fixture.test/tasks"),
        )

    async def resume() -> None:
        events.append("resume")

    agent.list_tasks = AsyncMock(side_effect=lookup)
    posting = MagicMock()
    posting.resume_one = AsyncMock(side_effect=resume)
    posting.rebuild_task_map.return_value = {}
    posting.source_ready = True
    posting.reserve_and_submit_next = AsyncMock()
    loop = TaskFeederLoop(agent, config, posting)

    with pytest.raises(httpx.ReadTimeout, match="capacity lookup unavailable"):
        await loop.feed_once()

    assert events == ["resume", "capacity"]
    posting.resume_one.assert_awaited_once_with()
    posting.rebuild_task_map.assert_called_once_with()
    posting.reserve_and_submit_next.assert_not_awaited()
