# MP-12 / T-132 red proof

The parent independently ran the new UI integration suite before production edits:

```sh
# From services/ui
UV_CACHE_DIR=/tmp/agent-economy-review-uv UV_FROZEN=1 \
uv run pytest tests/integration/test_mp12_sealed_projections.py -q
```

Result: **12 intended failures, 3 passing controls**, no collection or setup errors. The retained
`agent-economy-mp12-parent-red.log` demonstrates public open-bid detail leakage, stale post-open bid
representation without canonical integer amount, missing authorized poster/nonposter proxy behavior,
open-bid identity/amount leakage through history and initial/resumed SSE, sealed feedback leakage
through the same event paths, and bid participation leakage through poster/bidder feed membership
before pagination. The missing proxy currently falls through to the SPA as HTML; it is not described
as an observed 404.

Controls cover unrelated-agent exclusion, post-open bid-event visibility and unchanged public quality
projections while feedback is sealed. The quality control directly changes disposable database
visibility rows and adds a revealed event; it proves projection behavior, not the real mutual-reveal
service or timeout scheduler. Those later MP-13 obligations remain open.

SSE waits have a three-second timeout. Ruff formatting and lint checks passed. No existing assertions
or production files were changed during red proof. Implementation has been assigned separately;
task 14.1 is verified. T-132/T-133 remain open until the required fresh integrated root gate.

## Implemented and independently reviewed

Public task drilldown now returns aggregate bid count without open bid rows. Poster-authorized
reads go through the signed Task Board SDK, preserve 403 responses, and return private/no-store
responses enriched with public bidder names and visible quality. The browser preserves public
count and correct phase for observers, and has explicit poster loading/failure states with no
accept action when authorized details are unavailable.

One projector sanitizes legacy and newly produced event history/SSE. Actor filters and agent feeds
exclude sealed participation before pagination. Private stored bid attribution survives for post-open
history, including losing bidders. Feedback submission events disclose no sealed quality content.

The first implementation review found four defects (count/phase loss, silent fetch failures,
destroyed actor attribution and lost name/quality display). Behavioral red logs preceded their
repairs. Full re-review approved the repaired source with zero unresolved findings.

The parent reconciled the existing drilldown test to exact canonical 83/91 integer amounts while
keeping bidder-name/timestamp checks. Four existing browser detail tests now use an explicit poster
fixture; all nine cases still assert public `bids=[]` and count 1. The five observer cases remain
unstubbed. The four poster cases retain nonempty rows, Bob's name, quality and exact offered amount.
They test rendering, not signed authorization. Final independent handoff review approved these
changes and the router's suppression-free runtime annotation handling.

Parent verification:

- UI component `just ci-quiet`: exit 0. The first run found a prohibited typing suppression; removing
  deferred annotations followed the existing FastAPI router pattern, then the entire gate passed.
- Full UI integration: **246 passed, zero deselections**.
- Combined existing/new browser contract cases: **16 passed**.
- New actual signed seven-service proof: **2 passed**. The operator reads its own sealed bids with
  exact names/amounts, another poster's bids return 403 without leakage, and acceptance reveals the
  losing bidder's actor-filtered history/feed membership. This proof uses real services and signatures,
  not browser response stubs.
- The live proof's ten service/bootstrap/command groups were drained and absent in a fresh host
  process inventory; `live-proxy-runtime.json` and `live-proxy-cleanup.json` retain the evidence.

Source remains frozen pending the combined root gate. No Git mutation occurred. The direct visibility
aggregate control still does not establish MP-13's actual mutual/timeout reveal service behavior.
