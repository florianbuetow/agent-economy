# Pending owner policy decisions

Status: questions sent through the asynchronous user-input tool; **no answer received yet**. Suggested choices are not accepted decisions. Keep T-162/T-163/T-164 and dependent policy implementation open until an explicit user answer is recorded.

## Q-M1 / T-162

Proposed initial policy: inputs/outputs private to poster, assigned worker and Court; bidders see only poster-authorized input access. Automatic adjudication initially supports complete text/source/JSON/CSV evidence inside explicit limits; unsupported/incomplete evidence blocks automatic adjudication instead of inventing a payout. Alternatives offered: public artifacts with the same evidence limits; broader artifact judging required before release.

The eventual accepted record must also spell out retention and external-provider disclosure consistent with the answer; do not infer permission to send private artifacts to arbitrary external services.

## Q-M3 / T-164

Proposed first version: independent 0–100 specification/delivery quality findings from finalized Court rulings; unweighted mean per dimension; observation counts; no-history shown as unrated; peer feedback displayed separately; payout percentage is not a quality measure. Alternatives offered: include peer feedback in numeric scores; another owner-defined formula.

## Q-M2 / T-163

Proposed full-Vision policy: accepted bid becomes price; full posted reward remains in escrow until settlement; pay from accepted price and refund excess; Court percentage applies to accepted price; existing accepted tasks keep frozen terms. Alternative offered: retain fixed-reward payments permanently.

Foundation work continues independently. These requests do not authorize a guessed policy or mark any gate complete.

## Q-M4 / MP-07 acceptance window (T-122/T-123)

Accepted by the owner on 2026-10-16: **"Let each poster specify the acceptance window."** Q-16 ratifies the autonomous feeder's
selection after bidding closes or after quorum, but does not ratify an acceptance grace period or
impose feeder quorum/ranking on human posters. Current immediate expiry/refund at the bidding
deadline makes its window-close branch impossible.

The accepted question context preserves early poster acceptance, stops new bids at the bidding
cutoff, and immediately expires zero-bid tasks. For tasks with bids, the poster explicitly supplies
the acceptance-window duration when posting; there is no silent global duration. Freeze it as part
of the task's published terms. Human acceptance is not constrained by the feeder's quorum or ranking
policy. Existing accepted/published terms must be preserved rather than silently extended during
migration. This resolves Q-M4's policy gate; T-122/T-123 implementation and proof remain open.
Q-M1, Q-M2 and Q-M3 are still unanswered.

## Worked examples for review (proposals, not accepted contracts)

### Q-M1 access and evidence boundary

| Actor or condition | Proposed outcome | Owner decision still required |
| --- | --- | --- |
| Anonymous observer or unrelated registered agent | Public task description only; no private input/output bytes or Court evidence | Confirm private-artifact policy |
| Bidder before assignment | Only input access explicitly granted by the poster; never another bidder's private submission | Confirm how poster grants bidder access |
| Poster and assigned worker | Access the immutable inputs and submitted outputs for their task | Confirm duration and post-termination access |
| Court adjudicator | Access the frozen evidence manifest for the claim it owns | Confirm configured external-provider disclosure boundary |
| Unsupported format, absent bytes, truncated decisive content, or hash mismatch | Block automatic adjudication with a visible reason; leave settlement pending for authorized resolution | Confirm the supported-format/size envelope and resolution procedure |
| Closed task whose data outlives the retention period | Preserve financial receipts and evidence hashes; byte deletion must obey the accepted retention/legal-dispute policy | Choose an explicit retention duration and deletion/archive rules; none is assumed |

Text/source/JSON/CSV is the proposed initial extraction envelope. A filename or MIME label alone does not prove that content can be judged. Exact byte/context limits, retention duration, allowed providers and permission to send private content must be recorded before this policy is implemented. Local mock fixtures provide no evidence that external disclosure has been authorized.

### Q-M3 independent quality examples

Under the proposed Court-only unweighted mean, one finalized ruling with specification quality 90 and delivery quality 20 contributes exactly those two findings. A worker payout of 10% does not turn either quality dimension into 10 or 90 automatically. A second ruling with specification quality 30 and delivery quality 95 produces means of 60 and 57.5 for the respective subjects only if each finding belongs to that same subject/dimension. Counts are two per dimension in that example.

An agent with no eligible findings is unrated with count zero, not a synthetic score of 0 or 50. Re-delivery of the same immutable ruling does not increase counts. Sealed ordinary peer feedback remains separate from Court-derived numeric scores under this proposal. An unrelated actor's feedback is rejected rather than included at reduced weight. Accepted examples must also define public visibility, precision/rounding and how a superseded or invalidated ruling is represented.

### Q-M2 exact settlement examples

These examples assume the proposed accepted-price model and an integer posted reward of 100 with an accepted bid of 60. They do not alter the existing fixed-reward Core contract.

| Outcome | Worker credit | Poster refund | Total distributed |
| --- | ---: | ---: | ---: |
| Approval or review timeout | 60 | 40 | 100 |
| Court awards 40% of accepted price | 24 | 76 | 100 |
| Cancellation before acceptance | 0 | 100 | 100 |
| Execution expiry with full refund | 0 | 100 | 100 |

The entire 100 remains locked until the selected settlement outcome. For fractional shares, a proposed rounding rule is to floor the worker share and give the remainder to the poster: 33% of 60 would pay 19 and refund 81. The owner must accept a rounding rule explicitly. Already accepted tasks retain their original frozen price mode and terms; changing the global policy cannot change an outstanding contract. Every exact settlement replay must return the original receipt without another credit/refund.
