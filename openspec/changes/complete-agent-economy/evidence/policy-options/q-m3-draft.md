# Q-M3 / T-164: independent quality standing — proposed, not accepted

**Status: DRAFT — no formula, eligibility rule, visibility rule, or appeal policy below is accepted.** Q-12 assigns aggregation to Reputation; Q-13 requires a three-judge real panel and an explicit ambiguity rubric. Q-16 compares delivery quality after bid amounts tie. This draft proposes the missing versioned contract; it does not authorize a policy change or close T-164.

## Proposed `quality-v1` starting rule

Each judge returns separate integer 0–100 specification-quality and delivery-quality findings, each with its own rubric-based explanation. The worker payout percentage is a third independent field. Aggregate each field by the median of the three judge values. Never derive either quality dimension from payout or its complement.

Eligible, durably revealed peer ratings map `dissatisfied` to 0, `satisfied` to 50, and `extremely_satisfied` to 100. The assigned worker rates the poster's specification; the poster rates the assigned worker's delivery.

For each subject, dimension, and task, a finalized Court finding replaces the peer observation for scoring that same task. Preserve the peer record in provenance with exclusion reason `court_override`. This source replacement does not replace or supersede the ruling itself. Otherwise use the eligible revealed peer rating. Each included task contributes exactly once, with equal weight.

The score is the arithmetic mean of included task observations, rounded once half-up to the nearest integer using exact integer/rational arithmetic. With no eligible observations return `status: unrated`, `score: null`, and zero counts; do not display invented earned standing.

Return the formula version, included task count, included Court/peer counts, excluded peer count, and distinct peer-counterparty count. Counts are descriptive; `quality-v1` makes no statistical-confidence claim.

Exact feedback/ruling replay returns the original logical observation and never changes counts. Changed content under that identity conflicts. Unrelated, premature, wrong-direction, wrong-subject, and self-authored feedback is rejected using authoritative task parties and outcomes. Invalid historical rows are excluded with visible reasons rather than silently treated as valid data.

## Proposed outcome eligibility

Under the recommended rule, a task contributes a quality observation only when its authoritative outcome is one of:

- poster approval after a valid submission;
- review-timeout auto-approval after a valid submission; or
- a finalized Court ruling.

Cancellation, bidding or acceptance expiry with no contract, and execution expiry do not contribute to either quality dimension. They have no judged specification/delivery pair under this proposed contract. A task that never received a valid submission likewise contributes no peer quality score.

This exclusion has a deliberate limit: delivery standing does not penalize a worker for abandonment, missed execution deadlines, or other reliability failures, and specification standing does not penalize a poster for a no-contract outcome. If the owner wants those outcomes to affect selection, the project must define a separate reliability/contract-performance dimension or explicitly revise the quality semantics before implementation. They must not be smuggled into delivery/specification quality as invented zeroes.

A Court finding is eligible only from the task's immutable final ruling. Under the proposed no-merits-appeal policy, that ruling cannot be replaced or superseded. Operational replay redelivers the identical ruling and cannot add an observation. If the owner selects merits appeals, a ruling-version contract must first define which version is current, when an earlier observation is replaced, how counts/provenance change, and how downstream selection replays are reconciled.

## Proposed visibility and authorized consumers

**Recommended visibility:** publish each subject's aggregate status, score, formula version, included/excluded counts, and distinct-counterparty count. Restrict source-level provenance, source identities, rubric explanations, and exclusion reasons to the score subject, authorized participants in the source task, and the local operator. Never expose private artifact content through either projection.

The feeder is an authorized ranking consumer. It receives the supported delivery aggregate needed by Q-16 — status, score, formula version, and counts — through the same versioned Reputation contract. It does not need private source-level provenance, rubric text, or artifact content to select among equal-amount bids. A feeder lookup failure remains a dependency failure, not an unrated score.

Visibility is an independent owner choice:

| Choice | Aggregate | Source-level provenance | Tradeoff |
| --- | --- | --- | --- |
| **P-A. Public aggregate, restricted provenance — proposed recommendation** | Public status, score, version, and counts; feeder has the same ranking projection. | Subject, authorized task participants for their task, and operator only. | Supports public standing and feeder selection while limiting participant-history disclosure. |
| **P-B. Participant-restricted aggregate and provenance** | Subject, authorized task participants, operator, and explicitly authorized feeder only. | Same restricted audience. | Stronger privacy, but public observers cannot verify or compare standing. |
| **P-C. Public revealed provenance** | Public aggregate. | Public source task identity, source kind, dimension, value, inclusion/exclusion reason, and counterparty identity after lawful reveal; never artifact content. | Maximum inspectability with substantial relationship and outcome privacy cost. |

Every provenance response is bounded and paginated. Authorization is evaluated per source record; access to one task does not disclose unrelated private task provenance.

## Worked examples

| Case | Included observations | Result |
| --- | --- | --- |
| Precise specification, poor delivery | Court specification 92, delivery 18 | Poster specification 92; worker delivery 18; one task each |
| Vague specification, adequate delivery | Court specification 25, delivery 80 | Poster specification 25; worker delivery 80; independent payout |
| No eligible history | None | Unrated, null score, counts 0 |
| Peer disagreement across two tasks | 100 and 0 | Score 50, peer tasks 2 |
| Peer 0 and Court 80 on the same task | Court 80; peer excluded | Score 80, included tasks 1, excluded peer 1 |
| Repeat one ruling three times | Court 70, same ruling/task identity | Score 70, included Court tasks 1 |
| Ten 100 ratings from one partner, one 0 from another | Eleven eligible tasks | Score 91; tasks 11, counterparties 2 |
| Worker misses execution deadline | No quality observation | Existing quality score unchanged; reliability consequence is outside `quality-v1` |
| Task expires before a contract | No quality observation | Both parties' quality histories unchanged |

The repeated-partner example exposes the simple formula's limit: repeated legitimate tasks between cooperating identities can amplify standing. Authorization, unique task observations, and replay suppression do not provide collusion or Sybil resistance. No staking, fees, time decay, concurrent-contract caps, or new dependencies are introduced.

## Equal-amount feeder selection

Compare amount first. For equal-amount contenders, fetch the supported delivery standing. Positive rated scores outrank lower scores. A valid unrated result has selection rank 0 only; the endpoint and UI still show `null`/`unrated`. A rated 0 and unrated contender therefore tie, resolved by the existing lexicographic bid ID rule. Counts do not break ties.

Lookup failure, malformed response, unauthorized response, or unsupported formula is not an unrated result. It creates no new selection intent. Retry that tied task within its configured bounds/window while processing independent tasks; the task may expire if the dependency does not recover. Do not discard known standing or silently invent an empty score to make acceptance succeed.

## Formula alternatives for the owner

| Choice | Benefit | Cost |
| --- | --- | --- |
| **F-A. Flat task mean after Court override — proposed recommendation** | Simple and explainable; task count equals scoring sample count. | Repeated-pair influence remains visible but unbounded. |
| **F-B. Group peer tasks by reviewer, then average reviewer means plus individual Court-task findings** | Compute one exact mean per peer reviewer, retain each Court task as one unit, then take the exact half-up mean of those units. This limits repeated trading with the same peer. | Ten tasks from one reviewer count like one from another; adds unequal effective task weights. The eleven-task example becomes 50. |
| **F-C. Court findings only** | Take the exact half-up mean of eligible Court-task findings. This has the strongest available independent provenance and a simple formula. | Most agents without disputes remain unrated; ordinary feedback has no scoring influence. |

All formula choices retain separate specification/delivery dimensions, payout independence, exact half-up arithmetic, one logical observation per task/dimension, explicit no-history output, replay suppression, eligibility checks, source counts, and no coercion of lookup failure into `unrated`.

## Bounded owner decisions still required

- Choose F-A, F-B, or F-C for source weighting and aggregation.
- Confirm the recommended eligible outcomes, or explicitly define how cancellation, no-contract expiry, and execution expiry affect a named quality or reliability dimension.
- Choose P-A, P-B, or P-C for aggregate/provenance visibility and confirm the feeder's authorized ranking projection.
- Confirm the proposed no-merits-appeal rule. If appeals are required, define ruling versioning, supersession, score replacement, and downstream replay before implementation.

These choices affect reputation-driven selection and the later economic Vision. They do not change the ratified fixed-reward Core settlement rule, introduce fees/caps, or authorize implementation before an explicit owner decision.
