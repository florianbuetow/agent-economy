# Adversarial campaign: publication, auctions, identity, assets, and economics

Source revision at execution start: `35cf0e3a5fcfd3c6a92d6c2e305bdfd711c7aa16`.
The working tree also contained the separately owned dispute/UI changes listed by
`git diff --name-only`; none of those files were modified by this lane. All
mutating E2E checks used repository-owned disposable runtimes.

## Executed commands

| Evidence | Command summary | Exit | Result |
| --- | --- | ---: | --- |
| E-UNIT | Task Board bidding, accepted-price, input, submission, deadline, Gateway serialization, and governed-asset unit files | 0 | 115 passed in 174.19 s |
| E-ID | Identity registration gaps and agent router tests | 0 | 23 passed in 0.42 s |
| E-RACE | Task Board public replay, bid-admission, acceptance/expiry, atomic transition, native lifecycle, and deadline-transition integration files | 0 | 20 passed in 15.65 s |
| E-UI-BROWSER | New three-worker/acceptance and approval-recovery scenarios plus existing posting conservation, each against an owned isolated seven-service runtime | 0 | 3 passed in 28.45 s (`ui-browser.log`) |
| E-AGENT | External earn-then-post input cycle and unattended divergent-task flow under `isolated_test_runtime.py --provision` | 0 | 2 passed in 8.30 s |
| E-POST-RECOVERY | Distinct identical postings, lost posting response, and Task Board SIGKILL/restart under an owned isolated runtime | 0 | 3 passed in 6.94 s |
| E-ASSET-PROJECTION | Atomic asset admission, storage safety, pending public projection, and terminal financial recovery | 0 | 70 passed in 12.05 s |
| E-PUB-FUNDS | New real-Gateway concurrent escrow admission regression | 0 | 1 passed in 0.56 s after two test-oracle corrections described below |
| E-PUB-PUBLIC | New public Task Board publication race over the real Gateway/Bank writer | 0 | 1 passed in 0.85 s (`taskboard-publication-funds.log`) |
| E-AUCTION | New two-acceptance race and equal-price explicit-choice regressions | 0 | 2 passed in 1.14 s |
| E-UPLOAD-FREEZE | Existing real-Gateway upload-versus-submit barrier | 0 | 1 passed in 0.85 s |
| E-PROTOCOL2 | Frozen input-contract and current opaque upload-key characterizations | 0 | 2 passed (`publication-pending-final.log`) |
| E-PENDING-UI | Browser rendering of absent-receipt and confirmed-receipt/projection-pending states | 0 | 2 passed (`ui-browser.log`) |
| E-COURT-NO-FEEDBACK | Court rejects non-completed/malformed Task Board settlement acknowledgments before Reputation | 0 | 31 passed (`court-no-feedback.log`) |
| E-SHARED-KEY | Two real OS actors sharing one key plus changed-operation conflict | 0 | Included in 4-pass `agent-adversarial-final.log` |
| E-CROSS-TASK | Two accepted tasks, cross-task asset lookup, wrong fresh content, and poster dispute | 0 | Included in 4-pass `agent-adversarial-final.log` |
| E-THREE-TASK | Concurrent approval, no-bid expiry, and 50% ruling with row/event/balance checks | 0 | Included in 4-pass `agent-adversarial-final.log` |
| E-MULTI-JOB | Durable two-job runtime-context isolation characterization | 0 | 1 passed (`multi-job-restart.log`) |
| E-MULTI-JOB-PROCESS | Real SIGKILL/restart with two accepted jobs and one expiry | 0 | Included in 4-pass `agent-adversarial-final.log` |
| E-MARKET-BURST | Parent-owned five-worker/five-task/two-poster market sequence | 0 | Retained by the parent campaign lane |

One discarded inventory invocation exited 4 before collection because it named
two nonexistent test files. It is not campaign evidence. A first E-AGENT launch
also exited 2 before runtime startup because the script path was relative to the
DB Gateway project; the absolute-path rerun above is the evidence-bearing run.

## Sequence dispositions

`PASS` means the complete sequence oracle was exercised by the cited run. `NOT RUN`
means the exact compound sequence remains unfinished even if nearby assertions
passed. `BLOCKED` is reserved for an owner decision or external prerequisite.

| ID | Disposition | Evidence and exact boundary |
| --- | --- | --- |
| PUB-01 | PASS | E-UI-BROWSER lost the committed response, restored the same saved operation in two tabs, sent two exact retries, and asserted one task, one escrow, identical bodies, completion markers, and conserved balance. |
| PUB-02 | PASS | E-PUB-PUBLIC admitted an uncertain protocol-1 publication and proved changed title, spec, reward, and every deadline fail before any new Bank effect (reward gets earlier `token_mismatch`; other changes get `operation_id_conflict`). E-PROTOCOL2 then froze one original input, injected an unknown Bank outcome (`awaiting_lock`), and rejected changed input bytes with 409 `invalid_status`; input-manifest/publication-contract hashes and the sole ready artifact hash remained unchanged, with zero task or escrow rows. |
| PUB-03 | PASS | E-PUB-FUNDS proved the Bank serialization invariant directly. E-PUB-PUBLIC then concurrently admitted two distinct reward-300 public postings from one balance-500 poster: one returned 201, one typed 402, final balance was 200, one task/escrow existed, and the loser remained an explicit `awaiting_lock` publication with no phantom task. |
| PUB-04 | PASS | E-POST-RECOVERY created two identical-term postings under distinct operations and asserted distinct task/escrow identities. |
| PUB-05 | PASS | E-POST-RECOVERY killed Task Board after the Bank lock commit and proved restart plus exact replay converged on one publication without compensation of the committed lock. |
| PUB-06 | PASS | E-UI-BROWSER selected two generation-1 files in one tab, replaced both paths, selected generation 2 in a second tab, deleted both sources, and posted concurrently. Both operations took the allowed atomic-failure branch: two explicit `awaiting_inputs` publications, zero tasks, zero escrows, and zero ready uploads. No mixed generation or partial task existed. |
| PUB-07 | PASS | E-RACE exercised URL/payload and cross-command operation-binding conflicts before public command mutation. |
| AUC-01 | PASS | E-UI-BROWSER registered three independently keyed workers, submitted concurrent sealed bids `60/70/60`, proved a worker receives 403 when listing bids, and proved the poster sees all three exact bid rows. |
| AUC-02 | PASS | E-UI-BROWSER held two stale pages without SSE refresh, clicked different visible Accept buttons concurrently, captured two distinct immutable operation bodies for the two bid IDs, observed one 200/one typed 409, then reloaded both tabs and matched the authoritative accepted bid to its exact worker at price 60. |
| AUC-03 | PASS | E-AUCTION used two tasks with reversed equal-price bid arrival order, explicitly chose a different worker on each, and proved both chosen worker IDs at price 60. |
| AUC-04 | PASS | E-UNIT rejected self-bid, signer/payload mismatch, and unknown signer without changing bid count/task state. |
| AUC-05 | PASS | E-RACE deterministically held bid admission across acceptance/cutoff barriers and rejected a commit at equality. |
| AUC-06 | PASS | E-UNIT deadline evaluator distinguished open tasks with bids from no-bid tasks and proved idempotent refund/expiry behavior. |
| AUC-07 | PASS | E-RACE returned the committed bid on exact response-loss replay while fresh late admission remained rejected. |
| IDN-01 | PASS | E-UNIT rejected poster, rival-worker, and signer/payload impersonation for upload/submission before manifest or lifecycle mutation. |
| IDN-02 | PASS | E-SHARED-KEY launched two OS subprocesses loading the same `alice.key`, synchronized them on a file barrier, and submitted the same bid operation. Both returned the identical 201 result while one bid/agent existed; a third same-key process changing amount under that operation got 409 and left the bid unchanged. |
| IDN-03 | PASS | E-ID registered distinct keys with colliding names. E-SAME-NAME then used two full-stack agents named `Same Display Name`: IDs, balances 100/20, sealed-bid permissions, forbidden cancellation, poster/worker task histories, and UI profile rows stayed separate. |
| IDN-04 | PASS | E-UNIT/E-RACE rejected task-URL/payload and action-token replay before mutation. |
| IDN-05 | BLOCKED | This is an A-labelled policy audit. Current wrong-key/startup behavior is testable, but continuity/revocation/migration semantics require an owner decision. |
| IDN-07 | PASS | E-ID exercised same-key/handle and colliding-name registration precedence without exposing private material. |
| AST-01 | PASS | E-CROSS-TASK accepted one independently keyed worker on tasks A and B. Downloading A's asset through B returned 404. The same wrong bytes uploaded freshly to B passed structural admission, were frozen/submitted, then the poster downloaded the exact mismatch and filed a real dispute. This proves mechanical binding and semantic review are distinct. |
| AST-02 | PASS | E-UPLOAD-FREEZE paused an upload after its accepted-state read, committed submission/freeze, then resumed the upload. The late upload was fenced and no orphaned metadata/file appeared; the baseline admitted asset alone remained in the frozen task. |
| AST-03 | PASS | E-UNIT governed-asset assertions preserved distinct asset IDs and content hashes independently of display filename. |
| AST-04 | PASS | E-ASSET-PROJECTION ran the dedicated absolute path, parent traversal, symlink retarget, pinned-root, no-clobber, and prefix-sibling storage tests. |
| AST-05 | PASS | E-UNIT and E-AGENT downloaded accepted inputs and submitted outputs with exact bytes/hashes/roles; inputs did not become outputs or advance delivery. |
| AST-06 | BLOCKED | The desired changed-byte conflict is not the current contract. The retained failing proof `upload-changed-bytes-contract-failure.log` received 201/original asset for changed bytes. `asset_manager.py` and `schemas.py` explicitly promise that the same key/signer/task returns the stored asset **without reading the new body**. `upload-current-contract.log` characterizes that behavior with one stored asset. An owner decision is required between opaque replay keys and content-bound keys before implementation. |
| AST-07 | PASS | The dispute/UI lane's `test_submission_correction_guidance_browser.py` verifies frozen-submission guidance and existing post-submission upload rejection, retained in `disputes/ui-adversarial-additions.log`. The UI directs the user to review/dispute recovery rather than offering unsupported revision. |
| REV-01 | PASS | E-UNIT pins reward 100/bid 60 approval to worker +60 and poster refund 40; exact retry adds no effect. |
| REV-02 | PASS | E-RACE used barriers at the review cutoff and proved one auto-approval/approval winner with one settlement. |
| REV-03 | PASS | E-RACE proved stale validated submission cannot overwrite execution expiry. |
| REV-04 | PASS | E-UNIT expired an accepted task without submission and proved the full escrow refund once. |
| REV-05 | PASS | E-UI-BROWSER clicked Approve, committed its response then lost it, cloned the tab-scoped saved intent into a stale second tab, clicked both visible Try again controls, and asserted all three request bodies/operation IDs were identical. Both tabs converged on approved; balances were `-60/+60`, with one settlement receipt and one `task.approved` event. |
| REV-06 | PASS | E-ASSET-PROJECTION exercised real Gateway absent/confirmed-receipt pending projections and monotonic recovery. E-PENDING-UI rendered the exact shapes: absent receipt showed `SETTLEMENT PENDING` without paid success; confirmed receipt showed `PAID · TASK UPDATE PENDING`; neither exposed feedback controls. E-COURT-NO-FEEDBACK ran all 31 malformed/non-completed ruling-ack cases and proved `submit_platform_feedback` is never called until Task Board returns the exact completed settlement acknowledgment. |
| REV-07 | PASS | E-UNIT pins approval 100/60 and Court 50% of price 61. During this campaign the stale native-operations, Task Board service, UI test-spec, and feature-completeness prose was reconciled to the owner-ratified accepted-price rule; no conflicting settlement oracle remains in the audited sources. |
| ECO-01 | PASS | E-AGENT completed an independently keyed worker earning 200, then posting a 150 input task worked by the former poster; exact balances conserved the initial 500. |
| ECO-02 | PASS | E-THREE-TASK concurrently completed one approved 100/60 task, one no-bid reward-80 expiry, and one reward-120 dispute/rebuttal/mock-50% ruling. Balances were poster 410, workers 60/30 (total 500); exact refunds/payments, terminal API rows, and task-bound approved/expired/ruled events were asserted. |
| ECO-03 | PASS | E-MULTI-JOB-PROCESS started the real worker loop in an owned subprocess with two accepted jobs, killed its process group after task A's committed upload, let shorter-deadline task B expire/refund while offline, then restarted the same keyed journal owner. A adopted its single existing asset and submitted; B had no asset, worker remained unpaid, and poster net cost was only A's still-locked 100. The unit seam separately pins journal/task isolation. |
| ECO-04 | PASS | Parent-owned E-MARKET-BURST ran five workers across five tasks from two posters, preserving task/bid/accepted-worker/payment binding without claiming a fairness guarantee. Its exact retained command/log/hash are in the shared campaign evidence. |

## Findings and next slices

No current-contract product defect was demonstrated by the 36 passing sequences.
New-test oracle/setup mistakes (HTTP 409 versus the contract's 402, receipt-row
shape, Playwright tab/session setup, and fixture account creation) were corrected
before the retained passing runs without product changes. AST-06 exposed a real
contract choice rather than an implementation regression: current opaque upload
keys intentionally return the original asset without reading changed bytes.
IDN-05 likewise remains an explicit key-rotation policy decision. These are the
only two BLOCKED dispositions. This lane added campaign tests and evidence only;
it did not change product source or weaken existing acceptance assertions.
