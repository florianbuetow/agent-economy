# MP-07 asset and deadline-precision reconciliation

The untouched combined run collected 18 tests. All 13 AssetManager tests passed, but their accepted
tasks were arbitrary dictionaries in the retired in-memory store rather than histories admitted by
the native Gateway contract. Three of five deadline-precision tests failed from fixture drift:
the create payload omitted `acceptance_window_seconds`, and the evaluator fixture omitted the now
required platform and deadline-actor authority. The two pure formatting controls passed. None of
those three failures demonstrated lost timestamp precision in current production behavior.

The AssetManager tests retain their original content, SHA-256, size limit, asset-count limit, task
existence, worker ownership, listing, download, path-traversal, metadata, and count assertions. Each
task now lives in an owned exact-v3 Gateway store. Accepted tasks are reached through real published
terms, native bid, and native acceptance operations, freezing the worker, reward, bid amount, and
execution cutoff before asset work begins. Missing and open states are also explicit Gateway facts;
no accepted dictionary is fabricated.

The precision tests keep the original `00.250000Z` creation fraction and derived
`01.250000Z` bidding cutoff. The HTTP payload proof now supplies the explicit acceptance window.
The before/equality scenarios use the approved native task fixture with an explicit deadline actor,
one integer-microsecond Gateway clock, deterministic deadline operation identity, real Bank receipt,
ledger transaction, escrow resolution, restored poster balance, and single Board expiry event.
One microsecond before cutoff no operation or financial effect exists; equality completes expiry.

The shared `gateway_task_store.py` and `native_task_fixture.py` helpers are consumed read-only. Test
logs and final source hashes are retained under
`/private/tmp/agent-economy-mp07-asset-precision-*`. This reconciliation changes no production code
and does not broaden asset immutability or quota policy beyond the existing assertions.
