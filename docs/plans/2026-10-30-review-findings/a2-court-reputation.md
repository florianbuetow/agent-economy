# A2 — Court & Reputation audit (G07–G12), HEAD 802fe37

## Summary table

| G-id | Status | One-line |
|---|---|---|
| G07 | MISSING | Court still truncates deliverables to a byte budget, decodes with `errors="replace"`, drops hashes/filenames/uploader, mixes inputs and outputs, and has no prompt-injection boundary. |
| G08 | MISSING (plus a live bug) | Ruling is persisted only after Task Board settlement and Reputation feedback. A failure in between reverts and re-judges, which then permanently conflicts at Task Board. No judging lease or reclaim. Rebuttal replay returns 409. |
| G09 | MISSING | Judges return only `worker_pct` and `reasoning`. Quality ratings are derived from payout thresholds. The shipped profile has 1 mock judge, not 3 real ones. |
| G10 | MISSING | Reputation checks only signer equals `from_agent_id`. No task-party, outcome, direction, or category check. Court and peer feedback are indistinguishable in storage. Any replay returns 409. |
| G11 | PARTIAL | Mutual reveal is atomic and durable. Timeout reveal is computed at read time only, so the Reputation API and the UI disagree after the timeout. |
| G12 | MISSING | Reputation has no score endpoint. The feeder averages raw records itself. The UI runs its own SQL over `visible = 1`. |

Doc accuracy: FEATURE-COMPLETENESS G07–G12 descriptions match current code. None are stale.

## Evidence per gap

### G07 — Evidence bundle (rule 3)
- **Truncation.** `services/court/src/court_service/services/ruling_orchestrator.py:80-89` slices each asset's bytes to the remaining budget and stops once the total is used up. The budget is `max_deliverable_bytes: 65536` in `services/court/config.yaml`. Nothing is reported to the judges or the caller.
- **Invalid UTF-8.** `ruling_orchestrator.py:89` decodes with `errors="replace"`, which produces exactly the fabricated text that rule 3 forbids.
- **Metadata dropped.** The Task Board listing at `services/task-board/src/task_board_service/services/asset_manager.py:549-558` provides `content_hash`, `filename`, `uploader_id` and `content_type`. `DeliverableFetcher.fetch` keeps only the bytes. Poster inputs and worker outputs end up in one unlabeled list.
- **No immutable manifest.** The fetcher reads every current task asset. It does not read the frozen input manifest or the submitted output manifest.
- **Fallback hides failures.** If `deliverable_fetcher` is None, `routers/disputes.py:235-236` falls through to `task_data.get("deliverables")`, which `get_task` never carries. Judges then rule on an empty list without any error.
- **Spec field fallback.** `_build_context` at `ruling_orchestrator.py:237` uses `str(task_data.get("spec", ""))`. A missing spec silently becomes an empty string.
- **Prompt injection.** Nothing in `judges/prompts.py` or `llm_judge.py:84-92` marks the claim, rebuttal or deliverables as untrusted, and nothing delimits them. They are interpolated straight into the user prompt.
- **What is present.** Spec, claim and rebuttal are included. The rule "ambiguity favors the worker" is implemented with a rubric in `judges/prompts.py:5-62`.
- **Tests.** `tests/unit/routers/test_wp06_deliverables.py` asserts only that the uploaded content reaches the judge context. No test covers truncation, decoding, hashes or injection.

### G08 — Persist before side effects, retry, reclaim, rebuttal replay (rule 5)
- **Order.** In `ruling_orchestrator.py:444-475`, `finish_ruling` runs the judges, then `_record_task_ruling` (Task Board settlement), then `_record_feedback` (Reputation), and only then `persist_ruling`. Any exception calls `revert_to_rebuttal_pending`, which also deletes the ruling (`dispute_db_client.py:296-298`).
- **Retry re-judges.** No votes are stored before delivery, so the next trigger calls the judges again (`ruling_orchestrator.py:447`). This is the concrete bug B1 below.
- **No reclaim.** `begin_ruling` sets `judging` without a lease or owner (`ruling_orchestrator.py:429`). `_validate_ruling_preconditions` rejects every status except `rebuttal_pending` and `rebuttal_submitted` (`:200-207`). Nothing in services, agents or libs scans for stale `judging` claims. A crash after `begin_ruling` therefore leaves the dispute stuck forever, see bug B2.
- **Non-atomic status claim.** `set_status` is an unconditional POST (`dispute_db_client.py:273-294`), not a compare-and-set. Two concurrent triggers can both pass validation and both judge.
- **Rebuttal replay.** `dispute_service.py`'s `submit_rebuttal` raises 409 `rebuttal_already_submitted` for any second submission, including an identical one. The check and the write are also not atomic.
- **Tests give false confidence.** `tests/unit/routers/test_wp06_retry_clean.py:62` ("kill between TB record and dispute persist converges on retry") fakes Task Board `record_ruling` as deduplicated by `ruling_id`. The real Gateway deduplicates by `operation_id` plus the request SHA-256, see bug B1. The test passes against behavior production does not have.

### G09 — Structured findings and the real panel (rules 5 and 6)
- **Payout only.** `llm_judge.py:102-109` validates only `worker_pct` and `reasoning`. There is no schema version, no spec-quality or delivery-quality finding, and no explanation per dimension.
- **Quality derived from payout.** `ruling_orchestrator.py:173-185` maps `worker_pct` to `delivery_rating` and `_spec_rating` (inverted) using the 80/40 cutoffs. The ruling summary is cut to 256 characters for the feedback comment (`:285`).
- **Silent defaults.** For dict votes, `_normalize_vote` turns an invalid `worker_pct` into 50 and clamps out-of-range values (`ruling_orchestrator.py:146-158`). LLMJudge also accepts `bool`, because `isinstance(True, int)` is true (`llm_judge.py:104`).
- **Shipped panel.** `config.yaml` sets `panel_size: 1` with one `provider: "mock"` judge. The real LM Studio block is commented out and also has only one judge. The config validator allows any odd size, so 3 is not required.
- **Provider.** The provider is LiteLLM with an `api_base`, so an OpenAI-compatible endpoint is supported. `lifespan.py:34` falls back to `"llm"` when the provider is missing, which conflicts with the project's no-defaults rule.

### G10 — Feedback authorization and provenance (rule 6)
- **Signer only.** `services/reputation/src/reputation_service/routers/feedback.py:183-196` checks only that the signer equals `from_agent_id`. `services/feedback.py:validate_feedback` checks field types, self-feedback, category, rating and comment length.
- **No task-party check.** No code verifies that `from` and `to` are the task's poster and worker, that the task reached an eligible outcome, or that the category matches the direction. For example, a poster can submit `spec_quality` about the worker, and anyone can rate anyone else's task. The only guards are the foreign keys at `current_v5.sql:224-226`.
- **No provenance.** `feedback_db_client.py:58-61` derives `role` from the category, so Court-signed rows get role `poster` or `worker` like peer rows do. The only provenance signal is `from_agent_id == platform id`, which is implicit.
- **Replay.** The unique key `(task_id, from_agent_id, to_agent_id)` (`current_v5.sql:229-230`) returns 409 `feedback_exists` for both an identical replay and changed content. Court treats any 409 as success (`ruling_orchestrator.py:309-341`), so a changed Court finding would be silently dropped.
- **Tests.** No test exercises task-party authorization.

### G11 — Timed reveal (rule 8)
- **Mutual reveal works.** It is atomic inside one `BEGIN IMMEDIATE` transaction and emits a `feedback.revealed` event (`services/db-gateway/.../reputation_writer.py:85-139`). It is tested in `tests/unit/test_persistence.py:711` and `:903`.
- **Timeout reveal is read-time only.** `services/feedback.py:is_visible` compares against the clock on each read. No background job or event exists, and `visible` stays 0 in the database.
- **Consumers disagree.** The UI filters `visible = 1` (`services/ui/src/ui_service/services/agents.py:238,246`), so it never shows feedback that timed out. The Reputation API, and therefore the feeder, does show it.

### G12 — Service-owned score (rules 6 and 7)
- **No endpoint.** Reputation exposes only `POST /feedback` and `GET /feedback/{task|agent|id}` (`routers/feedback.py:114,231,258,285`). There is no score endpoint, no per-task weighting, no Court override, no eligibility filter, no explicit unrated result, no counts, and no restricted source detail.
- **Feeder computes locally.** `agents/src/task_feeder/acceptance.py:57-70,143-150` averages every raw `delivery_quality` record per record, not per task. A disputed task with both a peer row and a Court row counts twice. An unrated bidder is scored as the floor of the scale.
- **UI computes separately.** `services/ui/src/ui_service/services/agents.py:153,238-246,358` and `metrics.py:345-442` run separate SQL ratio formulas directly on `reputation_feedback`.

## Logic bugs found

- **B1 (high): a retry after partial delivery can never finish.**
  - **Scenario.** Task Board `record_ruling` succeeds and settles escrow, and the task moves from `disputed` to a terminal status. Then Reputation feedback fails with a non-409 error, or `persist_ruling` fails. Court reverts to `rebuttal_pending` and deletes its ruling (`ruling_orchestrator.py:476-478`).
  - **Why it sticks.** The Task Board trigger only fires for `disputed` tasks (`services/task-board/.../deadline_evaluator.py:88-91`), so it stops. A manual retry re-judges. It sends the same `operation_id` (`operation_id_for("court-ruling", dispute_id)`, `ruling_orchestrator.py:362`) but a new `expected_lifecycle_version`, and with LLM judges usually a different `worker_pct` and summary. The Gateway rejects that with 409 `operation_id_conflict`, because the request SHA covers version and arguments (`task_operation_writer.py:393-399`, `task_operation_requests.py:315-342`). Task Board may already reject it as the wrong status in `accepted_task_arguments`.
  - **Result.** Money is settled, but Court has no persisted ruling and no votes, and the quality feedback is missing or partial. Nothing ever converges. The test at `test_wp06_retry_clean.py:62` masks this.
- **B2 (high): a crash during judging leaves the dispute stuck.** A crash or kill between `begin_ruling` (`judging`) and the end of `finish_ruling` leaves the dispute in `judging`. Every later trigger fails with `dispute_not_ready`, and the Task Board trigger logs this and retries forever (`deadline_evaluator.py:233-243`).
- **B3 (medium): concurrent triggers can both judge.** `begin_ruling` is check-then-set without compare-and-set (`ruling_orchestrator.py:428-429`, `dispute_db_client.py:273`). Two concurrent `/rule` calls, such as the periodic sweep plus a read-path evaluation, can both judge. The loser fails at the Gateway with `operation_id_conflict`, then runs `revert_to_rebuttal_pending` and `_delete_ruling`. That can roll back the winner's status or delete its ruling, depending on interleaving.
- **B4 (medium): ruling feedback is double-counted.** Peer feedback has no outcome gate, so on a disputed task both peer rows and the Court rows feed the feeder score and the UI ratios. The Court finding does not replace the peer finding.
- **B5 (low): late rebuttals are accepted.** Neither Court `submit_rebuttal` nor Task Board `submit_rebuttal` (`task_ruling.py:85-196`) checks `rebuttal_deadline`. After a failed ruling reverts to `rebuttal_pending`, a rebuttal can still land after the window closed.
- **B6 (low): changed Court feedback is dropped.** Court treats any Reputation 409 as success. Together with B1's re-judging, a changed Court finding is lost without any signal.

## Recommended work items (ordered)

1. **Persist the decision before delivery (G08, fixes B1, B2, B3).** Persist the votes, the final `worker_pct`, the summary and the request snapshot in one atomic compare-and-set claim before any Task Board or Reputation call. Delivery must replay the stored payload with the stored `expected_lifecycle_version`. *Done when* a test that fails Reputation once after Task Board settled reaches `ruled` with one judge invocation and one settlement, against the real Gateway `operation_id` semantics.
2. **Make judging reclaimable (G08, B2).** Add a judging lease with an expiry and a sweep that reclaims expired leases. *Done when* a dispute left in `judging` with an expired lease is re-ruled or resumed by the periodic trigger.
3. **Make rebuttals replayable and deadline-checked (G08, B5).** An identical rebuttal returns 200 with the same record. Changed content returns 409. Submissions after the deadline are rejected. *Done when* these three cases pass through the public API.
4. **Build a complete evidence bundle (G07).** Include the frozen spec, the input manifest and the output manifest with hashes, uploader and role, plus the claim and rebuttal. Reject or explicitly defer oversized or undecodable evidence instead of truncating it. Add an untrusted-content delimiter to the prompt. *Done when* tests cover an oversized asset, invalid UTF-8 and an injected instruction, each with explicit behavior and no silent change.
5. **Version the judge output and ship a 3-judge profile (G09).** The judge schema must carry `spec_quality`, `delivery_quality`, `worker_pct` and explanations. Reject invalid votes instead of defaulting to 50. Ship a 3-judge OpenAI-compatible profile. *Done when* a malformed vote fails the panel and the ruling stores both quality findings independently of payout.
6. **Authorize feedback against the task (G10, B4, B6).** Check the actual task parties, an eligible outcome, and the direction and category. Store an explicit `source` of `court` or `peer`. An exact replay returns 200 and changed content returns 409. *Done when* a non-party, a wrong direction, and feedback on an ineligible task are rejected through `POST /feedback`.
7. **Make timed reveal durable (G11).** A background sweep sets `visible = 1` and emits one `feedback.revealed` event per pair. *Done when* the Reputation API and the UI agree once the timeout has passed, and the event is emitted exactly once.
8. **Add the service-owned quality score (G12).** `GET /agents/{id}/quality` returns separate dimensions, equal per-task weighting, a Court override, the eligibility filter, explicit `unrated`, counts, and restricted provenance. *Done when* the feeder and the UI both call it, their local formulas are deleted, and a disputed task counts once.
