# Goal: finish the Agent Economy

Updated: 2026-11-04 after the owner's scope correction.

## What the user wants

A working, robust home-LAN job board: one machine runs the existing economy;
humans and independently keyed agents can post jobs with inputs, take work,
submit results, receive the agreed payment, and resolve disputes. The UI must be
convenient for posting, tracking jobs, inspecting files, and navigating agent
history. Ordinary interruptions must not abandon committed work or duplicate
payments. OpenTelemetry must make failures and activity understandable.

Finish the existing product. Use judgment, investigate the code, fix demonstrated
problems, and verify the result without making the user supervise routine work.

## Source of scope

The owner's instructions and ratified decisions are authoritative. The existing
`MASTERPLAN-TO-COMPLETE-THE-PROJECT.md` defines the Core and non-math Vision work.
`FEATURE-COMPLETENESS.md`, recent commits, and review documents help locate work;
their historical findings must be checked against current code.

An assistant-written plan, checklist, review suggestion, or test does not create
a new user requirement. Do not promote suggestions into release blockers.
This correction supersedes the previous GOAL.md and copied prompts wherever they
required a second physical machine, SSH access, or a new agent discovery stream.
The canonical issue tracker remains `openspec/specs/completion-backlog/spec.md`.

## Current checkpoint — 2026-11-04

The runtime, strict-response, UI file/submission, external-agent input, restore,
and classification fixes have focused verification and independent review. Real
isolated browser poster and worker journeys now reach approval and conserved
payment. Full repository `just ci-quiet` passed on the integrated tree on
2026-11-04; all 1,497 recorded source/config/test inputs were unchanged during
the run. Do not reimplement these fixes.

The configured Qwen3-0.6B Court panel fails the declared judgment-quality cases.
Bounded general prompt repairs did not pass, so the original prompt and requested
model remain unchanged. This is the remaining unmet judge-readiness criterion;
a passing code gate must not be presented as proof that it passed.

See [acceptance evidence](docs/plans/2026-11-04-completion-evidence/acceptance.md)
and [current CI status](docs/plans/2026-11-04-completion-evidence/root-ci-run.md).

## Work to finish

1. **Fix demonstrated robustness defects.** Finish Task Board's blocking Gateway
   call repairs and explicit handling of malformed dependency responses. Preserve
   financial, authorization, replay, and recovery behavior. Check other reported
   defects against the actual code before changing anything.
2. **Make the existing remote-agent instructions accurate.** Correct commands,
   worker/feeder configuration, own-key setup, registered IDs, funding operation
   IDs, and authenticated balance checks. Verify configured LAN bindings and
   independent client behavior locally. No second physical machine is required.
3. **Verify the actual judge configuration.** Use only LM Studio `qwen3-0.6b`;
   Court judges retain `/no_think`. Evaluate the existing small declared corpus.
   Address demonstrated failures without changing the model, weakening the
   rubric, or manipulating the expected results. Preserve failure evidence.
4. **Finish existing public participation flows where genuinely incomplete.**
   Reuse the public SDK, external-agent example, and existing browser/agent tests.
   Verify inputs, outputs, accepted-price payment, and earn-then-post behavior.
   Existing polling is acceptable. Fix documentation to describe shipped APIs.
5. **Verify the already-planned economic and operational behavior.** Reuse the
   non-math classification runtime and report to check observed multi-round
   behavior; reuse restart, current-format backup/restore, and telemetry tooling.
   Add only missing basic checks needed to establish that these features work.
6. **Reconcile status and complete the release checks.** Update stale completion
   claims and canonical tickets from actual evidence. Run the required final
   isolated root CI after integrating the fixes.

## Definition of done

The project is done when all of the following are true:

- The existing human-poster, human-worker, independent-agent, and unattended
  feeder/worker journeys work through their public interfaces, including inputs,
  deliverables, review/dispute, payment, and the planned earn-then-post behavior.
- Accepted-price settlement conserves funds; retries do not duplicate effects;
  ordinary restarts recover committed work. Task Board remains responsive during
  slow dependency calls and reports dependency failures explicitly.
- Court uses the requested model, preserves evidence and one durable ruling,
  applies the agreed ambiguity/quality rules, and passes the existing real-model
  acceptance cases. Schema-valid JSON alone is not proof of sound judgment.
- The UI supports the intended tasks conveniently; existing navigation, files,
  payment/status views, and action feedback agree with the services.
- Existing non-math, operational recovery, and observability features have basic
  evidence supporting their actual claims. Do not claim measured adaptation from
  scripted winners, or restored work from an archive-only byte comparison.
- Documentation describes the shipped system. LAN readiness is checked locally;
  physical cross-machine connectivity is not a completion condition.
- Relevant focused checks and the final isolated root `just ci-quiet` pass.
  Substantive changes receive a bounded independent review; concrete findings
  are resolved and affected checks rerun.
- The tracker and final report accurately distinguish completed work, remaining
  defects, and explicitly deferred scope. Commit/push follows the repository's
  Europe/Amsterdam office-hours rule; report any publication restriction.

## What NOT to do

- **Do not invent requirements or side projects.** Every change must fix a
  demonstrated defect or complete behavior already requested by the owner.
- **Do not require another machine, SSH, extra hardware, or a new deployment.**
  None is available or needed for this task. Do not make their absence a blocker.
- **Do not add an agent event-stream API, SDK streaming iterator, or new Gateway
  discovery routes.** Existing agent polling is sufficient. Preserve the existing
  UI event stream and fix actual bugs in it only when necessary.
- Do not redesign the architecture, add services, replace the frontend, adopt
  Kubernetes/kind, introduce per-service containers, or build generic frameworks.
- Do not expand LAN UI exposure, add TLS/authentication projects, or change
  balance/privacy policy without an explicit owner decision. Keep current bounds.
- Do not develop historical-data migrations, compatibility layers, or obsolete
  schema paths. Use the current schema and disposable test data.
- Do not reopen completed features based on stale documents, or add speculative
  hardening unrelated to an observed defect or an agreed business invariant.
- Do not build exhaustive fault matrices, new verification infrastructure,
  benchmarks, mutation-test suites, or repeated planning/review ceremonies.
- Do not change models, substitute mocks for real judge evidence, hard-code
  expected outcomes, weaken assertions, suppress failures, or skip required gates.
- Do not stop unrelated services, overwrite ordinary runtime data/keys, or run
  destructive experiments against the user's normal environment.
- Do not ask the user to make routine engineering decisions or perform ordinary
  debugging steps. Investigate and choose the simplest adequate solution.

## How to test and check your own work

1. **Establish the requirement.** Identify the owner-requested behavior and its
   observable result. Check existing code/tests first. If the proposed check adds
   a new requirement, discard it rather than expanding the task.
2. **Reproduce concrete defects.** Add the smallest missing regression and verify
   it fails for the intended reason before implementation. Distinguish environment
   failures from application failures. Follow existing test/implementation tickets.
3. **Fix and run focused checks.** Use the justfile and existing test harnesses.
   Reuse tests; add only missing behavior checks. Stop an investigative test run
   exceeding ten minutes, inspect why, and choose a narrower useful check.
4. **Verify the real boundary.** A public flow needs a public API, CLI, browser, or
   runtime check. Use isolated local services and independent client identities.
   Test configured LAN bindings locally; do not demand physical LAN hardware.
   For money, authorization, files, and recovery, assert the relevant invariant
   directly, including one meaningful retry or negative case where needed.
5. **Check integrations and review the diff.** Confirm callers match the changed
   contract. Run applicable style/type/static checks. Review for scope creep,
   swallowed errors, weakened tests, secrets, and unintended behavior changes.
   An independent reviewer checks substantive edits, not a new speculative audit.
6. **Run the final gate once integrated.** Run isolated root `just ci-quiet` to its
   actual exit status. This required full gate is exempt from the ten-minute
   investigative-test limit. Fix failures and rerun affected checks and the gate.
   Do not run mutation testing.
7. **Report evidence honestly.** Record commands, exit statuses, and meaningful
   results. Historical green runs and an agent's summary do not prove the final
   tree works. Do not claim completion until the definition above is satisfied;
   do not hold completion hostage to excluded or invented requirements.
