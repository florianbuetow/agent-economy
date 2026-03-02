# UI Service Integration Tests — Implementation Plan

## Goal

Create 123 integration tests for the UI service API endpoints. Tests verify data is correctly read from a seeded SQLite database and that database mutations are reflected without restart (staleness tests).

**No existing files are modified. Only new files are created.**

## Rules

- All tests marked `@pytest.mark.integration`
- Use `asyncio_mode = "auto"` (already configured in pyproject.toml)
- Do NOT modify any existing test files or Python source files
- All assertions must be based on the seed data you insert
- Event payloads must be stored as JSON **strings** (the service calls `json.loads()`)
- Use `PRAGMA foreign_keys = OFF` during seed inserts
- Use `PRAGMA journal_mode = WAL` so the read-only service connection can see writes from the `write_db` fixture

## Files to Create

All files go in `services/ui/tests/integration/`:

```
services/ui/tests/integration/
  conftest.py          # Overwrite the empty existing one
  helpers.py           # Seed data constants and insert function
  test_health.py       # 12 tests
  test_agents.py       # 25 tests
  test_metrics.py      # 22 tests
  test_tasks.py        # 19 tests
  test_events.py       # 22 tests
  test_quarterly.py    # 17 tests
```

## Phase 1: Create helpers.py

This file defines ALL seed data as Python constants and provides an `insert_seed_data(conn)` function that populates all tables.

### Seed Data

**5 agents:**

| agent_id | name | public_key | registered_at |
|---|---|---|---|
| `a-alice` | Alice | `ed25519:QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE=` | `2026-01-15T10:00:00Z` |
| `a-bob` | Bob | `ed25519:QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI=` | `2026-01-16T11:00:00Z` |
| `a-carol` | Carol | `ed25519:Q0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0M=` | `2026-01-17T12:00:00Z` |
| `a-dave` | Dave | `ed25519:RERERERERERERERERERERERERERERERERERERERERERERA=` | `2026-01-18T13:00:00Z` |
| `a-eve` | Eve | `ed25519:RUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVFRUVE=` | `2026-02-01T09:00:00Z` |

**5 bank accounts:**

| account_id | balance | created_at |
|---|---|---|
| `a-alice` | 800 | `2026-01-15T10:00:00Z` |
| `a-bob` | 1200 | `2026-01-16T11:00:00Z` |
| `a-carol` | 500 | `2026-01-17T12:00:00Z` |
| `a-dave` | 300 | `2026-01-18T13:00:00Z` |
| `a-eve` | 1000 | `2026-02-01T09:00:00Z` |

**10 bank transactions:**

| tx_id | account_id | type | amount | balance_after | reference | timestamp |
|---|---|---|---|---|---|---|
| `tx-1` | `a-alice` | credit | 1000 | 1000 | `salary_r1_alice` | `2026-01-20T10:00:00Z` |
| `tx-2` | `a-bob` | credit | 1000 | 1000 | `salary_r1_bob` | `2026-01-20T10:00:00Z` |
| `tx-3` | `a-carol` | credit | 1000 | 1000 | `salary_r1_carol` | `2026-01-20T10:00:00Z` |
| `tx-4` | `a-dave` | credit | 1000 | 1000 | `salary_r1_dave` | `2026-01-20T10:00:00Z` |
| `tx-5` | `a-eve` | credit | 1000 | 1000 | `salary_r1_eve` | `2026-02-01T10:00:00Z` |
| `tx-6` | `a-alice` | escrow_lock | 200 | 800 | `esc-1` | `2026-02-01T10:05:00Z` |
| `tx-7` | `a-alice` | escrow_lock | 100 | 700 | `esc-2` | `2026-02-02T10:05:00Z` |
| `tx-8` | `a-bob` | escrow_release | 200 | 1200 | `esc-1` | `2026-02-10T15:00:00Z` |
| `tx-9` | `a-carol` | escrow_release | 70 | 570 | `esc-5` | `2026-03-01T12:00:00Z` |
| `tx-10` | `a-alice` | escrow_lock | 150 | 550 | `esc-6` | `2026-03-02T00:00:00Z` |

**6 escrows:**

| escrow_id | payer_account_id | amount | task_id | status | created_at | resolved_at |
|---|---|---|---|---|---|---|
| `esc-1` | `a-alice` | 200 | `t-task1` | released | `2026-02-01T10:05:00Z` | `2026-02-10T15:00:00Z` |
| `esc-2` | `a-alice` | 100 | `t-task2` | locked | `2026-02-02T10:05:00Z` | NULL |
| `esc-3` | `a-alice` | 80 | `t-task3` | released | `2026-02-05T10:05:00Z` | `2026-02-15T10:00:00Z` |
| `esc-4` | `a-eve` | 50 | `t-task4` | released | `2026-02-10T09:00:00Z` | `2026-02-20T14:00:00Z` |
| `esc-5` | `a-dave` | 100 | `t-task5` | split | `2026-02-15T10:00:00Z` | `2026-03-01T12:00:00Z` |
| `esc-6` | `a-alice` | 150 | `t-task6` | locked | `2026-03-02T00:00:00Z` | NULL |

**12 tasks (ALL statuses covered):**

| task_id | poster_id | worker_id | title | spec | reward | status | accepted_bid_id | escrow_id | bidding_deadline_seconds | deadline_seconds | review_deadline_seconds |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `t-task1` | `a-alice` | `a-bob` | Build Login Page | Login spec text | 200 | approved | `bid-1` | `esc-1` | 86400 | 604800 | 172800 |
| `t-task2` | `a-alice` | `a-carol` | Design Dashboard | Dashboard spec | 100 | accepted | `bid-3` | `esc-2` | 86400 | 604800 | 172800 |
| `t-task3` | `a-alice` | `a-bob` | API Integration | API spec | 80 | approved | `bid-5` | `esc-3` | 86400 | 604800 | 172800 |
| `t-task4` | `a-eve` | NULL | Write Tests | Test spec | 50 | cancelled | NULL | `esc-4` | 86400 | 604800 | 172800 |
| `t-task5` | `a-dave` | `a-carol` | Fix Bug | Bug spec | 100 | ruled | `bid-7` | `esc-5` | 86400 | 604800 | 172800 |
| `t-task6` | `a-alice` | NULL | Mobile App | Mobile spec | 150 | open | NULL | `esc-6` | 86400 | 604800 | 172800 |
| `t-task7` | `a-bob` | NULL | Data Pipeline | Pipeline spec | 120 | open | NULL | NULL | 86400 | 604800 | 172800 |
| `t-task8` | `a-carol` | `a-bob` | Email Service | Email spec | 90 | submitted | `bid-9` | NULL | 86400 | 604800 | 172800 |
| `t-task9` | `a-eve` | NULL | Search Engine | Search spec | 300 | open | NULL | NULL | 86400 | 604800 | 172800 |
| `t-task10` | `a-bob` | NULL | Chat System | Chat spec | 5 | expired | NULL | NULL | 86400 | 604800 | 172800 |
| `t-task11` | `a-dave` | `a-eve` | Logging Setup | Log spec | 60 | disputed | `bid-11` | NULL | 86400 | 604800 | 172800 |
| `t-task12` | `a-carol` | NULL | File Upload | Upload spec | 40 | open | NULL | NULL | 86400 | 604800 | 172800 |

**Task timestamps:** (set these on each task row)

- t-task1: created_at=2026-02-01T10:00:00Z, accepted_at=2026-02-03T12:00:00Z, submitted_at=2026-02-08T10:00:00Z, approved_at=2026-02-10T15:00:00Z, bidding_deadline=2026-02-02T10:00:00Z, execution_deadline=2026-02-10T12:00:00Z, review_deadline=2026-02-10T10:00:00Z
- t-task2: created_at=2026-02-02T10:00:00Z, accepted_at=2026-02-04T14:00:00Z, bidding_deadline=2026-02-03T10:00:00Z, execution_deadline=2026-02-11T14:00:00Z
- t-task3: created_at=2026-02-05T10:00:00Z, accepted_at=2026-02-06T09:00:00Z, approved_at=2026-02-15T10:00:00Z, bidding_deadline=2026-02-06T10:00:00Z, execution_deadline=2026-02-13T09:00:00Z
- t-task4: created_at=2026-02-10T09:00:00Z, cancelled_at=2026-02-20T14:00:00Z, bidding_deadline=2026-02-11T09:00:00Z
- t-task5: created_at=2026-02-15T10:00:00Z, accepted_at=2026-02-16T11:00:00Z, disputed_at=2026-02-25T10:00:00Z, ruled_at=2026-03-01T12:00:00Z, worker_pct=70, ruling_id=rul-1, ruling_summary="Worker delivered 70% of requirements", dispute_reason="Incomplete deliverable", bidding_deadline=2026-02-16T10:00:00Z, execution_deadline=2026-02-23T11:00:00Z
- t-task6: created_at=2026-03-02T00:00:00Z, bidding_deadline=2026-03-02T06:22:02Z
- t-task7: created_at=2026-03-02T00:07:57Z, bidding_deadline=2026-03-02T06:30:00Z
- t-task8: created_at=2026-02-20T08:00:00Z, accepted_at=2026-02-21T10:00:00Z, submitted_at=2026-02-28T16:00:00Z, bidding_deadline=2026-02-21T08:00:00Z, execution_deadline=2026-02-28T10:00:00Z, review_deadline=2026-03-02T06:35:00Z
- t-task9: created_at=2026-02-25T09:00:00Z, bidding_deadline=2026-02-26T09:00:00Z
- t-task10: created_at=2026-01-25T10:00:00Z, expired_at=2026-02-01T10:00:00Z, bidding_deadline=2026-01-26T10:00:00Z
- t-task11: created_at=2026-02-28T10:00:00Z, accepted_at=2026-03-01T08:00:00Z, disputed_at=2026-03-02T06:34:00Z, bidding_deadline=2026-03-01T10:00:00Z, execution_deadline=2026-03-02T06:39:00Z
- t-task12: created_at=2026-01-20T10:00:00Z, bidding_deadline=2026-01-21T10:00:00Z

**12 bids:**

| bid_id | task_id | bidder_id | proposal | submitted_at |
|---|---|---|---|---|
| `bid-1` | `t-task1` | `a-bob` | I can build this login page | `2026-02-01T14:00:00Z` |
| `bid-2` | `t-task1` | `a-carol` | Login page expert here | `2026-02-01T15:00:00Z` |
| `bid-3` | `t-task2` | `a-carol` | Dashboard design proposal | `2026-02-02T14:00:00Z` |
| `bid-4` | `t-task2` | `a-dave` | I can design dashboards | `2026-02-02T16:00:00Z` |
| `bid-5` | `t-task3` | `a-bob` | API integration experience | `2026-02-05T14:00:00Z` |
| `bid-6` | `t-task3` | `a-eve` | Skilled in API work | `2026-02-05T15:00:00Z` |
| `bid-7` | `t-task5` | `a-carol` | Bug fix proposal | `2026-02-15T14:00:00Z` |
| `bid-8` | `t-task6` | `a-bob` | Mobile app developer | `2026-03-02T00:15:55Z` |
| `bid-9` | `t-task8` | `a-bob` | Email service builder | `2026-02-20T12:00:00Z` |
| `bid-10` | `t-task9` | `a-alice` | Search engine builder | `2026-02-25T14:00:00Z` |
| `bid-11` | `t-task11` | `a-eve` | Logging expert | `2026-02-28T14:00:00Z` |
| `bid-12` | `t-task9` | `a-bob` | Search engine specialist | `2026-02-25T15:00:00Z` |

**3 assets:**

| asset_id | task_id | uploader_id | filename | content_type | size_bytes | storage_path | uploaded_at |
|---|---|---|---|---|---|---|---|
| `asset-1` | `t-task1` | `a-bob` | login-page.zip | application/zip | 245760 | /data/assets/asset-1 | `2026-02-08T09:00:00Z` |
| `asset-2` | `t-task1` | `a-bob` | screenshot.png | image/png | 102400 | /data/assets/asset-2 | `2026-02-08T09:30:00Z` |
| `asset-3` | `t-task8` | `a-bob` | email-service.tar.gz | application/gzip | 512000 | /data/assets/asset-3 | `2026-02-28T15:00:00Z` |

**8 feedback records:**

| feedback_id | task_id | from_agent_id | to_agent_id | role | category | rating | comment | submitted_at | visible |
|---|---|---|---|---|---|---|---|---|---|
| `fb-1` | `t-task1` | `a-alice` | `a-bob` | poster | delivery_quality | extremely_satisfied | Excellent work | `2026-02-10T16:00:00Z` | 1 |
| `fb-2` | `t-task1` | `a-bob` | `a-alice` | worker | spec_quality | satisfied | Clear spec | `2026-02-10T16:30:00Z` | 1 |
| `fb-3` | `t-task3` | `a-alice` | `a-bob` | poster | delivery_quality | satisfied | Good job | `2026-02-15T11:00:00Z` | 1 |
| `fb-4` | `t-task3` | `a-bob` | `a-alice` | worker | spec_quality | extremely_satisfied | Very detailed spec | `2026-02-15T11:30:00Z` | 1 |
| `fb-5` | `t-task5` | `a-dave` | `a-carol` | poster | delivery_quality | dissatisfied | Incomplete work | `2026-03-01T13:00:00Z` | 1 |
| `fb-6` | `t-task5` | `a-carol` | `a-dave` | worker | spec_quality | dissatisfied | Vague requirements | `2026-03-01T13:30:00Z` | 1 |
| `fb-7` | `t-task2` | `a-alice` | `a-carol` | poster | delivery_quality | satisfied | Progress looks good | `2026-02-10T12:00:00Z` | 0 |
| `fb-8` | `t-task3` | `a-carol` | `a-bob` | poster | delivery_quality | satisfied | Decent work | `2026-02-15T12:00:00Z` | 0 |

**2 court claims:**

| claim_id | task_id | claimant_id | respondent_id | reason | status | filed_at |
|---|---|---|---|---|---|---|
| `clm-1` | `t-task5` | `a-dave` | `a-carol` | Incomplete deliverable | ruled | `2026-02-25T10:00:00Z` |
| `clm-2` | `t-task11` | `a-dave` | `a-eve` | Wrong implementation | filed | `2026-03-02T06:34:00Z` |

**2 rebuttals:**

| rebuttal_id | claim_id | agent_id | content | submitted_at |
|---|---|---|---|---|
| `reb-1` | `clm-1` | `a-carol` | The spec was ambiguous | `2026-02-26T10:00:00Z` |
| `reb-2` | `clm-2` | `a-eve` | I followed the spec exactly | `2026-03-02T06:36:00Z` |

**1 ruling:**

| ruling_id | claim_id | task_id | worker_pct | summary | judge_votes | ruled_at |
|---|---|---|---|---|---|---|
| `rul-1` | `clm-1` | `t-task5` | 70 | Worker delivered 70% of requirements | `[{"judge":"j1","vote":70},{"judge":"j2","vote":70},{"judge":"j3","vote":70}]` | `2026-03-01T12:00:00Z` |

**25 events:** (event_id is INTEGER PRIMARY KEY AUTOINCREMENT — insert with explicit IDs)

| event_id | event_source | event_type | timestamp | task_id | agent_id | summary | payload (as JSON string) |
|---|---|---|---|---|---|---|---|
| 1 | identity | agent.registered | 2026-01-15T10:00:00Z | NULL | a-alice | Alice registered | {"agent_name":"Alice"} |
| 2 | identity | agent.registered | 2026-01-16T11:00:00Z | NULL | a-bob | Bob registered | {"agent_name":"Bob"} |
| 3 | identity | agent.registered | 2026-01-17T12:00:00Z | NULL | a-carol | Carol registered | {"agent_name":"Carol"} |
| 4 | identity | agent.registered | 2026-01-18T13:00:00Z | NULL | a-dave | Dave registered | {"agent_name":"Dave"} |
| 5 | bank | salary.paid | 2026-01-20T10:00:00Z | NULL | a-alice | Alice received salary | {"amount":1000} |
| 6 | bank | salary.paid | 2026-01-20T10:00:00Z | NULL | a-bob | Bob received salary | {"amount":1000} |
| 7 | board | task.created | 2026-02-01T10:00:00Z | t-task1 | a-alice | Alice posted Build Login Page | {"title":"Build Login Page","reward":200} |
| 8 | board | bid.submitted | 2026-02-01T14:00:00Z | t-task1 | a-bob | Bob bid on Build Login Page | {"bid_id":"bid-1"} |
| 9 | board | bid.submitted | 2026-02-01T15:00:00Z | t-task1 | a-carol | Carol bid on Build Login Page | {"bid_id":"bid-2"} |
| 10 | board | task.accepted | 2026-02-03T12:00:00Z | t-task1 | a-alice | Alice accepted Bob for Build Login Page | {"worker_id":"a-bob","worker_name":"Bob"} |
| 11 | bank | escrow.locked | 2026-02-01T10:05:00Z | t-task1 | a-alice | Escrow locked 200 for Build Login Page | {"escrow_id":"esc-1","amount":200} |
| 12 | board | task.submitted | 2026-02-08T10:00:00Z | t-task1 | a-bob | Bob submitted Build Login Page | {"worker_name":"Bob","asset_count":2} |
| 13 | board | task.approved | 2026-02-10T15:00:00Z | t-task1 | a-alice | Alice approved Build Login Page | {"reward":200} |
| 14 | bank | escrow.released | 2026-02-10T15:00:00Z | t-task1 | a-alice | Escrow released 200 for Build Login Page | {"escrow_id":"esc-1","amount":200} |
| 15 | reputation | feedback.revealed | 2026-02-10T16:30:00Z | t-task1 | a-alice | Feedback revealed for Build Login Page | {"category":"delivery_quality"} |
| 16 | board | task.created | 2026-02-15T10:00:00Z | t-task5 | a-dave | Dave posted Fix Bug | {"title":"Fix Bug","reward":100} |
| 17 | board | task.disputed | 2026-02-25T10:00:00Z | t-task5 | a-dave | Dave disputed Fix Bug | {"reason":"Incomplete deliverable"} |
| 18 | court | claim.filed | 2026-02-25T10:00:00Z | t-task5 | a-dave | Dave filed claim on Fix Bug | {"claim_id":"clm-1"} |
| 19 | court | rebuttal.submitted | 2026-02-26T10:00:00Z | t-task5 | a-carol | Carol submitted rebuttal | {"claim_id":"clm-1"} |
| 20 | court | ruling.delivered | 2026-03-01T12:00:00Z | t-task5 | a-dave | Ruling delivered for Fix Bug | {"ruling_id":"rul-1","worker_pct":70} |
| 21 | board | task.ruled | 2026-03-01T12:00:00Z | t-task5 | a-dave | Fix Bug ruled: 70% to worker | {"worker_pct":70,"worker_id":"a-carol"} |
| 22 | identity | agent.registered | 2026-02-01T09:00:00Z | NULL | a-eve | Eve registered | {"agent_name":"Eve"} |
| 23 | board | task.created | 2026-03-02T00:00:00Z | t-task6 | a-alice | Alice posted Mobile App | {"title":"Mobile App","reward":150} |
| 24 | board | task.cancelled | 2026-02-20T14:00:00Z | t-task4 | a-eve | Eve cancelled Write Tests | {"title":"Write Tests"} |
| 25 | board | task.created | 2026-03-02T00:07:57Z | t-task7 | a-bob | Bob posted Data Pipeline | {"title":"Data Pipeline","reward":120} |

### Key Derived Values for Assertions

- **GDP total** = SUM(reward) for approved tasks + SUM(reward * worker_pct / 100) for ruled tasks = (200 + 80) + (100 * 70 / 100) = 280 + 70 = **350**
- **Escrow locked** = SUM(amount) WHERE status='locked' = 100 + 150 = **250**
- **Tasks open** = 4 (t-task6, t-task7, t-task9, t-task12)
- **Tasks in execution** = 2 (accepted: t-task2, submitted: t-task8)
- **Tasks disputed+ruled** = 2 (disputed: t-task11, ruled: t-task5)
- **Tasks completed_all_time** = 2 approved (t-task1, t-task3)
- **Total tasks** = 12
- **Total agents** = 5
- **Max event_id** = 25
- **Bob total_earned** (escrow_release) = 200
- **Alice total_spent** (escrow_lock) = 200 + 100 + 150 = 450
- **Alice balance** = 800
- **Visible spec_quality feedback**: fb-2 (satisfied, to Alice), fb-4 (extremely_satisfied, to Alice), fb-6 (dissatisfied, to Dave) → 3 total
- **Visible delivery_quality feedback**: fb-1 (extremely_satisfied, to Bob), fb-3 (satisfied, to Bob), fb-5 (dissatisfied, to Carol) → 3 total
- **Bob tasks_completed_as_worker** (status='approved' AND worker_id='a-bob') = 2 (t-task1, t-task3)
- **Alice tasks_posted** = tasks where poster_id='a-alice' = 4 (t-task1, t-task2, t-task3, t-task6)

## Phase 2: Create conftest.py

```python
"""Integration test fixtures with seeded SQLite database."""

import json
import os
import sqlite3
from pathlib import Path

import aiosqlite
import pytest
from httpx import ASGITransport, AsyncClient

from ui_service.app import create_app
from ui_service.config import clear_settings_cache
from ui_service.core.lifespan import lifespan
from ui_service.core.state import reset_app_state

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"


@pytest.fixture
def db_path(tmp_path):
    """Create and seed a SQLite database, return its path."""
    from tests.integration.helpers import insert_seed_data

    db_file = tmp_path / "economy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(SCHEMA_PATH.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    insert_seed_data(conn)
    conn.close()
    return db_file


@pytest.fixture
async def app(db_path, tmp_path):
    """Create test app pointing at the seeded database."""
    web_dir = tmp_path / "web"
    web_dir.mkdir(exist_ok=True)
    (web_dir / "index.html").write_text("<html><body>Test</body></html>")

    config_content = f"""\
service:
  name: "ui"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8008
  log_level: "info"
logging:
  level: "WARNING"
  directory: "{tmp_path / 'logs'}"
database:
  path: "{db_path}"
sse:
  poll_interval_seconds: 1
  keepalive_interval_seconds: 15
  batch_size: 50
frontend:
  web_root: "{web_dir}"
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def client(app):
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def write_db(db_path):
    """Writable connection to the same DB for staleness tests."""
    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    await conn.close()
```

## Phase 3: Create test files

Create each test file with the tests listed below. Every test function must be `async def` and marked with `@pytest.mark.integration`.

### test_health.py (12 tests)

1. `test_health_returns_200_with_status_ok` — GET /health returns 200, status="ok"
2. `test_health_has_uptime_seconds` — uptime_seconds is a positive number
3. `test_health_has_started_at_iso` — started_at is a non-empty string
4. `test_health_latest_event_id_matches_seed` — latest_event_id == 25
5. `test_health_database_readable_true` — database_readable is True
6. `test_health_post_not_allowed` — POST /health returns 405
7. `test_health_response_has_exact_keys` — keys are exactly {status, uptime_seconds, started_at, latest_event_id, database_readable}
8. `test_health_uptime_increases_between_calls` — second call has >= uptime than first
9. `test_health_types_correct` — uptime is float/int, started_at is str, latest_event_id is int, database_readable is bool
10. `test_health_no_db_returns_readable_false` — Create a separate app fixture with nonexistent DB path, assert database_readable=False (use a separate inline fixture or parametrize)
11. `test_health_latest_event_id_updates_on_insert` — **STALENESS**: insert event_id=26 via write_db, re-query /health, assert latest_event_id=26
12. `test_health_latest_event_id_zero_when_no_events` — **STALENESS**: DELETE FROM events via write_db, re-query, assert latest_event_id=0

### test_agents.py (25 tests)

**List agents:**
1. `test_list_returns_all_agents` — total_count=5
2. `test_list_default_sort_total_earned_desc` — first agent is Bob (earned=200)
3. `test_list_sort_by_total_spent` — sort_by=total_spent, first is Alice (spent=450)
4. `test_list_sort_by_tasks_posted` — sort_by=tasks_posted, first is Alice (4 posted)
5. `test_list_sort_by_tasks_completed` — sort_by=tasks_completed, first is Bob (2 completed)
6. `test_list_sort_by_spec_quality` — sort_by=spec_quality
7. `test_list_sort_by_delivery_quality` — sort_by=delivery_quality
8. `test_list_sort_asc_reverses` — order=asc, last agent should be the one that's first in desc
9. `test_list_pagination_limit_offset` — limit=2&offset=2 returns 2 agents, total_count still 5
10. `test_list_invalid_sort_returns_400` — sort_by=invalid returns 400
11. `test_list_agent_stats_structure` — each agent has stats with all expected keys

**Agent profile:**
12. `test_profile_returns_correct_agent` — GET /api/agents/a-alice returns name="Alice"
13. `test_profile_not_found_returns_404` — GET /api/agents/a-nonexistent returns 404
14. `test_profile_balance_matches_seed` — Alice balance=800
15. `test_profile_recent_tasks_included` — Alice has recent_tasks list (she's poster on 4 tasks)
16. `test_profile_recent_tasks_role_field` — tasks have role "poster" or "worker"
17. `test_profile_feedback_excludes_invisible` — Bob's feedback does NOT include fb-8 (visible=0)
18. `test_profile_balance_staleness` — **STALENESS**: update a-alice balance to 999, re-query, assert 999

**Agent feed:**
19. `test_feed_returns_agent_events` — GET /api/agents/a-alice/feed returns events involving Alice
20. `test_feed_pagination_before` — before param works for pagination
21. `test_feed_has_more_flag` — limit=1 returns has_more=true

**Agent earnings:**
22. `test_earnings_total_and_avg` — Bob total_earned=200, tasks_approved=2, avg_per_task=100
23. `test_earnings_cumulative_data_points` — data_points list has ascending cumulative values
24. `test_earnings_no_earnings_agent` — Dave has total_earned=0
25. `test_earnings_staleness` — **STALENESS**: insert escrow_release for Bob, re-query, total_earned increases

### test_metrics.py (22 tests)

1. `test_gdp_total` — gdp.total == 350
2. `test_gdp_per_agent` — gdp.per_agent is a float
3. `test_gdp_rate_per_hour` — gdp.rate_per_hour is a float >= 0
4. `test_gdp_staleness` — **STALENESS**: insert new approved task with reward=100, GDP increases to 450
5. `test_agents_total_registered` — agents.total_registered == 5
6. `test_agents_with_completed_tasks` — agents.with_completed_tasks >= 1
7. `test_agents_active` — agents.active is an integer >= 0
8. `test_tasks_total_created` — tasks.total_created == 12
9. `test_tasks_completed_all_time` — tasks.completed_all_time == 2
10. `test_tasks_open` — tasks.open == 4
11. `test_tasks_in_execution` — tasks.in_execution == 2
12. `test_tasks_disputed` — tasks.disputed == 2
13. `test_tasks_completion_rate` — tasks.completion_rate is a float between 0 and 1
14. `test_tasks_staleness` — **STALENESS**: insert new open task, tasks.open increases to 5
15. `test_escrow_total_locked` — escrow.total_locked == 250
16. `test_escrow_staleness` — **STALENESS**: insert new locked escrow, total_locked increases
17. `test_spec_quality_avg_and_breakdown` — spec_quality has avg_score, pct fields as floats
18. `test_spec_quality_trend` — spec_quality has trend_direction and trend_delta
19. `test_labor_avg_bids_and_reward` — labor_market.avg_bids_per_task > 0, avg_reward > 0
20. `test_labor_reward_distribution_buckets` — reward_distribution has all 4 bucket keys
21. `test_gdp_history_valid_params` — GET /api/metrics/gdp/history?window=24h&resolution=1h returns data_points
22. `test_gdp_history_invalid_window_returns_400` — window=2h returns 400

### test_tasks.py (19 tests)

**Competitive:**
1. `test_competitive_only_tasks_with_bids` — all returned tasks have bid_count > 0
2. `test_competitive_sorted_by_bid_count_desc` — first task has highest bid_count
3. `test_competitive_default_status_open` — tasks are open or accepted status
4. `test_competitive_limit` — limit=2 returns at most 2
5. `test_competitive_includes_poster_info` — each task has poster.agent_id and poster.name
6. `test_competitive_staleness_new_bid` — **STALENESS**: insert bid on t-task7, it now appears in competitive

**Uncontested:**
7. `test_uncontested_open_tasks_without_bids` — returned tasks have no bids (t-task7, t-task9, t-task12 — but only if old enough)
8. `test_uncontested_min_age_filter` — min_age_minutes=0 includes recent tasks
9. `test_uncontested_limit` — limit=1 returns 1 task
10. `test_uncontested_has_minutes_field` — each task has minutes_without_bids > 0
11. `test_uncontested_staleness_bid_removes_task` — **STALENESS**: insert bid on uncontested task, it disappears

**Drilldown:**
12. `test_drilldown_approved_task_full` — GET /api/tasks/t-task1 returns poster, worker, bids (2), assets (2), feedback
13. `test_drilldown_not_found_returns_404` — GET /api/tasks/t-nonexistent returns 404
14. `test_drilldown_bids_with_delivery_quality` — each bid's bidder has delivery_quality stats
15. `test_drilldown_accepted_bid_flag` — bid-1 has accepted=true, bid-2 has accepted=false
16. `test_drilldown_disputed_task_has_dispute` — GET /api/tasks/t-task5 has dispute with claim, rebuttal, ruling
17. `test_drilldown_non_disputed_has_null_dispute` — GET /api/tasks/t-task1 has dispute=null
18. `test_drilldown_feedback_visible_only` — drilldown only shows visible feedback
19. `test_drilldown_staleness_new_bid` — **STALENESS**: insert new bid on t-task1, bids count increases

### test_events.py (22 tests)

1. `test_events_returns_reverse_chronological` — event_ids are descending
2. `test_events_default_returns_all_25` — default returns all 25 events (limit 50 > 25)
3. `test_events_limit_param` — limit=5 returns exactly 5
4. `test_events_limit_clamped_to_200` — limit=999 returns at most 200 (or all 25)
5. `test_events_before_cursor` — before=15 returns events with event_id < 15
6. `test_events_after_cursor` — after=20 returns events with event_id > 20
7. `test_events_before_and_after_range` — before=20&after=10 returns events in range
8. `test_events_filter_by_source` — source=identity returns only identity events (IDs 1,2,3,4,22)
9. `test_events_filter_by_type` — type=task.created returns only task.created events (IDs 7,16,23,25)
10. `test_events_filter_by_agent_id` — agent_id=a-alice returns Alice's events
11. `test_events_filter_by_task_id` — task_id=t-task1 returns task1's events (IDs 7,8,9,10,11,12,13,14,15)
12. `test_events_has_more_true` — limit=5 with 25 events → has_more=true
13. `test_events_has_more_false` — limit=50 with 25 events → has_more=false
14. `test_events_oldest_newest_ids` — oldest_event_id and newest_event_id match first/last in list
15. `test_events_invalid_limit_returns_400` — limit=abc returns 400
16. `test_events_invalid_before_returns_400` — before=abc returns 400
17. `test_events_limit_zero_returns_400` — limit=0 returns 400
18. `test_events_payload_is_dict` — each event's payload is a dict, not a string
19. `test_events_staleness_new_event` — **STALENESS**: insert event_id=26, it appears as newest
20. `test_sse_content_type` — GET /api/events/stream returns text/event-stream content-type
21. `test_sse_sends_existing_events` — SSE stream with last_event_id=24 sends event 25
22. `test_sse_retry_directive` — SSE stream starts with retry directive

### test_quarterly.py (17 tests)

1. `test_quarterly_explicit_2026_q1` — quarter=2026-Q1 returns 200
2. `test_quarterly_default_current_quarter` — no quarter param returns a report
3. `test_quarterly_invalid_format_returns_400` — quarter=2026-Q5 returns 400
4. `test_quarterly_invalid_year_returns_400` — quarter=ABCD-Q1 returns 400
5. `test_quarterly_no_data_returns_404` — quarter=2020-Q1 returns 404
6. `test_quarterly_gdp_total` — gdp.total == 350
7. `test_quarterly_gdp_previous_quarter_zero` — gdp.previous_quarter == 0
8. `test_quarterly_period_boundaries` — period.start is quarter start and period.end is quarter end
9. `test_quarterly_tasks_posted` — tasks.posted == 12
10. `test_quarterly_tasks_completed` — tasks.completed == 2
11. `test_quarterly_tasks_disputed` — tasks.disputed >= 1
12. `test_quarterly_labor_avg_bids` — labor_market.avg_bids_per_task > 0
13. `test_quarterly_agents_registrations` — agents.new_registrations == 5
14. `test_quarterly_agents_total_at_end` — agents.total_at_quarter_end == 5
15. `test_quarterly_notable_highest_value_task` — notable.highest_value_task.reward == 300 (t-task9)
16. `test_quarterly_notable_top_workers` — notable.top_workers is a list
17. `test_quarterly_staleness_new_approved_task` — **STALENESS**: insert approved task, GDP increases on re-query

## Phase 4: Verification

Run from `services/ui/`:

```bash
just ci-quiet
```

All 123+ tests must pass along with formatting, linting, type checking, and security scanning.

If `just ci-quiet` does not run integration tests by default (only unit), try:

```bash
cd services/ui && uv run pytest tests/integration/ -v
```

Then also run `just ci-quiet` to ensure no lint/format/type issues.
