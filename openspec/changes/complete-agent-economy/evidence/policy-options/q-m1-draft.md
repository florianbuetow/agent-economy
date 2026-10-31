# Q-M1 / T-162 owner decision draft: evidence and artifact privacy

**Status: historical alternatives, superseded where decided by the owner on
2026-10-22.** See [the accepted decision record](owner-decisions-2026-10-22.md)
for public access, configurable OpenAI-compatible Court providers, initial
text/source/JSON/CSV evidence, immediate terminal-job download closure and
archiving after more than three days. Other draft values are not accepted.
The existing Court specification already excludes appeals; that is not an open
choice. Do not apply the private-access, provider-approval or deletion proposals
below as additional owner requirements.

## Existing constraints (not new choices)

- Q-2/Q-3 retain one local operator on a loopback-only UI. Q-13 requires three real production judges, median aggregation, and the ambiguity-favors-worker rubric. Q-15 keeps dispute fees, zero-balance restrictions, and contract caps out of v1.
- Task Board currently accepts ten 10 MiB worker uploads and exposes their list/download routes publicly. Court reads at most 65,536 aggregate bytes, in listing order, decodes every file as UTF-8 with replacement, and gives judges no filename, media type, hash, or truncation marker. These are baseline defects, not accepted policy.
- The accepted design requires immutable manifests and evidence, recoverable pending disputes, and no financial ruling that pretends omitted evidence was considered.

## Universal requirements for every option

Whichever choices the owner makes, the resulting policy must preserve all of these requirements:

- Freeze the accepted specification, artifact manifest, evidence roles, uploader and task/revision identities, verified media type and parser result, byte sizes, SHA-256 hashes, completeness status, and consent/access receipts. Extension or caller-declared MIME alone is insufficient provenance.
- Give every judge the same complete, immutable evidence bundle. Never omit, reorder without a recorded canonical rule, silently truncate, substitute, summarize, or default missing evidence. Untrusted artifact content remains data rather than instructions or executable code.
- Missing bytes, a hash or parser mismatch, an incomplete representation, unavailable judges, an ineligible provider, or a consent mismatch leaves adjudication recoverably blocked with escrow unchanged. Never substitute a mock, empty, partial-evidence, or default financial ruling.
- Preserve a durable audit chain for accepted terms, manifests and hashes, access and consent receipts, evidence-bundle identity, votes, ruling, payout, and ledger/operation receipts. Byte deletion or display-name redaction must not erase financial or adjudication provenance.
- Record and approve the retention, training, residency, disclosure, and deletion terms for every enabled model provider or operator, including a local provider where those terms or controls apply. An absent, incompatible, or expired provider assessment keeps that profile disabled and affected disputes pending.
- Freeze the permitted model boundary, evidence profile, access policy, and publication policy before bidding. A later outage or refusal cannot silently change those accepted terms.
- Treat a final ruling as immutable under the proposed no-merits-appeal rule. Before final ruling, integrity recovery may restore only identical hashed bytes. If the owner wants merits appeals, a separate ruling-version and supersession contract must be accepted before implementation.

## Axis 1 — full-specification, input, and output visibility

| Choice | Public view | Bidder/participant access | Consequence |
| --- | --- | --- | --- |
| **V-A. Public full record** | Full specification, inputs, outputs, claim, rebuttal, and the publication-safe ruling record are public. | No private bidder gate is needed for published bytes. | Best suited to a synthetic public economy; unsuitable for confidential work. Public downloads cannot be recalled. |
| **V-B. Private full record with public discovery summary — proposed recommendation** | Only a poster-authored discovery summary and structural metadata are public: task category, declared artifact types/counts/sizes, evidence profile, model boundary, lifecycle, payout, and typed outcome. The full specification, filenames, hashes, bytes, private quotations, and free-form ruling text remain private. | A registered bidder receives the frozen full specification and inputs only after poster approval of a signed access request. The assigned worker, poster/operator, and Court receive the role-appropriate private record. | Supports confidential work while allowing public discovery; adds audited authorization and revocation boundaries. |
| **V-C. Private full record with open authenticated bidder access** | Same public discovery summary as V-B. | Every authenticated bidder who accepts the frozen task terms may retrieve the full specification and inputs without per-request poster approval. | Easier discovery than V-B, but discloses private inputs to a wider authenticated population. |

Under V-A, the poster must freeze consent to publish the specification, inputs, and any later claim before bidding. A worker must freeze consent to publish its outputs and any later rebuttal in its binding bid or acceptance contract before uploading them. Both consents identify the manifest and publication policy. Missing consent prevents publication and acceptance under V-A; it does not trigger a private fallback. Revoking service access cannot revoke bytes already downloaded or copied from a public endpoint.

Under V-B or V-C, publishing an input later requires the poster's separate consent, and publishing a worker output requires both poster and uploader consent. Authorized recipients may retain or export bytes they already downloaded; access control cannot technically recall those copies. Local single-operator deployment does not itself make artifacts public.

### Exact proposed public projection

These fields are part of the visibility choice, not implicit permissions to expose arbitrary database columns:

| Field | V-A | V-B / V-C |
| --- | --- | --- |
| Task ID, poster agent ID, assigned worker agent ID, public agent display names | Public; the worker is shown only after assignment, never as a sealed contender | Public under this proposal, with the same sealed-contender restriction; private artifacts do not imply an anonymous task |
| Discovery summary, category, declared types/counts/sizes, frozen policy/profile IDs | Public | Public |
| Lifecycle status, deadlines, posted reward, confirmed payout/refund amounts, typed terminal reason | Public | Public |
| Full specification, artifact names/hashes/bytes, claim and rebuttal | Public with the frozen consents above | Authorized private audiences only |
| Ruling ID, final median payout/specification-quality/delivery-quality values | Public after final ruling | Public after final ruling; no source text |
| Three judge identifiers, model/provider/profile version identifiers | Public profile metadata; never credentials, host paths, or account identifiers | Same public profile metadata |
| Each judge's structured vote values and rubric explanations; final free-form ruling summary | Public after final ruling under V-A consent; this is the entire meaning of the public ruling record, not hidden reasoning or a model transcript | Authorized private audiences only |

Public visibility does not include sealed bid identities/amounts, unrevealed peer feedback, credentials, internal filesystem paths, or hidden model reasoning. If anonymous task parties are desired, that is a revision to the proposed visibility options and must be selected explicitly.

Q-M3's separate P-C option deliberately adds public revealed task/counterparty scoring provenance. Selecting P-C together with V-B or V-C makes those relationships and quality observations public while artifact bytes remain private. P-A does not add that source-level disclosure. The owner must choose both axes explicitly.

### Proposed V-B access matrix

| Actor | Before bid | After acceptance/submission | Terminal |
| --- | --- | --- | --- |
| Public | Discovery summary and structural metadata only | Lifecycle, payout, and typed outcome metadata; no full specification, filenames, hashes, bytes, private quotations, or free-form ruling text | Same unless separately published with the required frozen consent |
| Registered bidder | Full frozen specification and inputs after poster approval of a signed request recording agent, task, manifest hash, time, evidence profile, and model boundary | Losing bidder's service access ends at acceptance; no output access | Previously downloaded copies cannot be revoked |
| Poster/operator | Own specification, inputs, and access audit | All outputs, claim, rebuttal, and ruling | Bytes until retention expiry; audit remains |
| Assigned worker | Frozen specification and inputs; own bid | Own outputs plus claim, rebuttal, and ruling | Bytes until retention expiry; audit remains |
| Court/judges | None | One frozen complete admissible bundle | Bundle hash, votes, and ruling remain; bytes follow retention |

## Axis 2 — model execution boundary

| Choice | Permitted judges | Release implication |
| --- | --- | --- |
| **M-A. Task-selectable local or external eligibility — proposed recommendation** | Each task declares `local_only` or an allowlisted `external_model_eligible` profile before bidding. | Release requires a measured three-judge profile for every advertised mode. Bidders acknowledge the selected mode before private access or a binding bid. |
| **M-B. Local-only** | Evidence reaches only locally operated real models. | Core/Vision readiness requires a measured local three-judge profile meeting MP-25. Mock profiles remain limited to isolated development/CI and do not prove real adjudication readiness. |
| **M-C. External-eligible only** | Evidence may reach only the explicitly named, assessed external profiles accepted in the task terms. | Tasks cannot fall back to a local or different external profile after bidding. Unavailable or unacceptable provider terms leave adjudication pending. |

External execution remains opt-in, credentialed, budget-bounded, and records the exact profile identifier plus evidence hash. No provider retention, training, residency, or deletion capability is assumed. The operator records applicable terms before enabling a profile and disables it when the assessment is absent, incompatible, or expired.

## Axis 3 — evidence capability required for release

| Choice | Release scope | Unsupported evidence outcome |
| --- | --- | --- |
| **E-A. Bounded native envelope — proposed recommendation for the first release** | Automatic adjudication supports the complete text/source/JSON/CSV bundle described below. Tasks whose declared required evidence cannot fit are rejected before bidding. | Unsupported evidence may be stored under the chosen visibility policy, but it cannot be attached as adjudicative evidence. There is no implied extraction or human-arbitration fallback. |
| **E-B. Broader automatic extraction required before release** | Release is blocked until specified PDF, office, image, audio/video, archive, or other selected formats have deterministic extraction/representation profiles and the full represented bundle passes MP-25. | A format without an accepted complete representation remains ineligible; extraction failure blocks judgment with escrow unchanged. |
| **E-C. Human arbitration required before release** | Release is blocked until a separately accepted human-arbitration contract can inspect the broader evidence set and produce an attributable, immutable, settlement-authorizing decision. | Unsupported automatic evidence routes to that explicit process only when its authority, deadlines, privacy, reviewer conflicts, appeal/versioning, fees, and failure recovery are accepted and implemented. |

E-B and E-C may be combined only by an explicit owner decision. Selecting public visibility or local models does not select broader extraction or human arbitration. Selecting E-A means broader-artifact tasks must be reformulated before bidding or remain outside the supported release scope.

### Proposed E-A supported-format and capacity profile

| Artifact | Proposed treatment |
| --- | --- |
| UTF-8 plain text (`text/plain`, `.txt`) and Markdown (`text/markdown`, `.md`) | Automatic only when the complete evidence bundle fits the 64 KiB aggregate limit |
| UTF-8 source text (`text/plain` with declared source language and one of `.py`, `.js`, `.ts`, `.html`, `.css`, `.sql`, `.sh`) | Treated only as literal source text, never executed or rendered; valid UTF-8 without NUL bytes is required. Admission makes no claim that the source compiles or is correct. |
| UTF-8 JSON (`application/json`, `.json`) | Automatic when syntactically valid and within the same aggregate limit |
| UTF-8 CSV (`text/csv`, `.csv`) | Automatic when parseable under a declared delimiter/header contract and within the same aggregate limit |
| PDF, office files, images/audio/video, archives, executables, encrypted/malformed files, or overflow | May be staged under the chosen visibility policy, but cannot be adjudicative evidence under E-A |

The limit covers required inputs, outputs, the full specification, claim, and rebuttal together. Publish explicit per-field/output reservations with the profile and validate their maximum total before bidding. Do not admit a task whose declared evidence cannot fit. Model instructions and context overhead are budgeted separately and tested for every three-judge profile; every activated judge must process the entire admitted bundle.

This is the complete proposed E-A allowlist. Every unlisted type, extension, or source language is unsupported until an explicit profile revision; extensions and declared media types must agree with the verified byte/parser result. Selecting E-B or E-C alone does not close T-162: the selected formats and complete representation rules, or the human-arbitration contract, still require acceptance before implementation and release.

## Axis 4 — retention and deletion

| Choice | Raw evidence | Durable audit |
| --- | --- | --- |
| **R-A. Proposed 90+30 schedule** | Keep bytes until 90 days after the later of terminal task status and confirmed financial settlement. Revoke ordinary access at expiry, purge eligible live copies on the next maintenance run, and expire recovery copies within 30 more days. | Retain the universal audit chain for the life of the financial ledger. |
| **R-B. Owner-defined schedule** | The owner supplies explicit live-copy and recovery-copy periods, unresolved-dispute handling, and any legal/operational hold rules before release. | The universal audit chain still remains; a shorter byte schedule cannot erase provenance. |

Preserve unresolved-task evidence. A restore reapplies deletion tombstones before serving bytes. An offline process cannot promise scheduled physical erasure while stopped. Publicly downloaded or otherwise exported copies are outside platform deletion control.

## Proposed blocked-judgment and appeal behavior

- Poster approval remains available while the task is submitted. Once disputed, approval is not a bypass for a blocked Court attempt; identical-byte recovery must restore missing evidence.
- Under E-A or E-B, a mutual settlement or human decision requires its own accepted authority and settlement contract. Under E-C, only the accepted human-arbitration contract may authorize that outcome.
- A final ruling cannot be rerun, replaced, or superseded under the proposed no-merits-appeal rule. Post-ruling corruption is an operational incident against the immutable audit record.

## Proposed worked examples

1. With V-B, M-B, and E-A, a public discovery summary advertises a task with a 12 KiB CSV input and 20 KiB JSON output reservation. An approved bidder sees the private frozen specification and input. All text fields and evidence fit 64 KiB, so the three local real judges receive the same complete hashed bundle.
2. A task requires a 2 MiB PDF. Under E-A it cannot enter bidding as an automatically adjudicable task. Under E-B, release depends on an accepted complete PDF representation. Under E-C, release depends on the implemented human-arbitration contract. Visibility V-A/V-B/V-C does not change that admissibility result.
3. An admitted artifact is missing during dispute. Under every option, Court records a blocked attempt, leaves escrow untouched, and retries identical-byte retrieval; it never rules from an empty string, summary, or prefix.
4. Under V-A, the poster accepted public input/specification terms and the worker accepted public output terms before work. Later service-access revocation stops future authenticated access where applicable but cannot recall copies already downloaded from the public record.

## Bounded owner decisions still required

- Choose V-A, V-B, or V-C for full-specification and artifact visibility/access.
- Choose M-A, M-B, or M-C for the model boundary.
- Choose E-A, E-B, E-C, or an explicit E-B+E-C combination for release capability. If E-A is selected, confirm the 64 KiB complete-bundle limit and format allowlist.
- Choose R-A or provide the explicit R-B schedule.
- Confirm or replace the proposed no-merits-appeal rule. An appeal choice must define ruling versions and supersession before implementation.
