# ADR: Operator Identity and Genesis — the Q-2 × Q-9 Contract Delta

**Date:** 2026-07-13
**Status:** Accepted (ratified contract delta, 2026-07-13)
**Individual decision records:** `docs/plans/2026-07-10-q2-operator-identity-decision.md` (Q-2),
`docs/plans/2026-07-10-q9-treasury-bootstrap-decision.md` (Q-9)
**Source:** `docs/plans/2026-07-09-target-architecture-and-refactoring-plan.md` §2.3, §2.4, §2.8,
§9 Q-2/Q-9, and the "Ratified contract delta (Q-2 × Q-9 interaction, 2026-07-13)" note (§5.0)

## Context

Q-2 and Q-9 were each answered independently and correctly, but neither one alone specifies where
the 1,000,000-coin genesis balance should land — and answering them independently creates a gap
that only shows up once both land together. This ADR documents that interaction, which neither
individual decision record covers on its own.

- **Q-2** (operator identity): as-built, the UI's `UserAgent` *is* the platform (treasury)
  identity — `AgentFactory.user_agent()` loads the `"platform"` handle, sharing
  `data/keys/platform.key`. Q-2 ratifies splitting this: a distinct `operator` roster identity for
  browser-driven economic actions, leaving the platform key for notary operations only (contract
  co-signing, platform-signed service calls).
- **Q-9** (treasury bootstrap): as-built, the UI mints the 1,000,000-coin treasury at startup
  (tier 4), which means "UI down ⇒ no treasury." Q-9 ratifies moving genesis to an explicit,
  idempotent `just provision` bootstrap step (or extended `fund-feeder` CLI), independent of any
  single service's uptime.

Neither record says *whose account* receives the genesis mint once the UI's UserAgent is no
longer the platform identity. Before Q-2, "mint to platform at UI startup" was self-consistent,
because the UI's identity and the platform's identity were the same account. After Q-2 splits
them, that destination is no longer coherent: platform `credit` is the system's unbounded minting
mechanism (the sovereign issuer, §2.4) and structurally needs no balance of its own, while the
newly separate `operator` account is the one that actually spends money — it must fund the escrow
locks for every task posted through the UI proxy.

## Decision

**The 1,000,000-coin genesis balance is provisioned to the `operator` account**, not the platform
account, via the Q-9 explicit bootstrap step (not at UI startup).

- Platform `credit` mints unboundedly by design (§2.4: "Minting is unbounded by design — platform
  = sovereign issuer, no supply cap") — it needs no starting balance to fund anything.
- The operator (§2.3's Q-2 identity) needs actual funds up front, because it is the account that
  locks escrow when the UI proxy posts a task on the operator's behalf.

§2.4's prior wording, "the `platform` account seeded with 1,000,000 coins," is **superseded** by
this delta; WP-12 updates §2.4 and §2.8 accordingly.

## Consequences

- Bootstrap ordering matters: the `operator` roster identity (Q-2) must exist before the
  provisioning step (Q-9) can fund it — provisioning depends on registration, not the reverse.
- The UI no longer mints anything at startup (Q-9's own consequence); its startup path only needs
  the `operator` account to already have a balance, provisioned out-of-band.
- Platform `credit`/account-creation-with-balance remains the only other money-creation path in
  the system (§2.4); it stays balance-independent by design.
- `services/ui/tests/unit/test_treasury_funding.py`'s prior contract (UI mints treasury; asserts
  `user_agent.treasury_balance`) is superseded and was rewritten under frozen-test exception #10
  (WP-08, 2026-07-13) to assert the new contract: UI startup performs no mint, a zero-balance
  operator at startup is not a failure, and `just provision` owns genesis.
- The eight reconstructed arc42 ADRs under `docs/arc42/` remain undated and are out of scope for
  this documentation sweep; arc42 regeneration is deferred to a later pass.
