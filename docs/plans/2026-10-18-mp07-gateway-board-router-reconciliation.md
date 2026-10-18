# MP-07 Gateway Board router-test reconciliation

## Scope and evidence

This reconciliation updates only the legacy Gateway Board router suite to the reviewed native
operation contract. It does not change production code or claim MP-07/root completion.

- Original test source SHA-256: `7c39a3cd68f50ba8d7569cde80aef2289fcebf202d71feb4b51e7bfc69bdf3af`.
- Focused baseline: `/private/tmp/agent-economy-mp07-gateway-board-router-baseline.log`.
- Baseline result: 37 collected, 14 passed, 23 failed.
- Root cause: publication fixtures omit the now-required acceptance window, and the bid/status
  success cases call writers intentionally retired by the MP-07 contract.

## Fixture contract

- Use a fixed Gateway business clock in 2099 while leaving JWT signing/verification on its real
  clock. Publish coherent integer bidding and acceptance cutoffs around that fixed time.
- Register every poster, bidder, worker and ruling actor through Identity. The ruling actor uses
  the actual application-configured platform authority public key.
- Fund the poster and lock exactly 100 credits. A bid may be 90, but acceptance and every payout
  remain tied to the frozen task reward of 100.
- Create bids and lifecycle transitions through `POST /board/task-operations`. For Bank/Court
  effects, persist the real Gateway-owned receipt/claim through its domain HTTP endpoint, then
  finalize with the empty object. Helpers only compose those public requests and inspect the real
  persisted rows; they do not write lifecycle history directly.

## Per-test reconciliation

### TASK-01 through TASK-09

- Add `acceptance_window_seconds` and coherent `bidding_cutoff_us` / `acceptance_cutoff_us` to all
  otherwise complete publications.
- Preserve valid creation `201`, duplicate/FK errors, required-field errors, negative-reward
  rejection, and task/event atomicity assertions.

### BID-01 through BID-07

- BID-01 routes a valid bid through the native operation endpoint and asserts the completed
  operation plus persisted bid/event.
- BID-02 submits the second bid from the same bidder under a distinct operation and preserves the
  `409 bid_exists` guarantee.
- BID-03 uses two registered bidders and two completed native operations.
- BID-04 changes the expected missing-task result to the reviewed `404 task_not_found` contract.
- BID-05 preserves the actual bidder foreign-key conflict.
- BID-06/BID-07 replace obsolete proposal/event validation with native structural negatives:
  missing `amount` in the exact argument object, and an injected top-level `event`. Both must
  return `400 invalid_payload` without bids, operations or events.

### TSTAT-01 through TSTAT-14

- TSTAT-01 accepts a persisted bid via a pure native operation.
- TSTAT-02 accepts, records a real worker asset, then submits via native operations.
- TSTAT-03 adds a real Bank release receipt after the approval claim and finalizes it.
- TSTAT-04 claims cancellation, persists the real Bank release receipt, and finalizes it.
- TSTAT-05 claims dispute, persists a real Court claim, and finalizes it.
- TSTAT-06 executes the complete bid/accept/asset/submit/dispute/Court-claim/ruling/Bank-receipt
  history, registering the ruling actor with the configured authority key before finalization.
- TSTAT-07 expires a no-bid task at the fixed bidding cutoff, then persists/finalizes the refund.
- TSTAT-08 preserves `404 task_not_found` through a valid native command.
- TSTAT-09 replaces generic unknown-column coverage with an unknown native argument and proves no
  mutation.
- TSTAT-10/TSTAT-11 replace generic empty/missing-updates coverage with empty/missing native
  arguments and prove structural rejection.
- TSTAT-12 replaces missing generic event with an injected top-level event and proves structural
  rejection.
- TSTAT-13 preserves the multi-field persistence observation after native acceptance, including
  worker, accepted bid, timestamps, deadlines, frozen reward/bid amount and lifecycle version.
- TSTAT-14 reverses the obsolete “Gateway does not validate transitions” premise: a validly shaped
  approval from `open` is rejected as `invalid_status`, with task/contract/event/operation state
  unchanged.

### ASSET-01 through ASSET-07

- Keep the asset endpoint contract unchanged. Repair publication prerequisites only, and preserve
  valid, duplicate, FK, required-field and multiple-asset observations.

## Verification

Run the focused suite after each code edit, then run Ruff, format check and the broader Gateway
unit suite as an integration checkpoint. Archive raw outputs and final source hashes. Any new
production failure is reported instead of weakening an assertion.

Final focused evidence after the test source was frozen:

- `/private/tmp/agent-economy-mp07-gateway-board-router-final.log`: 37 passed.
- `/private/tmp/agent-economy-mp07-gateway-board-router-broader.log`: 97 passed across this file,
  the signed HTTP operation boundary, retired native-write boundary, operation regressions, pure
  finalization and Court-operation contract.
- `/private/tmp/agent-economy-mp07-gateway-board-router-ruff.log`: Ruff exit 0.
- `/private/tmp/agent-economy-mp07-gateway-board-router-format.log`: three files already formatted.
- `/private/tmp/agent-economy-mp07-gateway-board-router-sha256.log`: final scoped source hashes.

The broader 97-test gate is intentionally scoped. It does not replace the still-required full
Gateway and root CI gates, and it makes no claim about failures owned by other reconciliation
lanes.
