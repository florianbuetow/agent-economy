# Delegating Work to Sub-Agents via tmux

## Session Setup
- The sub-agent runs in a tmux session named `codex`
- It runs in the project working directory
- The agent is highly capable and can handle complex, long-running tasks given precise instructions
- Do NOT underestimate it — delegate aggressively, not conservatively

## Understanding the Agent's Behavior
- Treat the agent like it is extremely literal-minded: it follows instructions with exact precision, no more, no less
- It will work tirelessly for hours on a task without complaint — give it the full scope, not just a small piece
- It only stops when your instructions are ambiguous or insufficient for it to continue
- If it stops or asks a question, that means YOUR instructions were unclear — fix the instructions, not the agent
- Ambiguity is the enemy: every detail you leave unspecified is a potential point where the agent will halt or guess wrong
- Give it zero-ambiguity instructions with complete context and it will deliver
- The agent excels at execution — writing code, running tests, committing — but not at planning or design decisions
- YOU are the planner and architect; the agent is the executor. Plan thoroughly, then delegate the full implementation

## Sending Messages

### The Golden Rule: Always Send Enter Separately
```bash
# Step 1: Send the text
tmux send-keys -t codex "Your message here"
# Step 2: ALWAYS send Enter as a SEPARATE Bash tool call
tmux send-keys -t codex Enter
```

**NEVER** chain Enter in the same command with `&&`. It gets lost.
**NEVER** include Enter as part of the send-keys text argument.
**ALWAYS** make Enter its own standalone Bash tool call.

### Cancelling a Prompt First
When the agent is waiting at a Yes/No prompt:
```bash
# Call 1: Cancel the prompt
tmux send-keys -t codex Escape
# Call 2: Wait for it to process (separate Bash call)
sleep 2
# Call 3: Send your new message
tmux send-keys -t codex "Your correction here"
# Call 4: Submit it
tmux send-keys -t codex Enter
```

### Reading Agent Output
```bash
# See current screen
tmux capture-pane -t codex -p | tail -50

# Don't sleep+check in one command if user might interrupt
# Instead, check immediately or use short sleeps
```

## Priming the Agent

### After /clear or Starting a New Task
When starting fresh (after `/clear` or a new session), the agent has no project context. Always prime it:
1. Tell it to read `AGENTS.md` first — this contains project conventions, architecture, testing rules
2. List the specific files relevant to the task it should read
3. Tell it to use `uv run` for all Python execution — never raw `python` or `pip install`
4. Tell it not to modify existing test files in `tests/`
5. Only THEN give the task instructions

### Example Priming Message
```
Read these files FIRST before doing anything:
1. AGENTS.md — project conventions, architecture, testing rules
2. docs/service-implementation-guide.md — how to implement a service from scaffolding
3. docs/specifications/<service>-specs.md — the API specification for the service
4. <file> — <why it's relevant>

After reading all files, implement the following fix/feature.
<detailed instructions>

Use `uv run` for all Python execution — never use raw python, python3, or pip install.
Do NOT modify any existing test files in tests/.
```

## Giving Instructions

### Be Explicit and Comprehensive
The agent works best with:
- Exact file paths to read first
- Exact commands to run for verification
- Clear sequence of tasks
- What NOT to do (e.g., "Do not use pip install, use uv sync")
- Enforce TDD: "Write tests first, verify they fail, then implement"
- Tell it existing tests are acceptance tests that must not be modified
- Tell it to add new test files instead of modifying existing ones

### First Message Template
```
Read <spec-file> FIRST before doing anything. It is your single source of truth.
Follow it to the letter. Do not improvise. Do not deviate.
Execute Phase 1 through Phase N in order.
After EACH phase, run: just test (from the service directory)
After ALL phases, run: just ci (from the service directory)
Commit after each phase as specified.
Use `uv run` for all Python execution — never use raw python or pip install.
Do NOT modify existing test files in tests/ — add new test files instead.
If you cannot run a command due to sandbox restrictions, tell me and I will run it for you.
Important files to read: (1) ... (2) ... (3) ...
START NOW by reading the spec file.
```

### Correcting the Agent Mid-Task
- Cancel current prompt with Escape first
- Be direct: "STOP. Do X instead of Y."
- Tell it what went wrong and what to do differently

## Monitoring the Agent

### Automatic Periodic Checks — MANDATORY
After sending instructions to the agent, you MUST automatically check on it periodically. Never ask the user to check — do it yourself.

**Polling schedule:**
- **First 30 seconds:** Check every 10 seconds. The agent often hits permission prompts for new tools/commands right away. Every second you waste waiting is time the agent sits idle.
- **After first minute:** Check every 30 seconds.
- **After agent is clearly in a working rhythm** (no prompts, actively writing/running): Check every 60 seconds.

**Why check quickly at first:** When the agent runs a tool or command for the first time (e.g., `just init`, `uv run pytest`, a new script), it will likely prompt for permission. If you wait 2 minutes to check, that's 2 minutes of wasted idle time. Check back fast, approve or answer, then let it continue.

**What to look for on each check:**
- Is it waiting at a Yes/No permission prompt? → Answer it immediately
- Is it stuck or asking a question? → Provide the answer or clarify instructions
- Is it actively working (spinner, "Working")? → Leave it alone, check again later
- Has it finished? → Review the output and proceed

**How to check:**
```bash
tmux capture-pane -t codex -p | tail -50
```

Never use `sleep` before checking — just check immediately. If the agent is still working, check again on your next turn.

---

## Agent Limitations

### What It Cannot Do
- Run tmux-based TUI tests (sandbox restriction on tmux)
- Access authenticated services
- Run Docker commands (may be restricted)

### What I Must Handle
- All ticket operations (create, close, sync)
- Running integration tests that require services to be running
- Final CI validation (`just ci-quiet` from root) — ALWAYS run this, never skip

### When It Gets Stuck
- Permission errors on files → tell it to create new files instead of modifying
- Sandbox restrictions → offer to run commands for it and report results
- Missing tools → tell it to skip and you'll handle it

## Review Protocol

### ALWAYS Validate with CI
After the agent finishes ALL work, ALWAYS run from the service directory:
```bash
just ci-quiet
```
This is the definitive validation. Do NOT rely on `just test` alone — it misses:
- Format checks (ruff format)
- Lint checks (ruff check)
- Type checking (mypy, pyright)
- Security scanning (bandit)
- Spell checking (codespell)
- Custom rules (semgrep)

If `just ci-quiet` fails, investigate the failure. Common causes:
- Import ordering or formatting issues
- Type annotation missing or incorrect
- New dependencies not added to pyproject.toml
- Spelling errors in docstrings or variable names

### After Each Phase
- Check git log to verify commits
- Read the code it produced
- Run `just ci-quiet` (not just `just test`)
- Only close tickets after CI passes

### Quality Checks
- Does the code match the spec?
- Architecture rules respected? (no forbidden imports, business logic in services/ not routers/)
- Does `just ci-quiet` pass? (the ONLY check that matters)
- No default parameter values in configurable settings?
- Config loaded from config.yaml, not hardcoded?
- Pydantic models use `ConfigDict(extra="forbid")`?

## Anti-Patterns (Mistakes to Avoid)

1. **Being too conservative** — The agent can handle complex work. Delegate the full feature, not just the foundation.
2. **Guessing test outcomes** — Never claim a test will pass or fail without running it. Run `just ci-quiet` to verify.
3. **Forgetting Enter** — Always send Enter as a separate Bash call.
4. **Not enforcing TDD** — Explicitly tell the agent to write tests first, verify they fail, then implement.
5. **Not telling it about existing test files** — Tell it existing tests are acceptance tests that must not be modified.
6. **Running `just test` instead of `just ci-quiet`** — `just test` only covers pytest. `just ci-quiet` is the full CI pipeline including format, lint, type checks, security, and tests. Always use `just ci-quiet` for final validation.
7. **Not priming with AGENTS.md** — The agent needs project context. Always make it read AGENTS.md first.
8. **Letting it use pip install** — All Python execution must go through `uv run`. All dependency management through `uv sync`.
