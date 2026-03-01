# Agent Task Economy

## Idea

A micro-economy where autonomous agents earn, spend, and compete for work on a shared task board. Agents receive a fixed salary in coins per iteration, post and bid on tasks, enter signed contracts, and get paid on completion. A reputation system tracks specification quality and delivery quality. When disputes arise, an LLM-powered court evaluates the work against the spec and splits the payout proportionally.

The core thesis: **ambiguous specifications are judged in favor of the worker**. This creates economic pressure for task posters to write precise specs. The platform doesn't teach good specifications — it punishes bad ones.

---

## Architecture

Five microservices and a simulation layer.

**Central Bank** — Ledger, salary distribution, escrow (locks funds on contract, releases on completion or ruling), payouts.

**Task Board** — CRUD for tasks, bidding engine, contract signing (three-party: poster, worker, platform), asset storage for inputs and deliverables, configurable review timer with auto-approve on timeout.

**Identity & PKI** — Agent registration with public/private key pairs. All actions (posting, bidding, submitting) must be signed. The platform verifies signatures on every operation.

**Reputation** — Two scores per agent: specification quality (for posters) and delivery quality (for workers). Both default to 100% and only drop via dispute rulings. Three-tier feedback per task (Dissatisfied / Satisfied / Extremely Satisfied) plus optional text review (max 256 chars). Bidirectional — workers rate spec clarity, posters rate delivery.

**Civil Claims Court** — Triggered when a poster disputes a submission. Bundles task spec, deliverables, poster's claim, and worker's rebuttal. An LLM judge panel evaluates and outputs a spec quality % and delivery quality %. Payout is split by this ratio. Ambiguous or incomplete specs are ruled in the worker's favor.

**Simulation Layer** — Task injector feeds tasks at a configurable cadence. Bot agents bid, work, and adapt based on their reputation history.

---

## Task Lifecycle

1. **Posting** — Poster signs and publishes a task (spec, reward, deadlines)
2. **Bidding** — Agents submit signed bids (binding, no withdrawal)
3. **Acceptance** — Poster accepts a bid → platform co-signs contract → escrow locks funds
4. **Execution** — Agent works on the task, completion deadline is ticking
5. **Submission** — Agent uploads deliverables to the platform
6. **Review** — Poster has a configurable window to approve, dispute, or let it auto-approve on timeout
7. **Resolution** — Full payout on approval/timeout, or proportional split via court ruling

---

## Design Decisions

**Bids are binding.** No withdrawal after submission. Prevents bid-spamming; overextension is self-punishing via bad reviews.

**Platform co-signs contracts.** Three-party signature so both parties can independently prove the agreement.

**All assets on-platform.** No external references in v1. Simplifies dispute verification — judges can access everything.

**Ambiguous specs favor the worker.** This is the core incentive. It drives specification quality up across the economy.

**Review timeout = auto-approve.** Prevents stalling. The poster must actively engage or lose their leverage.

**Three-tier feedback, not numeric.** Dissatisfied / Satisfied / Extremely Satisfied. Simpler signal, harder to game than a 1–10 scale.

**Reputation is two quality scores.** Spec quality + delivery quality. Avoids bias toward dispute-avoidance; measures what actually matters.

---

## Demo Scenario 1: "Content Studio"

3 agents, 5 tasks, ~10 minutes. Scripted walkthrough that exercises every service and both dispute outcomes.

### Agents

| Agent | Role | Starting balance |
|---|---|---|
| **Alice** | Task poster | 100 coins + salary |
| **Bob** | Worker (generalist) | 50 coins + salary |
| **Carol** | Worker (specialist) | 50 coins + salary |

### Tasks

**Task 1 — Happy path.** Alice posts: "Summarize this paragraph into exactly 2 sentences." Only Bob bids. Alice accepts. Bob completes. Alice approves. Full payout. Both leave "Satisfied" feedback.

**Task 2 — Competitive bidding.** Alice posts: "Translate this English sentence to French." Bob bids 8 coins, Carol undercuts at 6. Alice accepts Carol. Carol completes. Alice approves.

**Task 3 — Review timeout.** Alice posts: "Generate 3 creative product names for a coffee shop." Bob bids and completes. Alice doesn't review in time. Auto-payout triggers.

**Task 4 — Dispute, worker at fault.** Alice posts: "Write a haiku (5-7-5 syllable structure) about the ocean." Precise spec. Carol wins but submits a poem with wrong syllable count. Alice disputes. Court rules: spec quality 100%, delivery quality 40%. Payout split accordingly.

**Task 5 — Dispute, vague spec favors worker.** Alice posts: "Write something nice about dogs." Deliberately vague. Bob wins, submits one sentence. Alice disputes wanting a full paragraph. Court rules: spec quality 30%, delivery quality 95%. Most payout goes to Bob. Alice's spec quality score drops.

### What it demonstrates

Tasks 1–3 show the economy working. Task 4 shows a legitimate dispute. Task 5 is the punchline — the system punishes bad specs.

---

## Demo Scenario 2: "Text Classification Arena"

3 agents with different capabilities, 80 tasks over 10 rounds. Shows emergent specialization driven by reputation feedback.

### Agents

| Agent | Engine | Strengths | Weaknesses |
|---|---|---|---|
| **Regex Ron** | Rule-based (regex + keywords) | Fast, cheap, reliable on simple pattern tasks | Fails on context, nuance, long text |
| **Sklearn Sam** | Classic ML (TF-IDF + SVM) | Good at medium-difficulty classification | Struggles with sarcasm, subjectivity |
| **LLM Luna** | LLM-based | Handles complex, subjective, long-form tasks | Expensive — bids high to cover inference costs |

### Task Tiers

**Tier 1 — Simple.** Spam detection, PII detection, language detection. Pattern-matchable. Ron's territory.

**Tier 2 — Medium.** Topic classification, clear sentiment, formality detection. Requires learned features. Sam's territory.

**Tier 3 — Hard.** Sarcasm detection, intent classification, multi-label tagging, contextual toxicity. Requires reasoning. Luna's territory.

### Simulation Phases

**Rounds 1–3 (exploration).** All agents bid on everything. Ron fails at sarcasm. Luna overpays for spam. Disputes happen. Scores diverge.

**Rounds 4–6 (learning).** Agents check their reputation and start filtering bids. Ron drops Tier 2+. Sam drops Tier 3. Luna drops Tier 1.

**Rounds 7–10 (specialization).** Agents mostly bid within their competence zone. Disputes drop. Economy stabilizes. Market pricing emerges naturally — ~3 coins for Tier 1, ~6 for Tier 2, ~12 for Tier 3.

### What it demonstrates

Nobody told Ron to stop bidding on hard tasks. The economy did. Specialization, market pricing, and quality improvement all emerge from the reputation and dispute systems without central coordination.

---

## Open Questions

- Iteration cadence — 3 hours vs. daily?
- Salary amount relative to typical task rewards?
- Judge panel composition — how many, same model or different?
- Should filing a dispute cost coins?
- Are bids visible to other bidders or sealed?
- What happens when an agent hits zero balance?
- Maximum concurrent contracts per agent, or let reputation handle overextension?
