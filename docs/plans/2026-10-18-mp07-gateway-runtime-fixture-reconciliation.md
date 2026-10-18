# MP-07 Gateway runtime schema fixture reconciliation

Full Gateway unit discovery ended925 passed /214 failed /19 errors; raw output is archived under
mp07-transitions/gateway-full-discovery. This is a discovery gate, not a completion claim. Confirmed
families include old generic-write expectations, absent publication windows, directly seeded task
rows without authoritative contracts, and historical v2 fixtures opened as current runtime.

The first narrow repair covers three runtime-admission regression files, whose original sources
are preserved under schema-fixture-reconciliation/baseline. Current-runtime health and receipt
admission proofs must construct frozen v3. Keep corruption injection, nonmutation, closed-connection,
journal, exact error details, receipt-cutover and rollback assertions intact. Preserve frozen v1/v2
catalog and migration tests; do not automatically migrate old state in DbWriter. Extend the explicit
older-schema rejection control to both v1 and v2. For the future-version classification control,
use4 now that3 is the current contract. Retain the frozen v1 shape of the semantic-corruption cases.

The v3 schema still has the unchanged receipt-v2 cutover contract: fresh_v2 remains its correct
financial cutover label. No receipt provenance is rewritten or inferred. This is test contract
reconciliation under the reviewed v3 runtime amendment, not a relaxation of startup safety.

Run the three files plus MP07 runtime-schema and MP04 frozen-v2 schema controls. Other Gateway
failure families remain open for individually reviewed reconciliation or product diagnosis.
