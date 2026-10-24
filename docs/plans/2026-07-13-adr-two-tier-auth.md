# ADR: Two-Tier Verification Model (Local Platform vs. Identity HTTP)

**Date:** 2026-07-13
**Status:** Accepted (R2 refinement — `openspec/specs/delivery-governance/spec.md § Platform auth model`, T-021)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.3

## Context

Every mutating call in the system is Ed25519/JWS-signed. Two different classes of signer exist:
ordinary agents (workers, posters, the UI operator) and the platform itself (notary operations —
contract co-signing, ruling settlement, court writes). Prior to this decision there was a
long-standing spec conflict ("local vs Identity-HTTP", June C2) over where signature verification
should happen, and no single rule scoped which class used which path.

## Decision

**Two-tier verification, scoped by who signed the operation:**

- **Agent-signed operations** — task create/cancel, bid, accept, asset upload, submit, approve,
  dispute, rebuttal-to-Task-Board, `escrow_lock`, balance/transaction reads, feedback submission,
  self-service zero-balance account creation — are verified via Identity's spec'd
  `POST /agents/verify-jws`, through the shared `service_clients.IdentityClient`.
- **Platform-signed operations** — account creation with non-zero balance, `credit`, escrow
  release/split, Task Board `record_ruling`, all Court writes, court-generated feedback — are
  verified **locally** via `PlatformAgent.validate_certificate()` (the pattern Court already used).
  No Identity round trip; this class of operation survives Identity outages. Central Bank,
  Reputation, and Task Board migrate their platform-op checks to this path (T-021), and the
  spec'd-but-never-called raw `POST /agents/verify` endpoint is retired.

## Accepted deviation

**Central Bank's `create_account` keeps Identity-path verification for both of its modes**
(self-service zero-balance *and* platform-funded), rather than moving the platform-funded mode to
local verification like every other platform-signed operation.

Rationale: `create_account` has a hard Identity dependency regardless of signer class, because it
must check agent existence via Identity's `get_agent`. Local platform verification would therefore
buy no outage resilience here — Identity is already a hard dependency of the call — and
distinguishing the two modes (zero-balance vs. funded) requires the verified signer identity
either way. This is the one documented exception to the two-tier split; §2.3's platform-op list
and the T-021 grep proof carry it explicitly.

## Consequences

- Central Bank, Reputation, and Task Board no longer make a remote Identity call for
  platform-signed operations except `create_account`; the remote `verify_jws_path`/`get_agent_path`
  config for those services is removed everywhere else.
- Court's existing local-verification pattern becomes the template all platform-op checks migrate
  to (T-021).
- `create_account`'s exception is revisited only if the agent-existence check ever moves off
  Identity (e.g., to a gateway read) — at that point it should be re-evaluated for local
  verification like its siblings.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
