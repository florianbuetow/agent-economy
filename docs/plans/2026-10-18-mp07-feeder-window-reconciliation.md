# MP-07 feeder publication window

T-181/T-182 includes forwarding the poster-selected `acceptance_window_seconds`. The SDK already
requires this keyword. The feeder omitted it, and its config rejected it as an unknown field.

New acceptance tests first failed 14 cases: the absent config field prevented validation/forwarding,
and an omitted field was accepted. These are not fourteen independent product defects. After adding
only the required strict positive SQLite-safe duration, thirteen cases passed and the forwarding
assertion failed on the exact missing keyword. Forwarding the configured value then passed all14.
Logs and original test source: `/private/tmp/agent-economy-mp07-feeder-window-*`.

Four existing unit fixtures and one unattended E2E fixture create valid feeder configuration. Their
old contract predates the required window. Focused original unit tests now showed six missing-field
failures and nine passes. Add explicit `acceptance_window_seconds: 120` to those valid fixtures only;
preserve their status, reward, ordering, extra-field and lifecycle assertions. The original files are
copied to `/private/tmp/agent-economy-mp07-feeder-window-fixtures/`. This value is test input, never a
production fallback. The shipping feeder YAML explicitly chooses120; the new test uses73 to prove
the code forwards the chosen value rather than substituting the configured bidding or review time.

This does not implement the later MP-18 selection policy, posting identity/recovery, or durable oracle
provenance. Existing selection delay/quorum behavior is unchanged. No full agents/root CI or E2E
completion is claimed by these focused checks.

Final focused controls pass29 and independent review approves. Scoped Mypy passed; Pyright exposed
the pre-existing `_FileSettings(**raw)` argument-type diagnostic, reproduced on the preserved original
source. Equivalent `_FileSettings.model_validate(raw)` removes that diagnostic without changing field
admission. The combined config/window run passes93 with scoped Pyright clean; independent review also
approves this validation-equivalent repair. No existing assertion was removed or relaxed.
