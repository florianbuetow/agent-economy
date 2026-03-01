# Phase 5 — Judge System

## Working Directory

```
services/court/
```

All files in this phase live under `src/court_service/judges/`. The judge system is self-contained — it has no FastAPI imports and no knowledge of HTTP or routers.

---

## File 1: `src/court_service/judges/base.py`

Defines the `JudgeVote` data structure and the `Judge` abstract base class.

### JudgeVote Dataclass

```python
@dataclass
class JudgeVote:
    judge_id: str        # e.g., "judge-0"
    worker_pct: int      # 0–100
    reasoning: str       # Written justification
    voted_at: str        # ISO 8601 timestamp
```

### DisputeContext Dataclass

All the information a judge needs to evaluate a dispute:

```python
@dataclass
class DisputeContext:
    task_spec: str           # Original task specification
    deliverables: list[str]  # Deliverable filenames/metadata
    claim: str               # Poster's rejection reason
    rebuttal: str | None     # Worker's response (null if not submitted)
    task_title: str          # Task title
    reward: int              # Task reward amount
```

### Judge ABC

```python
from abc import ABC, abstractmethod

class Judge(ABC):
    @abstractmethod
    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        """Evaluate a dispute and return a vote."""
        ...
```

The ABC is async because LLM calls are I/O-bound.

---

## File 2: `src/court_service/judges/prompts.py`

Contains the system prompt and evaluation prompt template as string constants. No logic.

### System Prompt

Define a `SYSTEM_PROMPT` constant that instructs the judge:
- You are an impartial dispute resolution judge
- You evaluate whether a worker fulfilled a task specification
- Ambiguity in the specification favors the worker (core economic principle)
- Return a `worker_pct` (0–100) and written reasoning
- 100 = worker fully delivered; 0 = worker delivered nothing of value
- Consider the spec, deliverables, claim, and rebuttal

### Evaluation Prompt Template

Define an `EVALUATION_TEMPLATE` string with placeholders for all `DisputeContext` fields:

```
Task Title: {task_title}
Task Reward: {reward}

=== SPECIFICATION ===
{task_spec}

=== DELIVERABLES ===
{deliverables}

=== CLAIM (Poster's rejection reason) ===
{claim}

=== REBUTTAL (Worker's response) ===
{rebuttal}

Based on the specification and deliverables, determine what percentage (0-100)
of the reward the worker should receive. Respond with EXACTLY this JSON format:
{{"worker_pct": <integer 0-100>, "reasoning": "<your explanation>"}}
```

### Design Notes

- The prompt asks for JSON output — the LLM judge will parse it
- `rebuttal` may be `"No rebuttal submitted"` if null — format it in the template, not the prompt constant
- Keep the prompts concise — LLM token usage matters for cost

---

## File 3: `src/court_service/judges/llm_judge.py`

LiteLLM-based implementation of the `Judge` ABC.

### Constructor

```python
class LLMJudge(Judge):
    def __init__(self, judge_id: str, model: str, temperature: float) -> None:
        self._judge_id = judge_id
        self._model = model
        self._temperature = temperature
```

### Method: `evaluate(context: DisputeContext) -> JudgeVote`

1. Format the evaluation prompt from `EVALUATION_TEMPLATE` using `context` fields
2. Call `litellm.acompletion()`:
   ```python
   response = await litellm.acompletion(
       model=self._model,
       messages=[
           {"role": "system", "content": SYSTEM_PROMPT},
           {"role": "user", "content": formatted_prompt},
       ],
       temperature=self._temperature,
       response_format={"type": "json_object"},
   )
   ```
3. Parse the JSON response to extract `worker_pct` and `reasoning`
4. Validate `worker_pct` is an integer in [0, 100]
5. Return `JudgeVote(judge_id=self._judge_id, worker_pct=..., reasoning=..., voted_at=now_utc_iso())`

### Error Handling

Any failure — LiteLLM exception, JSON parse error, validation error — should raise `ServiceError("JUDGE_UNAVAILABLE", ..., 502)`. The message should include the judge ID for debugging but NOT leak the raw LLM response.

### Key Design Notes

- Uses `litellm.acompletion` (async) — supports any LiteLLM-compatible provider
- `response_format={"type": "json_object"}` asks for structured JSON output (supported by most modern models)
- If the model returns invalid JSON or out-of-range values, that's a `JUDGE_UNAVAILABLE` error
- Temperature comes from config per judge — different judges can use different models/temperatures

---

## File 4: `src/court_service/judges/__init__.py`

Export the public interface:

```python
from court_service.judges.base import DisputeContext, Judge, JudgeVote
from court_service.judges.llm_judge import LLMJudge

__all__ = ["DisputeContext", "Judge", "JudgeVote", "LLMJudge"]
```

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from court_service.judges import LLMJudge, DisputeContext, JudgeVote; print('OK')"
```
