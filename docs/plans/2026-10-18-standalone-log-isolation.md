# Standalone fixture log isolation — T-179/T-180

The first Task Board integration repair exposed a shared fixture pattern: `setup_logging` opens
its configured directory relative to the process cwd, without rebasing it to the YAML location.
The isolated root command runner preserves service cwd and cannot protect fixture-local configs.
Ordinary log contents are retained; no test entry is deleted or rewritten out of a mixed log.

## Audited startup configurations

The following additional configurations now select an absolute path below their test root:

| Service | Startup configuration owners | Directory below owned root |
| --- | --- | --- |
| Identity | unit router fixture; integration Identity fixture | logs/identity |
| Central Bank | unit router fixture; two-tier helper; integration Bank fixture | logs/central-bank |
| Reputation | unit router fixture; integration fixture; persistence helper and CFG-01 startup | logs/reputation |
| Court | unit router fixture; integration Court fixture | logs/court |
| Gateway | unit TestClient config; Court integration companion Gateway | logs/db-gateway |
| Task Board | unit router fixture; deadline-configuration lifespan fixture | logs/task-board |

That is fifteen additional configuration sites across thirteen existing files. The prior Board
integration repair covers its own Board/Gateway pair. The newly added proof cases capture generated
configuration before app creation, lifespan, logging, server startup or network activity.

Some configurations were latent behind missing MP-07 authority/actor fields. Their paths are fixed
before that unrelated valid-input reconciliation removes the earlier startup failure. No behavioral
or negative configuration assertion changes as part of this log repair.

## Deliberately distinct cases

Identity/Bank integration companion Gateway helpers, the Bank ledger-store helper and cross-service
gateway_helpers initialize state manually and use ASGITransport without Gateway lifespan. Their
relative logging values do not currently cause log writes. Config-only tests are also separate.
UI startup fixtures, Identity live-subprocess fixtures and IsolatedTestRuntime generated configs
already use owned paths. These are code-path audit findings, not new live-run claims.

## Evidence and completion boundary

The original two configuration proofs and fifteen additional proofs fail on the old paths and pass
on the owned paths; the parent independently reran all seventeen after repair. Existing assertions
are unchanged. The corrected Board live tranche previously passed37 cases while all41 observed
ordinary Board log hashes stayed identical. Broader live unchanged-log checks and the fresh root
gate are still required before T-179/T-180 close. The full Core/Vision goal remains unchanged.
