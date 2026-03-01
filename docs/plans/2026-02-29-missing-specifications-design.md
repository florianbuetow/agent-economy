# Missing Specifications Design — Court Service + Central Bank Formal Specs

## Date: 2026-02-28

## Scope

Two services need formal specifications written:

1. **Court Service (port 8005)** — New service, not yet implemented. Needs API spec, auth spec, and test spec from scratch.
2. **Central Bank Service (port 8002)** — Fully implemented. Needs retroactive formal API spec and test spec to match the standard format used by Identity, Task Board, and Reputation.

---

## Deliverables

### Court Service

| Document | Path | Description |
|----------|------|-------------|
| API Spec | `docs/specifications/service-api/court-service-specs.md` | Endpoints, data model, lifecycle, error codes |
| Auth Spec | `docs/specifications/service-api/court-service-auth-specs.md` | JWS authentication model |
| Test Spec | `docs/specifications/service-tests/court-service-tests.md` | Release-gate test cases |
| Auth Test Spec | `docs/specifications/service-tests/court-service-auth-tests.md` | Authentication test cases |

### Central Bank Service

| Document | Path | Description |
|----------|------|-------------|
| API Spec | `docs/specifications/service-api/central-bank-service-specs.md` | Formal spec derived from existing implementation |
| Auth Spec | `docs/specifications/service-api/central-bank-service-auth-specs.md` | JWS authentication model |
| Test Spec | `docs/specifications/service-tests/central-bank-service-tests.md` | Formal test spec derived from existing tests |
| Auth Test Spec | `docs/specifications/service-tests/central-bank-service-auth-tests.md` | Authentication test cases |

---

## Court Service Design

### Purpose

The Court is the dispute resolution engine. When a poster rejects a deliverable, the Court evaluates the specification, deliverables, claim, and rebuttal through an LLM judge panel and issues a proportional payout ruling.

### Core Principles

- **Ambiguity favors the worker** — the fundamental economic incentive. If a spec is vague, the judge rules in the worker's favor.
- **Configurable odd-numbered panel** — panel size must be odd (1, 3, 5...). Start with 1 judge. Architecture supports easy addition of more judges.
- **Every judge must vote** — no abstentions. Each vote is a percentage (0-100%) plus written reasoning.
- **Court executes side-effects** — after ruling, Court calls Central Bank to split escrow and Reputation to record scores.
- **Platform-signed requests only** — Task Board orchestrates disputes on behalf of agents.
- **SQLite persistence** — same pattern as Identity and Central Bank. Full audit trail.

### Service Dependencies

```
Court (port 8005)
  ├── Identity (8001) — JWS token verification
  ├── Task Board (8003) — fetch task data (spec, deliverables, status)
  ├── Central Bank (8002) — split escrow based on ruling
  └── Reputation (8004) — record feedback scores
```

### Data Model

#### Dispute

| Field | Type | Description |
|-------|------|-------------|
| `dispute_id` | string | `disp-<uuid4>` |
| `task_id` | string | Task under dispute |
| `claimant_id` | string | Poster's agent ID |
| `respondent_id` | string | Worker's agent ID |
| `claim` | string | Poster's claim (reason for rejection, 1-10,000 chars) |
| `rebuttal` | string? | Worker's rebuttal (null until submitted) |
| `status` | string | `filed` → `rebuttal_pending` → `judging` → `ruled` |
| `rebuttal_deadline` | datetime | When rebuttal window expires |
| `worker_pct` | integer? | Final ruling: 0-100% to worker (null until ruled) |
| `ruling_summary` | string? | Aggregated reasoning (null until ruled) |
| `escrow_id` | string | Central Bank escrow ID for this task |
| `filed_at` | datetime | When claim was filed |
| `rebutted_at` | datetime? | When rebuttal was submitted |
| `ruled_at` | datetime? | When ruling was issued |

#### JudgeVote

| Field | Type | Description |
|-------|------|-------------|
| `vote_id` | string | `vote-<uuid4>` |
| `dispute_id` | string | FK to dispute |
| `judge_id` | string | Judge identifier (e.g., `judge-0`) |
| `worker_pct` | integer | This judge's percentage to worker (0-100) |
| `reasoning` | string | This judge's written reasoning |
| `voted_at` | datetime | When this vote was cast |

#### Ruling Aggregation

Final `worker_pct` = median of all judge votes. With 1 judge, the median is the single vote.

### Dispute Lifecycle

```
1. FILED              → Platform files claim (poster_id, worker_id, task_id, claim text)
                        Court fetches task data from Task Board
                        Status set to rebuttal_pending
2. REBUTTAL_PENDING   → Worker has configurable window to submit rebuttal via platform
3. JUDGING            → After rebuttal submission or window expiry:
                        - Each judge evaluates spec + deliverables + claim + rebuttal
                        - Each judge casts a percentage vote + reasoning
                        - All judges must vote (no abstentions)
4. RULED              → Median percentage calculated
                        → Central Bank splits escrow (worker_pct% to worker, rest to poster)
                        → Reputation records spec quality (poster) and delivery quality (worker)
                        → Task Board updated with ruling details
```

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/disputes/file` | Platform JWS (body) | File a new dispute |
| POST | `/disputes/{dispute_id}/rebuttal` | Platform JWS (body) | Submit worker's rebuttal |
| POST | `/disputes/{dispute_id}/rule` | Platform JWS (body) | Trigger judging (or auto after rebuttal/expiry) |
| GET | `/disputes/{dispute_id}` | None | Get dispute details + votes + ruling |
| GET | `/disputes` | None | List disputes (filterable by task_id, status) |
| GET | `/health` | None | Health check |

### Authentication Model

Platform-signed JWS tokens only. The Task Board acts as the orchestrator — it files claims on behalf of posters and submits rebuttals on behalf of workers. The Court never interacts with agents directly.

### Judge Architecture

```
src/court_service/
  judges/
    __init__.py
    base.py          # Abstract Judge interface (JudgeVote dataclass, Judge ABC)
    prompts.py       # System prompts and prompt templates
    llm_judge.py     # LiteLLM-based judge implementation
```

- LiteLLM as the LLM provider for maximum flexibility
- Judge configuration in `config.yaml` (model, temperature per judge)
- Prompts co-located with judge implementation in code files
- Panel size validated at startup (must be odd, >= 1)

### Configuration

```yaml
service:
  name: "court"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8005
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "data/court.db"

identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"

task_board:
  base_url: "http://localhost:8003"

central_bank:
  base_url: "http://localhost:8002"

reputation:
  base_url: "http://localhost:8004"

platform:
  agent_id: ""

disputes:
  rebuttal_deadline_seconds: 86400  # 24 hours

judges:
  panel_size: 1
  judges:
    - id: "judge-0"
      model: "gpt-4o"
      temperature: 0.3

request:
  max_body_size: 1048576
```

### Error Codes

| Status | Code | When |
|--------|------|------|
| 400 | `INVALID_JWS` | Malformed/missing JWS token |
| 400 | `INVALID_JSON` | Malformed request body |
| 400 | `INVALID_PAYLOAD` | Missing required fields in JWS payload |
| 400 | `INVALID_PANEL_SIZE` | Panel size is even or < 1 |
| 403 | `FORBIDDEN` | Non-platform signer |
| 404 | `DISPUTE_NOT_FOUND` | No dispute with this ID |
| 404 | `TASK_NOT_FOUND` | Task doesn't exist in Task Board |
| 409 | `DISPUTE_ALREADY_EXISTS` | Dispute already filed for this task |
| 409 | `DISPUTE_ALREADY_RULED` | Dispute already has a ruling |
| 409 | `REBUTTAL_ALREADY_SUBMITTED` | Worker already submitted rebuttal |
| 409 | `INVALID_DISPUTE_STATUS` | Operation not valid for current status |
| 502 | `IDENTITY_SERVICE_UNAVAILABLE` | Can't reach Identity service |
| 502 | `TASK_BOARD_UNAVAILABLE` | Can't reach Task Board |
| 502 | `CENTRAL_BANK_UNAVAILABLE` | Can't reach Central Bank |
| 502 | `REPUTATION_SERVICE_UNAVAILABLE` | Can't reach Reputation service |
| 502 | `JUDGE_UNAVAILABLE` | LLM provider returned an error |

### Side-Effects on Ruling

After the judge panel votes:
1. Calculate median `worker_pct` from all judge votes
2. Call Central Bank: `POST /escrow/{escrow_id}/split` with `worker_pct`
3. Call Reputation: `POST /feedback` for both poster (spec quality) and worker (delivery quality)
4. Update Task Board: record ruling on the task (ruling_id, worker_pct, ruling_summary)
5. Update dispute record: status → `ruled`, `ruled_at`, `worker_pct`, `ruling_summary`

---

## Central Bank Formal Specification

The Central Bank is already fully implemented. The formal specs will be derived directly from:
- The existing design doc (`docs/specifications/service-specs/central-bank-design.md`)
- The actual implementation code
- The existing test suite

No new design decisions needed — this is purely formatting the existing behavior into the standard spec format matching Identity, Task Board, and Reputation services.

### Documents to Create

1. **API Spec** — endpoints, data model, error codes, interaction patterns (matching identity-service-specs.md format)
2. **Auth Spec** — JWS authentication model, two-tier operations (matching reputation-service-auth-specs.md format)
3. **Test Spec** — release-gate test cases with test IDs (matching identity-service-tests.md format)
4. **Auth Test Spec** — authentication-specific test cases (matching reputation-service-auth-tests.md format)

---

## Approach

Write all 8 specification documents following the established format patterns:
- API specs follow the identity-service-specs.md structure (Purpose → Core Principles → Data Model → Endpoints → Error Format → What This Service Does NOT Do → Interaction Patterns)
- Auth specs follow the reputation-service-auth-specs.md structure (Authentication Model → Authentication Flow → Error Codes)
- Test specs follow the identity-service-tests.md structure (Error Contract → Test Data Conventions → Categorized test cases with IDs → Coverage Summary)
