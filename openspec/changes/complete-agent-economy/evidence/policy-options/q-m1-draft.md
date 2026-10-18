# Q-M1 / T-162 owner decision draft: evidence and artifact privacy

**Status: DRAFT — no option or policy choice below is accepted.** Owner acceptance or revision is required before an ADR, specification, test, configuration, or product change.

## Existing constraints (not new choices)

- Q-2/Q-3 retain one local operator on a loopback-only UI. Q-13 requires three real production judges, median aggregation, and the ambiguity-favors-worker rubric. Q-15 keeps dispute fees, zero-balance restrictions, and contract caps out of v1.
- Task Board currently accepts ten 10 MiB worker uploads and exposes their list/download routes publicly. Court reads at most 65,536 aggregate bytes, in listing order, decodes every file as UTF-8 with replacement, and gives judges no filename, media type, hash, or truncation marker.
- The accepted design requires immutable manifests and evidence, recoverable pending disputes, and no financial ruling that pretends omitted evidence was considered.

## Policy alternatives — all PROPOSED

1. **PROPOSED A — public synthetic economy.** Inputs and outputs are public; automatic judgment uses the bounded envelope below. Simple, but unsuitable for confidential work and irreversible after publication.
2. **PROPOSED B — participant-private, selectable model boundary (recommended).** Public views show task metadata and a redacted manifest. Bidders request audited pre-bid input access, granted by the poster. Outputs stay with participants and Court. Every task declares local-only or external-model-eligible before bidding.
3. **PROPOSED C — local-only private economy.** Use B's access rules, but evidence reaches only locally operated real models. Core/Vision readiness then requires a measured local three-judge profile meeting MP-25. Mock profiles remain limited to isolated development/CI and do not prove real adjudication readiness.

## PROPOSED recommended starting profile (B)

### PROPOSED supported-format/admissibility table

| Artifact | PROPOSED treatment |
|---|---|
| UTF-8 plain text, Markdown, allowlisted source-text media types | Automatic only when the complete evidence bundle fits the 64 KiB aggregate limit |
| UTF-8 JSON | Automatic when syntactically valid and within the same aggregate limit |
| UTF-8 CSV | Automatic when parseable under a declared delimiter/header contract and within the same limit |
| PDF, office files, images/audio/video, archives, executables, encrypted/malformed files, or overflow | May be staged privately, but cannot be attached as task evidence to this automatic profile |

**PROPOSED:** freeze media type, parser result, byte size, SHA-256, role, uploader, task/revision identity, and completeness status before judging. Extension or caller-declared MIME alone is insufficient. An activated three-judge profile must process the entire admitted bundle within its configured context; otherwise it is ineligible for this envelope.

**PROPOSED:** the limit covers required inputs, outputs, specification, claim and rebuttal together. Publish explicit per-field/output reservations with the profile and validate their maximum total before bidding. Do not admit a task whose declared evidence cannot fit, or silently truncate a later submission. Model context overhead is budgeted separately and tested for every judge profile.

### PROPOSED access matrix

| Actor | Before bid | After acceptance/submission | Terminal |
|---|---|---|---|
| Public | Public title/spec metadata; artifact count/categories; no names, hashes, or bytes | Lifecycle, payout and typed outcome metadata; no private evidence quotations or free-form ruling text | Same unless explicitly published |
| Registered bidder | Full frozen inputs after poster approval of a signed request recording agent, task, manifest hash, time, and model mode | Losing bidder's service access ends at acceptance; no output access | Previously downloaded copies cannot be revoked |
| Poster/operator | Own inputs and access audit | All outputs, claim, rebuttal, ruling | Bytes until expiry; audit remains |
| Assigned worker | Inputs and own outputs | Claim, rebuttal, ruling | Bytes until expiry; audit remains |
| Court/judges | None | One frozen admissible bundle | Bundle hash, votes, ruling; bytes follow retention |

**PROPOSED:** publishing inputs requires poster consent; publishing outputs requires poster and uploader consent. Local single-operator deployment does not make artifacts public.

### PROPOSED refusal, blocked judgment, and appeal behavior

- Reject automatic-adjudication tasks before bidding when declared required evidence is outside the envelope, returning exact format/size reasons. This creates no implied human-arbitration path.
- Missing bytes, hash mismatch, parser failure, incomplete representation, judge unavailability, or consent mismatch after acceptance leaves the dispute recoverably `evidence_blocked`/pending with escrow unchanged. Never substitute a mock, default, empty, truncated, or partial-evidence ruling.
- Poster approval remains available while the task is submitted. Once disputed, existing approval is not a bypass for a blocked Court attempt; identical-byte recovery must restore the evidence. Any mutual settlement or human arbitration route requires a separate accepted contract.
- Do not re-run a final ruling for a merits appeal. Before ruling, integrity recovery may restore only identical hashed bytes. Post-ruling corruption is an operational incident against the immutable audit record.

### PROPOSED retention, deletion, and model-data consent

- Keep raw evidence until 90 days after both terminal task status and confirmed financial settlement (start from the later timestamp). Revoke ordinary access at expiry; purge eligible live copies on the next maintenance run and expire recovery copies within 30 more days. Preserve unresolved-task evidence. A restore reapplies deletion tombstones before serving bytes; an offline process cannot promise scheduled physical erasure while stopped.
- Keep manifests/hashes, accepted terms, access/consent receipts, participant IDs, evidence-bundle hash, votes, ruling, payout, and ledger/operation receipts as long as the financial ledger. Deletion may remove eligible bytes and redact display names, but not this audit chain.
- Model mode is frozen before bidding. Poster publication and each signed input-access request/bid acknowledge that mode before private bytes or binding work. A bidder declining the external mode must not bid; changing the task to local-only requires new terms before bidding, not a switch during dispute. Approved clients must obey the declared boundary. Access control cannot prevent an authorized recipient from copying or exporting downloaded content outside the platform.
- External execution is opt-in, credentialed, budget-bounded, and records profile identifier plus evidence hash. No provider retention, training, residency, or deletion capability is assumed. The operator must record applicable terms before enabling a profile; absent or unacceptable terms keep it disabled and disputes pending.

## PROPOSED worked examples

1. A 12 KiB CSV input and 20 KiB JSON output, plus all text fields, fit 64 KiB. An audited bidder sees local-only mode; the three real judges receive the same complete hashed bundle.
2. A task requires a 2 MiB PDF. It may store the file, but cannot enter automatic adjudication until reformulated with admitted evidence or a later extraction/arbitration policy is accepted.
3. An admitted artifact is missing during dispute. Court records a blocked attempt, leaves escrow untouched, and retries retrieval; it never rules from an empty string or prefix.

## Owner decisions still required

- Choose A, B, or C; confirm the 64 KiB complete-bundle limit and format allowlist.
- Confirm poster-approved pre-bid access, or explicitly choose access for every authenticated requester instead.
- Confirm the 90+30-day deletion schedule, no-merits-appeal rule, and stated publication/external-model consents.
