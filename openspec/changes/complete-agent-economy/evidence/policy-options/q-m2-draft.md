# Q-M2 / T-163: pricing in the economic Vision — proposed, not accepted

Fixed posted rewards remain the ratified Core contract. Nothing in this draft
changes an existing task or authorizes implementation before the owner decides.

## Proposed starting rule

For newly posted tasks explicitly using a new price-based contract version,
treat the posted reward as a maximum budget. A valid bid is a positive integer
no greater than that budget. At acceptance, freeze the selected bid as the
contract price. Retain the full budget in escrow until final settlement.

Approval pays the contract price. A Court ruling awards a percentage of that
price, independently of its quality findings. For integer credits:

`worker payment = floor(contract price × worker percentage / 100)`

`poster refund = original escrow budget − worker payment`

All rounding remainder goes to the poster. The two legs must sum exactly to the
original escrow, including zero-value logical legs, without overflow or float
arithmetic. The worker percentage remains the Court's percentage of the agreed
price; it must not be relabeled as a percentage of the larger reserved budget.

There is one final settlement, with no partial refund at acceptance. This keeps
acceptance free of an additional money-transfer/recovery step, at the cost of
holding the poster's unused budget until settlement.

## Worked outcomes

Assume a posted budget of100 credits and an accepted bid of61. These are worked
examples, not configuration defaults.

| Outcome | Worker payment | Poster refund | Timing |
| --- | ---: | ---: | --- |
| Poster cancels before acceptance |0|100| Cancellation settlement |
| Bidding/acceptance window expires without a contract |0|100| Expiry settlement |
| Accepted worker misses execution deadline |0|100| Execution-expiry settlement |
| Poster approves, or review times out |61|39| Final settlement |
| Court awards0% of the contract price |0|100| Final settlement |
| Court awards33% |20|80| Final settlement: floor(61×33/100)=20 |
| Court awards50% |30|70| Final settlement: floor(61×50/100)=30 |
| Court awards100% |61|39| Final settlement |

A bid above100 or a zero/negative/non-integer bid is invalid for this version.
Unselected bids have no financial effect. Existing no-fee dispute and zero-balance
worker policies remain unchanged.

## Alternatives

| Choice | Effect | Tradeoff |
| --- | --- | --- |
| **A. Accepted price; excess returned at settlement — proposed recommendation** | Outcomes above; one final split conserves the full original budget. | Simpler recovery, but39 unused credits remain unavailable during the contract. |
| **B. Accepted price; excess returned at acceptance** | Refund39 once acceptance commits; retain61 for the worker/poster settlement. At33%, pay20 and refund41 later. | Releases capital earlier; requires an atomic, replayable acceptance/refund operation and a truthful remaining-escrow model. |
| **C. Fixed reward remains the final model** | Approval pays100 regardless of bid61; a33% ruling pays33 and refunds67. | Preserves current mechanics, but bids are signals rather than prices. The product must describe and measure that competition honestly, without a price-formation claim. |

## Versioning and rollout

Every task must retain an explicit immutable pricing version. Existing posted
tasks, including those still bidding, keep their original fixed-reward terms.
No migration may reinterpret an old accepted bid as a new price. The new rule
applies only to newly posted tasks whose signed terms disclose it; acceptance
freezes budget, price, parties, deadlines and version.

The public API, SDK, UI, Court context, Bank receipts, runtime journals and
economic examples must distinguish budget, bid, price, payout percentage,
payment and refund. A lost acknowledgment must replay the same versioned money
operation. A disputed accepted price does not authorize changing that price.

Engineering must add an amount/price-aware settlement contract for option A;
the current Bank percentage-only split applies to the entire escrow and cannot
be reused by passing the Court percentage unchanged. Option B instead needs
durable partial-release and remaining-escrow support. Neither is a documentation
change or an unannounced formula reinterpretation.

## Owner decision required

Select A, B or C, or specify another complete pricing/rounding/refund rule.
Acceptance will be recorded separately with the worked outcomes before MP-24's
price-forming implementation or claims. This draft is not an accepted ADR and
does not close T-163.
