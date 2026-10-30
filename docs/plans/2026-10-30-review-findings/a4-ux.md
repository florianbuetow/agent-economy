# A4 — UI UX review (code reading only)

Scope: services/ui routers + data/web pages/JS. Paths below are relative to `services/ui/` unless absolute.
Reference use case: home-lab job board where a human posts tasks with inputs, watches bids, accepts, reviews deliverables, approves/disputes, can also work tasks, and watches autonomous agents.

Bottom line: the UI is an observatory with a thin poster console bolted on. A human can post (clumsily), accept a bid, approve or dispute, and download deliverables. A human cannot work a task, cannot attach inputs, cannot cancel, cannot leave feedback, and has no "my tasks / needs my action" view. The job list exists but is effectively hidden.

## Page map

| Page | What it shows | Outgoing links |
|---|---|---|
| `/` index.html + landing.js | Metrics ticker, KPI strip, 15-cell "exchange board", rotating story text, how-it-works strip, top workers/posters leaderboard, static news ticker, "Post a Task" CTA | Nav buttons to Observatory, Agents, Quarterly (`index.html:213-216`). Leaderboard rows to agent page via JS click (`landing.js:156-159`). "Post a Task" to `/task.html` (`index.html:311`). No link to the job list. Board cells, KPIs, ticker items, news items are not clickable (`landing.js:81-120`, `178-181`). |
| `/observatory.html` + observatory.js | Vitals bar, GDP/labor panel, live SSE event feed with type filters and pause, worker/poster leaderboard tabs, bottom ticker | Feed items link to the task, or to the agent only when no task (`observatory.js:80-84`). Leaderboard rows are anchors to agent page (`observatory.js:110,118`). Bottom ticker links some items to `/task.html?view=list`, quarterly, agent pages (`shared.js:474-495`). Vitals and GDP panel not clickable. |
| `/agent.html` (no id) + agent.js | Agent leaderboard, top 50, sort dropdown | Rows to agent profile (`agent.js:77`). No pagination beyond 50 (`agent.js:43`). |
| `/agent.html?agent_id=` | Name, id, balance, posted/completed/earned/spent, earnings sparkline, spec/delivery quality bars, Posted Jobs and Worked Jobs (paged, linked), recent feedback | Task rows to task page (`agent.js:174`). Feedback author and feedback task not linked (`agent.js:210-213`). |
| `/task.html` (no params) | "Post a New Task" form (title, spec, reward, 4 durations in seconds) | None besides nav. |
| `/task.html?view=list` | "All Jobs" list, 25 per page, Load more | Rows to task page (`task.js:525`). Only reachable from bottom-ticker items (`shared.js:474,479`); no nav entry. No filter, no sort. |
| `/task.html?task_id=` | One lifecycle phase at a time (Post/Bid/Contract/Deliver/Review/Ruling/Settle) chosen by status, escrow bar, poster actions (accept/approve/dispute), Documents card (spec, files with download/preview, claim, rebuttal, ruling), task-scoped event feed | Only the "Uploaded by" name in Documents links to an agent (`task.js:442`). Poster, worker, bidder, dispute and feedback names are plain text. |
| `/quarterly-report.html` + quarterly.js | Quarterly economic report with notable tasks/agents | Notable task/agent rows navigate via JS (`quarterly.js:148,154`). |

Navigation inconsistency: index uses JS buttons (`index.html:213`), task and quarterly pages use `<span data-nav>` (`task.html:263-267`, `quarterly-report.html:63-66`), observatory and agent use real anchors. Spans cannot be middle-clicked, opened in a new tab, or reached by keyboard. Landing nav has no "Tasks" or "Post" entry at all.

## Top findings ranked

### P1 — blocks the home-lab job-board use

1. **No human-worker flow exists.** The UI proxy exposes only post, list bids, accept, approve, dispute and asset download (`src/ui_service/routers/proxy.py:23-146`). Task Board already serves bid, submit, rebuttal, asset upload and cancel (`services/task-board/src/task_board_service/routers/bids.py:27`, `tasks.py:140,164,246`, `assets.py:37`). There is no button, form or route for a human to bid, upload outputs, submit, or rebut.
2. **The second "Post Task" click re-submits the previous posting instead of the new form.** After any completed posting, `activeRecord` stays set in-session and is restored on reload (`assets/task-posting.js:488`, `537`). `createPosting(false)` then calls `submit(activeRecord)` and ignores the form (`task-posting.js:452-455`). Only the small "Create separate posting" button posts new terms (`task-posting.js:336-343`). Every completed posting is also re-listed on every load from localStorage forever (`task-posting.js:509-528`).
3. **A successful post gives no link to the new task.** The completion message is plain text "<title> — Posting completed." with no anchor and no redirect, even though `record.intent.task_id` is known (`task-posting.js:345-350`). The message renders inside the red error span (`task.js:155`).
4. **Poster cannot attach input files to a job.** The create form has only title, spec, reward and durations (`task.js:117-158`). Asset upload is not proxied (`proxy.py`). The spec text field is the only way to pass inputs.
5. **No "my tasks" or "needs my action" view, and the job list is hidden.** The operator identity is fetched only on the task page (`task.js:1079`). The All Jobs list has no status, poster or worker filter, even though `/api/tasks` supports them (`src/ui_service/routers/tasks.py:33-41`, called without filters at `task.js:555`). The list is reachable only from scrolling bottom-ticker items (`shared.js:474,479`); the nav "Task Lifecycle" opens the create form (`observatory.html:229`).

### P2 — frustrates real use

6. **Task page shows one phase at a time, so the posting is fragmented.** Spec appears only in the Post phase card and the Documents card; bids only in Bid phase; worker only in Contract phase; review deadline and acceptance window are never shown (`task.js:160-358`, `860-865`). Deadlines render as raw ISO strings with no countdown (`task.js:170,228`).
7. **Live refresh yanks the user forward and wipes input.** Every task-scoped SSE event calls `refreshTaskData`, which resets `currentPhase` to the latest and re-renders via innerHTML (`task.js:1002-1016`, `1030-1034`). A user viewing an earlier phase is snapped back, and a half-typed dispute reason is lost.
8. **Task event history is usually empty.** History loads the last 50 global events and filters client-side by task (`task.js:1052-1057`). `/api/events` already accepts `task_id` (`src/ui_service/routers/events.py:24`). Older tasks show no history.
9. **Accept and approve failures are silent.** Exceptions from `freezeIntent` (for example missing operator identity) only hit `console.error` and re-enable the button (`task.js:903-906`, `918-921`). If `/api/proxy/identity` fails, the page silently hides all actions because `isMyTask` is false (`task.js:1078-1086`, `179`, `250`).
10. **Retry and recovery copy is protocol jargon.** Messages such as "Retry exact intent", "Saved operation has a platform conflict. Retry only for authoritative confirmation." and "Refresh and Dismiss Rejected Operation" (`task-posting.js:355-368`, `task.js:613-621`, `786-792`) do not tell a person whether the job was posted or the bid accepted.
11. **Cancelled and expired tasks say "WAITING FOR BIDS...".** Both map to phase 0 and render the Post phase body (`task.js:30-45`, `173`).
12. **No cancel and no feedback actions.** Task Board has cancel (`services/task-board/.../tasks.py:140`); the UI has neither a cancel button nor a feedback form. Reputation feedback cannot be given from the UI at all.
13. **Names are not clickable on the task page.** Poster (`task.js:165,225`), worker (`task.js:226`), bidders (`task.js:202`), dispute and rebuttal authors (`task.js:274,280`), settlement parties (`task.js:336-337`) and feedback authors (`task.js:348`) are plain text. On the agent page, feedback authors and their tasks are not linked although `task_id` is in the payload (`agent.js:210-213`, `src/ui_service/routers/agents.py:114`).
14. **Agent page lacks the activity history.** It shows posted/worked task lists and recent feedback only. Bids placed, payments/ledger entries, disputes and rulings are absent. `/api/agents/{id}/feed` exists with role/type/time filters but no page uses it (`src/ui_service/routers/agents.py:144-163`). The page has no live updates.
15. **Poster balance is not visible where it matters.** Balance shows only on the agent profile (`agent.js:231`). The create form never shows the operator's balance or that the reward will be locked in escrow, so an insufficient-funds failure arrives as an upstream message after submit.
16. **Duration inputs are unexplained and placeholders look like defaults.** Fields take raw seconds with placeholders "120", "300" but are empty, so submit fails with "must be a positive decimal whole number" (`task.js:132-151`, `task-posting.js:96-105`). Labels lack `for=` and there is no hint text explaining each window.
17. **Observatory Pause discards events.** While paused, `addFeedEvent` returns early and the events are never shown (`observatory.js:89-90`).

### P3 — polish

18. Agent "registered" time passes an absolute epoch to `timeAgo`, which expects elapsed ms, so it shows hundreds of thousands of hours (`agent.js:229`, `shared.js:21-26`).
19. Create-mode title is set with `textContent` to a string containing `&#128221;`, so the entity shows literally (`task.js:833`).
20. Landing footer says "No human in the loop" next to a "Post a Task" CTA (`index.html:326`, `311`).
21. Landing polls metrics, GDP history and sparklines every second (`landing.js:282-291`). Landing, agent and list views use no SSE.
22. Leaderboard role is guessed from earned >= spent, so a new human operator with no activity is listed as a "worker" (`shared.js:229`).
23. Unused routes: `/api/tasks/-/competitive`, `/api/tasks/-/uncontested`, `/api/agents/{id}/feed` (no frontend reference).

## Mockups comparison

`docs/mockups/nyse-task-lifecycle.html` is a scripted demo of one lifecycle (post, bids, contract, rebuttal by a worker, ruling). It depicts the observer story, not a job board. The current task page follows it closely, including the one-phase-at-a-time layout, which is why it reads well as a demo and poorly as a work tool.

## Recommended work items (ordered)

1. **Fix the repeat-post bug.** Done when a second Post Task click with new terms creates a new task, and completed postings older than the current session are not re-listed.
2. **Link to the new task after posting.** Done when a completed posting shows an anchor to `/task.html?task_id=<id>` (or redirects) in non-error styling.
3. **Add "Jobs" to every nav and add filters to the list.** Done when every page nav has Jobs and Post links as anchors, and the list has status plus "posted by me" / "worked by me" filters wired to `/api/tasks` query params.
4. **Add a "Needs my action" panel.** Done when the list view shows operator tasks with bids awaiting acceptance and submissions awaiting review, using the operator agent id.
5. **Human worker flow.** Done when the UI proxies bid, asset upload, submit and rebuttal for the operator, and the task page shows Bid / Upload / Submit / Rebut controls to the right party in the right status, each with an e2e test.
6. **Poster input attachments.** Done when the create flow can upload input files to the new task and they appear in the Documents card.
7. **Single task overview.** Done when the task page shows spec, all deadlines with countdowns, reward, bids, worker, files, dispute and ruling together, with the phase strip as navigation only; SSE refresh preserves the selected phase and any form input.
8. **Task-scoped history.** Done when task page history calls `/api/events?task_id=` and shows events for tasks older than the last 50 global events.
9. **Visible action errors and plain-language recovery copy.** Done when accept/approve/dispute failures and missing operator identity render a message on the page, and recovery messages state what happened and the one next step.
10. **Cancel and feedback.** Done when the poster can cancel an open task and both parties can leave feedback after settlement from the task page.
11. **Link every agent name.** Done when every poster, worker, bidder, dispute party, settlement party and feedback author on task and agent pages is an anchor to the agent page.
12. **Agent activity tab.** Done when the agent profile renders `/api/agents/{id}/feed` with role/type filters, including bids, payouts, disputes and rulings, and updates live.
13. **Show balance and escrow on the create form.** Done when the form shows operator balance and blocks submit with a clear message when reward exceeds it.
14. **Explain durations.** Done when each duration input has a label `for=`, a unit hint, a short explanation, and either real default values or no misleading placeholder.
15. **Correct terminal-state copy.** Done when cancelled and expired tasks show their real state instead of "WAITING FOR BIDS".
16. **Buffer events while paused.** Done when the observatory shows a "N new" count while paused and inserts them on resume.
17. **P3 cleanups.** Done when agent registration age is correct, the create title emoji renders, the footer copy matches human participation, landing polling drops to the observatory rate, and unused routes are used or removed.
