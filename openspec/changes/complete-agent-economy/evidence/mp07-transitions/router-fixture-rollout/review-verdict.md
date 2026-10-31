# Independent router-fixture review

Reviewers: `/root/mp07_board_rollout_review` and `/root/mp07_writer_retirement_tests`, in
read-only review lanes separate from the authors of the respective slices.

Final bounded verdicts: **APPROVE** for the router authority/persistence fixture, bid/expiry/gap
reconciliation, Court/router recovery and replay cases, and task/submission/lifecycle conversions.
Parent runs provide the independent execution evidence; reviewers did not start test processes.

The reviews checked:

- One owned Gateway database and explicit actor/key/clock configuration, with real Bank/Court
  writer evidence rather than success-shaped responses.
- Platform fixture signatures bound to the actual configured key; other actors bound to their
  exact registered fixture keys. The impostor-key test failed before this fixture fix.
- A service graph constructed through public constructors, with cancellation/await of each sweep
  before closing its store. All data, logs, assets, keys and maintenance locks are temporary.
- Native request identity/version and poster-window fields supplied before signing, while generic
  signing helpers do not mutate request payloads.
- Source state plus pending ownership on failed or unproven effects; recovery after committed
  Court acknowledgments are lost; exact ruling replay without another payout; changed-term
  conflicts with unchanged storage.
- Exact selected cutoffs, actual receipts/legs/transactions, escrow and balance outcomes, one
  completed operation/event, and no losing effects.
- Negative security/field cases isolated with otherwise-valid required inputs. Multi-error shape
  and state cases remain separate and both assert no writes.
- The insert-collision case refunds Alice's new escrow while preserving Bob's existing task and
  locked escrow; this does not claim same-poster ambiguous posting is fixed.

Findings closed during review: broad verifier exception masking (four genuine fixture reds plus
three dependency controls), stale compatibility acceptance of `completed`, missing escrow operation
IDs in two negative cases, and a weak non-201 assertion replaced by exact `409` plus refund evidence.
The final verifier catches only explicit connection/timeout/JSON-decode failures; unexpected
exceptions and malformed verifier results remain visible test failures.

The first full-suite run additionally found one health-status fixture still missing cancellation
identity/version. Those fields were added before signing; all original status-count assertions
remain intact. A SQL line-wrap lint repair changes no query text or test expectation.

This is not full MP-07 or root-CI approval. Remaining dictionary-store consumers, public producers,
migration/recovery histories, later Core/Vision packages and real Court judging retain their own
required gates.
