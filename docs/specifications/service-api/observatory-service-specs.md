# Observatory Service — API Specification

**SUPERSEDED — "Observatory" was the old (React-era) name/design for this service. It was
renamed to `services/ui` on port 8008 per ratified decision R6/T-025
(`docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §3 R6; completion-backlog
T-025). The live contract now lives in `ui-service-specs.md`. There is no running "observatory"
service — do not cite this document as describing anything currently deployed.**

## Historical context

This document originally described a planned "Observatory" service: a React + Vite
single-page frontend on a different port, paired with a read-only FastAPI backend, that never
shipped past design docs. What was actually built at `services/ui` is a vanilla-JS,
server-rendered FastAPI application on port 8008 with both a read side (direct read-only
SQLite, the "observatory pattern") and a write side (`/api/proxy/*` via a dedicated operator
identity) — a materially different design from what this file describes.

Kept for historical reference only. Do not treat any endpoint, port, or architecture claim
below as current.
