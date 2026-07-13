# Observatory Service — Production Release Test Specification

**SUPERSEDED — "Observatory" was the old (React-era) name/design for this service. It was
renamed to `services/ui` on port 8008 per ratified decision R6/T-025
(`docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §3 R6; completion-backlog
T-025). The live test specification now lives in `ui-service-tests.md`. There is no running
"observatory" service — do not cite this document as describing anything currently deployed.**

## Historical context

This document originally described the release-gate test suite for a planned read-only
"Observatory" service with a React frontend, which never shipped past design docs. What was
actually built at `services/ui` has a different backend shape (vanilla JS, an operator write
path via `/api/proxy/*`) and its own test spec.

Kept for historical reference only. Do not treat any scenario, endpoint, or status/error code
below as current.
