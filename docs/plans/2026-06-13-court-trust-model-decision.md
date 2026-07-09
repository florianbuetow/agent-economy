# Decision Record: Court Trust Model (T-012)

**Date:** 2026-06-13 (ratified by Florian 2026-06-12)
**Status:** DECIDED — platform-signed only
**Shapes:** T-011 (Court mixin fix + `submit_rebuttal`), T-013 (dispute → Court handoff), T-015 (Court in the demo)

## Context

The Court service accepts three write operations: file a dispute, submit a rebuttal, and
trigger a ruling. The question was who may sign these requests:

- **Option A — platform-signed only:** every Court write carries a JWS token signed by the
  platform agent. Agents never talk to the Court directly; the Task Board (as the platform)
  mediates filings and worker rebuttals after doing agent authentication and authorization
  itself.
- **Option B — agent-signed rebuttals:** the Court additionally accepts agent-signed tokens
  on the rebuttal endpoint (and possibly on filing), verifying the signer against the agent
  roster and checking the signer is a party to the dispute.

The agents library currently contradicts the implemented Court API: `CourtMixin.file_claim()`
signs with the agent key and omits `respondent_id`/`escrow_id`
(`agents/src/base_agent/mixins/court.py`), and no `submit_rebuttal()` exists — this is the
T-011 bug that forced the decision.

## Decision

**Option A: keep all Court write operations platform-signed only. The platform mediates.**

Concretely:

- `POST /disputes/file`, `POST /disputes/{dispute_id}/rebuttal`, and
  `POST /disputes/{dispute_id}/rule` require a JWS token signed by the platform agent
  (`kid == settings.platform.agent_id`); no agent-signed tokens are accepted.
- The Task Board auto-files Court claims on dispute via a platform-signed service call
  (ratified separately under T-013).
- Worker rebuttals reach the Court via a platform-signed path: the platform verifies the
  worker's identity/authorization at its own layer, then forwards the rebuttal with a
  platform-signed token (implementation lands with T-011/T-013).
- All Court reads remain public.

## Rejected Alternative

**Option B — agent-signed rebuttals on the Court endpoint.** Rejected because:

- The Court would need the full agent roster plus party-authorization logic ("is this signer
  the respondent of this dispute?"), duplicating the authorization layer the Task Board
  already owns.
- It widens the attack surface: agents could hit Court write endpoints directly (fraudulent
  filings, rebuttals for disputes they are not party to, premature rulings).
- It contradicts both the implemented code and the published auth spec (see Alignment), so it
  would be the higher-churn option with no compensating benefit.

## Rationale

- **Matches the implemented Court API:** both write handlers verify the platform token and an
  exact action name (`verify_platform_token` + `require_action("file_dispute")` /
  `require_action("submit_rebuttal")` in
  `services/court/src/court_service/routers/disputes.py`).
- **Matches the published auth spec:** `docs/specifications/service-api/court-service-auth-specs.md`
  states "all write operations are platform-signed" (line 5), lists filing, rebuttal, and
  ruling in the platform-signed operations table (lines 19–25), and says "no agent-signed
  tokens are accepted" (lines 67, 148).
- **Single verification identity:** the Court verifies one key (the platform agent), keeping
  its authentication model the simplest in the system.
- **Centralized dispute orchestration:** business rules (task must be disputed, rebuttal
  window timing) stay enforced in one place, at the Task Board layer.

## Consequences

- T-011: `CourtMixin` must stop signing Court requests with the agent key; rebuttals are
  submitted through the platform-signed path, and the claim payload gains
  `respondent_id`/`escrow_id`.
- T-013: `POST /tasks/{id}/dispute` on the Task Board auto-files the Court claim via a
  platform-signed service call.
- T-015: demo Court actions are driven through the platform, not via agent-signed calls.
