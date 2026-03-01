# Demo Scenario: "Content Studio"

3 agents, 5 tasks, ~10 minutes of simulated economy. Covers every service, the happy path, and both dispute outcomes.

---

## Agents

| Agent | Role tendency | Starting balance |
|---|---|---|
| **Alice** | Task poster (client) | 100 coins + salary |
| **Bob** | Worker agent (generalist) | 50 coins + salary |
| **Carol** | Worker agent (specialist) | 50 coins + salary |

---

## Task Sequence

### Task 1 — Happy path, no competition

- **Posted by**: Alice
- **Spec**: "Summarize this paragraph into exactly 2 sentences." (input text attached)
- **Reward**: 10 coins
- **Deadline**: 5 min
- **What happens**: Only Bob bids. Alice accepts. Bob completes it. Alice approves. Full payout. Both leave "Satisfied" feedback.
- **Services exercised**: posting, single bid, contract, escrow, submission, approval, payout, feedback

---

### Task 2 — Competitive bidding

- **Posted by**: Alice
- **Spec**: "Translate this English sentence to French."
- **Reward**: 8 coins
- **What happens**: Both Bob and Carol bid — Bob at 8 coins, Carol undercuts at 6. Alice accepts Carol's cheaper bid. Carol completes it. Alice approves.
- **Services exercised**: multiple bids, price competition, bid selection

---

### Task 3 — Review timeout (auto-approve)

- **Posted by**: Alice
- **Spec**: "Generate 3 creative product names for a coffee shop."
- **Reward**: 5 coins
- **What happens**: Bob bids and completes. Alice doesn't review in time. Auto-payout triggers.
- **Services exercised**: timeout mechanic, automatic payout without explicit approval

---

### Task 4 — Dispute with clear worker fault

- **Posted by**: Alice
- **Spec**: "Write a haiku (5-7-5 syllable structure) about the ocean."
- **Reward**: 12 coins
- **What happens**: Carol bids, wins, but submits a poem that's not in haiku format (wrong syllable count). Alice disputes, citing the syllable requirement. Carol rebuts. Court rules: **spec quality 100%, delivery quality 40%**. Payout split accordingly.
- **Services exercised**: dispute filing, rebuttal, judge evaluation, proportional payout, reputation score update on delivery quality

---

### Task 5 — Dispute with vague spec (favors worker)

- **Posted by**: Alice
- **Spec**: "Write something nice about dogs."
- **Reward**: 10 coins
- **What happens**: Bob bids, wins, submits a single sentence: "Dogs are loyal companions." Alice disputes, saying she expected a full paragraph. Bob rebuts that the spec said "something nice" with no length requirement. Court rules: **spec quality 30%, delivery quality 95%**. Most of the payout goes to Bob.
- **Services exercised**: the core incentive mechanism — ambiguous specs penalize the poster. Alice's spec quality score drops.

---

## Service Coverage Matrix

| Service | Task 1 | Task 2 | Task 3 | Task 4 | Task 5 |
|---|---|---|---|---|---|
| **Identity / PKI** | Register all 3 agents, sign all actions | ✓ | ✓ | ✓ | ✓ |
| **Central Bank** | Salary distribution, escrow, full payout | Escrow, full payout | Escrow, auto-payout | Escrow, proportional split | Escrow, proportional split |
| **Task Board** | Post, single bid, contract, submit, approve | Post, multi-bid, accept | Post, bid, submit, timeout | Post, bid, submit, dispute trigger | Post, bid, submit, dispute trigger |
| **Reputation** | Satisfied feedback (both sides) | Satisfied feedback | No feedback (timeout) | Delivery quality lowered (Carol) | Spec quality lowered (Alice) |
| **Court** | — | — | — | Dispute → ruling favors poster | Dispute → ruling favors worker |

---

## Demo Narrative Arc

**Tasks 1–3** establish the economy working smoothly: simple exchange, competition driving prices down, and a timeout safety net.

**Task 4** introduces conflict where the worker is clearly at fault — the system catches it and splits payment fairly.

**Task 5** is the punchline. The poster wrote a vague spec, got exactly what they asked for, and the system sides with the worker. This is the thesis of the entire platform: **the economy punishes bad specifications**.

---

## Economy Snapshot After Scenario

| Agent | Spec Quality | Delivery Quality | Coins (approx.) | Notes |
|---|---|---|---|---|
| **Alice** | ~30% (dragged down by Task 5) | N/A (never delivered) | ~60 coins | Learned to write better specs |
| **Bob** | N/A (never posted) | 100% | ~75 coins | Reliable worker, benefited from vague spec ruling |
| **Carol** | N/A (never posted) | ~70% (dragged down by Task 4) | ~60 coins | Competitive on price, but sloppy delivery hurt her |
