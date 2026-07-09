# Events Architecture

## Overview

The `events` table is a shared append-only log in `data/economy.db`.

`services/observatory/src/observatory_service/services/events.py` is currently the only runtime consumer (history queries + SSE stream).

No service currently writes to `events` in production code. The only active writer today is `tools/seed-economy.sh` via its `emit()` helper, which INSERTs events alongside simulated state mutations.

## Event Types by Service

| Source | Event Types |
|---|---|
| identity | `agent.registered` |
| bank | `account.created`, `salary.paid`, `escrow.locked`, `escrow.released`, `escrow.split` |
| board | `task.created`, `task.cancelled`, `task.expired`, `bid.submitted`, `task.accepted`, `asset.uploaded`, `task.submitted`, `task.approved`, `task.auto_approved`, `task.disputed`, `task.ruled` |
| reputation | `feedback.revealed` |
| court | `claim.filed`, `rebuttal.submitted`, `ruling.delivered` |

## Emission Points

Emit only after the corresponding state mutation commits successfully.

- Identity
`services/identity/src/identity_service/services/agent_registry.py::register_agent()` -> `identity/agent.registered`.

- Central Bank
`services/central-bank/src/central_bank_service/services/ledger.py::create_account()` -> `bank/account.created`.
`services/central-bank/src/central_bank_service/services/ledger.py::credit()` -> `bank/salary.paid` (salary credits only).
`services/central-bank/src/central_bank_service/services/ledger.py::escrow_lock()` -> `bank/escrow.locked`.
`services/central-bank/src/central_bank_service/services/ledger.py::escrow_release()` -> `bank/escrow.released`.
`services/central-bank/src/central_bank_service/services/ledger.py::escrow_split()` -> `bank/escrow.split`.

- Task Board
`services/task-board/src/task_board_service/services/task_manager.py::create_task()` -> `board/task.created`.
`services/task-board/src/task_board_service/services/task_manager.py::cancel_task()` -> `board/task.cancelled`.
`services/task-board/src/task_board_service/services/task_manager.py::submit_bid()` -> `board/bid.submitted`.
`services/task-board/src/task_board_service/services/task_manager.py::accept_bid()` -> `board/task.accepted`.
`services/task-board/src/task_board_service/services/task_manager.py::submit_deliverable()` -> `board/task.submitted`.
`services/task-board/src/task_board_service/services/task_manager.py::approve_task()` -> `board/task.approved`.
`services/task-board/src/task_board_service/services/task_manager.py::dispute_task()` -> `board/task.disputed`.
`services/task-board/src/task_board_service/services/task_manager.py::record_ruling()` -> `board/task.ruled`.
`services/task-board/src/task_board_service/services/asset_manager.py::upload_asset()` -> `board/asset.uploaded`.
`services/task-board/src/task_board_service/services/deadline_evaluator.py::evaluate_deadline()` -> `board/task.expired` and `board/task.auto_approved` for deadline-driven transitions.

- Reputation
`services/reputation/src/reputation_service/services/feedback.py::submit_feedback()` -> `reputation/feedback.revealed` only when the resulting record is visible (`visible=True`, mutual reveal).

- Court
`services/court/src/court_service/services/dispute_service.py::file_dispute()` -> `court/claim.filed`.
`services/court/src/court_service/services/dispute_service.py::submit_rebuttal()` -> `court/rebuttal.submitted`.
`services/court/src/court_service/services/ruling_orchestrator.py::execute_ruling()` -> `court/ruling.delivered`.

## Architecture Decision: Direct SQLite vs Central Collector

Option A - Direct SQLite writes
- Each service opens shared `economy.db` and INSERTs into `events`.
- Pros: simplest implementation, no network hop, same transaction boundary as local mutation.
- Risks: many writers to one SQLite file.
- Mitigation: project already uses SQLite WAL mode (`PRAGMA journal_mode=WAL`), which is adequate for this single-host simulation workload.

Option B - Central event collector
- Add an HTTP event-ingest endpoint (new service or Observatory extension) and post events to it.
- Pros: cleaner separation of concerns, one writer process.
- Costs: extra service/API complexity, network failure modes, eventual-consistency decisions.

Recommendation
- Choose Option A now.
- Rationale: the economy runs as a single-host simulation; services already share the same DB path configuration; `tools/seed-economy.sh` already demonstrates direct-writer behavior.
- Revisit Option B when deploying across multiple hosts.

## Implementation Sketch

Add a shared writer in `libs/service-commons/`:

- `class EventWriter` initialized with explicit DB path.
- API: `async def emit(source, event_type, task_id, agent_id, summary, payload)`.
- Behavior:
  - Serialize `payload` as JSON.
  - Insert into `events(event_source,event_type,timestamp,task_id,agent_id,summary,payload)`.
  - Use UTC ISO timestamps (`Z` suffix).
  - Keep write semantics append-only.

Integrate `EventWriter` in each service method listed above immediately after successful state commit.
