# MP-07 financial-history fixture runtime admission

The MP04 legacy-observation unit suite currently creates verified schema v2 then opens DbWriter,
which correctly rejects old runtime catalogs now. Full Gateway discovery shows25 failures and7
setup errors in this file, all at that runtime admission boundary; the money assertions have not
run. Preserve the frozen v1/v2 migration tests and the entire legacy financial assertion matrix.

Prepare the disposable shared historical task fixture with explicit valid timing/accepted linkage
before the v2 receipt cutover, following the existing MP07 migration contract fixture. Only Task
Board/Court/assets placeholder inputs change; no Bank row, event, receipt timestamp, identifier,
resolution metadata, or balance is normalized. In particular all deliberate legacy empty/null/non-UTC
financial fields remain untouched. Assert financial rows/events identical across fixture preparation.

After the existing explicit v1-to-v2 migration, create and verify a distinct v2 backup, then invoke
explicit v2-to-v3 migration. Assert every Bank row/event/cutover/legacy marker unchanged across that
migration and exact v3 admission. Do not delete the historical task/Court graph, use a bypassed writer,
or infer any native Board operation provenance. Native history stays empty at cutover.

Run all original observation cases and frozen-v2 migration/MP07 migration controls. Any newly exposed
failure is investigated separately; do not normalize historical money fields to obtain green results.
This is fixture reconciliation, not a change to runtime migration or legacy financial semantics.
