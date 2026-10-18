# MP-07 runtime fixture reconciliation

The first parent Agents unit discovery after caller/startup integration produced657 passes and51
failures. Most old fixtures instantiate loops without the now-required command coordinator or omit
native task inputs. They are not evidence that the original business assertions failed.

## Worker factory and financial posting

The old valid WorkerFactory YAML fixture lacks runtime_state. Add explicit environment, owned
temporary directory, diagnostic and record limits. Retain all profile ordering, missing/empty profile,
unknown roster handle, unknown selected profile and agent-name assertions. The old `bundle.loop`
expectation is superseded by delayed construction after registration and journal replay. Replace that
single expectation with the returned worker configuration, actual profile and runtime configuration;
retain agent/client bundle checks. Close the two real test clients in async finally blocks.

The MP-04 task-posting proof predates the required acceptance window. Add one explicit test duration
to its SDK call; preserve its original escrow operation identity, task binding and signature assertions.
This does not change the completed financial-receipt contract or add a production fallback.

Original source copies are `/private/tmp/agent-economy-mp07-runtime-worker-factory-tests-before.py`
and `...runtime-financial-sdk-tests-before.py`. The full discovery output is retained at
`/private/tmp/agent-economy-mp07-runtime-agent-units-discovery.log`. Loop unit and E2E fixture
reconciliation remain separate pending work; no broad Agents or root completion is claimed here.
