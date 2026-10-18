# Q-M3 / T-164: independent quality standing — proposed, not accepted

Q-12 assigns aggregation to Reputation; Q-13 requires a three-judge real panel
and an explicit ambiguity rubric. Q-16 compares delivery quality after bid
amounts tie. This draft proposes the missing formula; it does not authorize a
policy change or close T-164.

## Proposed `quality-v1` starting rule

Each judge returns separate integer0–100 specification-quality and delivery-
quality findings, each with its own rubric-based explanation. The worker payout
percentage is a third independent field. Aggregate each field by the median of
the three judge values. Never derive quality from payout or its complement.

Eligible, durably revealed peer ratings map dissatisfied→0, satisfied→50 and
extremely_satisfied→100. The assigned worker rates the poster's specification;
the poster rates the assigned worker's delivery. Only eligible terminal-task
observations contribute.

For each subject, dimension and task, a finalized Court finding replaces the
peer observation for scoring that same task. Preserve the peer record in
provenance with exclusion reason `court_override`. Otherwise use the revealed
peer rating. Each included task contributes exactly once, with equal weight.

The score is the arithmetic mean of included task observations, rounded once
half-up to the nearest integer using exact integer/rational arithmetic. With no
eligible observations return `status: unrated`, `score: null`, and zero counts;
do not display invented earned standing.

Return the formula version, included task count, included Court/peer counts,
excluded peer count and distinct peer-counterparty count. Provide bounded,
paginated provenance showing source identities, dimensions, values and inclusion
reasons without exposing private artifact content. Counts are descriptive;
version1 makes no statistical-confidence claim.

Exact feedback/ruling replay returns the original logical observation and never
changes counts. Changed content under that identity conflicts. Unrelated,
premature, wrong-direction, wrong-subject and self-authored feedback is rejected
using authoritative task parties/outcome. Invalid historical rows are excluded
with visible reasons rather than silently treated as valid data.

## Worked examples

| Case | Included observations | Result |
| --- | --- | --- |
| Precise specification, poor delivery | Court specification92, delivery18 | Poster specification92; worker delivery18; one task each |
| Vague specification, adequate delivery | Court specification25, delivery80 | Poster specification25; worker delivery80; independent payout |
| No history | None | Unrated, null score, counts0 |
| Peer disagreement across two tasks |100 and0| Score50, peer tasks2 |
| Peer0 and Court80 on the same task | Court80; peer excluded | Score80, included tasks1, excluded peer1 |
| Repeat one ruling three times | Court70, same ruling/task identity | Score70, included Court tasks1 |
| Ten100 ratings from one partner, one0 from another | Eleven eligible tasks | Score91; tasks11, counterparties2 |

The final example exposes the simple formula's limit: repeated legitimate tasks
between cooperating identities can amplify standing. Authorization, unique task
observations and replay suppression do not provide collusion or Sybil resistance.
No staking, fees, time decay or concurrent-contract caps are introduced.

## Equal-amount feeder selection

Compare amount first. For equal-amount contenders, fetch the supported delivery
standing. Positive rated scores outrank lower scores. A valid unrated result
has selection rank0 only; the endpoint and UI still show null/unrated. A rated0
and unrated contender therefore tie, resolved by the existing lexicographic bid
ID rule. Counts do not break ties.

Lookup failure, malformed response or unsupported formula is not an unrated
result. It creates no new selection intent. Retry that tied task within its
configured bounds/window while processing independent tasks; the task may
expire if the dependency does not recover. Do not discard known standing or
silently invent an empty score to make acceptance succeed.

## Alternatives for the owner

| Choice | Benefit | Cost |
| --- | --- | --- |
| **A. Flat task mean after Court override — proposed recommendation** | Simple and explainable; task count equals scoring sample count. | Repeated-pair influence remains visible but unbounded. |
| **B. Group peer tasks by reviewer, then average reviewer means plus individual Court-task findings** | Limits repeated trading with the same peer. | Ten tasks from one reviewer count like one from another; adds unequal effective task weights. The eleven-task example becomes50. |
| **C. Court findings only** | Strongest available independent provenance and simple formula. | Most agents without disputes remain unrated; ordinary feedback has no scoring influence. |

Owner acceptance is required before the formula, no-history ordering and strict
dependency-failure behavior become a decision record or implementation. These
choices affect reputation-driven selection and the later economic Vision; they
do not change the ratified fixed-reward Core settlement rule.
