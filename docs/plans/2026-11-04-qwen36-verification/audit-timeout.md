# Audit request timeout — 2026-11-04

Two root attempts failed at Central Bank's unchanged vulnerability audit with
PyPI ReadTimeout at the auditor's implicit15-second socket timeout. A direct
retry passed, but another root attempt encountered the same transient failure.

The installed pip-audit CLI explicitly supports --timeout (default15). A normal
scan with --timeout60 passed. All seven identical service recipes now state that
request bound explicitly; cache settings, dependency coverage, the existing
advisory policy, and nonzero failure propagation are unchanged.

A later recipe invocation still timed out at60 seconds. Therefore timeout length
alone is not claimed as the network root cause or a guarantee against outages.
Direct PyPI HTTPS subsequently returned200 in0.344seconds. A diagnostic audit and
then the actual recipe with supported PIP_AUDIT_LOGLEVEL=DEBUG both passed.
Final root CI retains that logging so any recurrence identifies the package.

Verification: project structure and existing identical-recipe test passed;
independent review approved the bounded configuration diff. No advisory was
newly ignored, no check was skipped, no dependency changed, and PATH was preserved.
Raw initial failures and successful direct scan are retained as lossless gzip
artifacts with hashes in audit-log-integrity.json.

Final run: PIP_AUDIT_LOGLEVEL=DEBUG just ci-quiet from the repository root.
Source manifest: final-root-source.json (1497 inputs).
Output: /tmp/agent-economy-qwen36-final-root.log.
Exit file: /tmp/agent-economy-qwen36-final-root.exit.
The process is running; no final root pass is claimed yet.
Active final-root execution handle:5735. It has passed the previously failing
Central Bank audit and reached Task Board. Re-poll that exact process; no root
pass is claimed until its terminal exit status is available.

## Terminal verification

Final-root exec5735 completed with exit0 at2026-11-04T12:37:43UTC.
`final-root-result.json` records the outcome and unchanged1497-input check.
`final-root.log.gz` retains the complete output losslessly. All configured checks
passed; no new advisory exclusion, dependency change, or failure suppression was
used. The remaining release step is publication outside the prohibited Git hours.
