# UI Multi-Page Website Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform three standalone HTML mockups into a functioning multi-page website served by the UI service, with working navigation between all pages and clickable interactive elements — using dummy/mock data for now.

**Architecture:** Three separate HTML files (`index.html`, `observatory.html`, `task.html`) served by the existing FastAPI static file server. Shared CSS extracted into `assets/style.css`, shared JS utilities into `assets/shared.js`, and per-page JS into `assets/<page>.js`. The existing SPA fallback in `app.py` already serves specific files when they exist, so no backend changes are needed for this step.

**Tech Stack:** Plain HTML/CSS/JS (no build tools, no framework). FastAPI serves static files. Playwright MCP for manual browser testing.

---

## Summary of Pages

| Page | File | Source Mockup | Description |
|------|------|---------------|-------------|
| Landing | `data/web/index.html` | `docs/mockups/nyse-landing-page.html` | Hero, KPI strip, exchange board, leaderboard, CTA |
| Observatory | `data/web/observatory.html` | `docs/mockups/nyse-live-ticker.html` | 3-column layout: GDP panel, live feed, leaderboard + vitals bar |
| Task Lifecycle | `data/web/task.html` | `docs/mockups/nyse-task-lifecycle.html` | Step-by-step demo with form, bids, contract, dispute, ruling |

## File Structure After Implementation

```
services/ui/data/web/
├── index.html                # Landing page (refactored: CSS/JS extracted)
├── observatory.html          # Platform overview page
├── task.html                 # Task lifecycle demo page
└── assets/
    ├── style.css             # Shared CSS (variables, reset, animations, nav, ticker, footer)
    ├── shared.js             # Shared JS (agents roster, economy state, ticker builder, nav renderer)
    ├── landing.js            # Landing page JS (KPI, exchange board, leaderboard, stories)
    ├── observatory.js        # Observatory page JS (vitals, GDP panel, feed, leaderboard)
    └── task.js               # Task lifecycle page JS (step engine, renderers, feed, demo controls)
```

## Navigation Scheme

All three pages share the same top nav bar with consistent links:

| Nav Button | Target | Style |
|-----------|--------|-------|
| ATE logo + "Agent Task Economy" | `/` (landing) | Brand, always visible |
| "Observatory" | `/observatory.html` | Outline button (solid when active) |
| "Enter the Economy" / "Task Lifecycle" | `/task.html` | Solid cyan button |
| LIVE indicator | — | Green dot + "LIVE" label |

The nav highlights the current page. On landing page, "Observatory" is outline and "Enter the Economy" is solid. On observatory, "Observatory" is solid. On task page, "Task Lifecycle" is solid.

Additionally, all CTA buttons on the landing page need to be wired:
- "Enter the Economy" → `/task.html`
- "Watch Live Agents" / "Observatory" → `/observatory.html`
- "Post a Task" → `/task.html`
- "Register Your Agent" → no-op for now (could show alert)
- "Explore the data →" → scroll to exchange board (stays on page)

On the observatory page:
- "Home" nav link → `/`
- "Observatory" nav link → active (current page)

On the task lifecycle page:
- "Observatory" nav link → `/observatory.html`
- "Task Lifecycle" nav link → active (current page)
- "Create Task" nav link → resets demo to step 0

---

## Task 1: Extract Shared CSS into `assets/style.css`

**Files:**
- Create: `services/ui/data/web/assets/style.css`

**What:** Extract all CSS that's common across the three mockups into a single shared stylesheet. This includes:
- Reset & base styles (box-sizing, body, scrollbar)
- CSS custom properties (`:root` variables)
- Utility classes (.up, .down, .muted, .dim, .cyan, .yellow, .orange, .bold, .mono, .label)
- Animations (pulse-glow, ticker-scroll, fade-in-up, slide-in, flash-green, flash-red, bar-rise, counter-glow, sparkle, slide-right, scale-in, typing, blink-caret, progress-fill, gavel-swing)
- Top nav styles (.topnav / .nav + variants)
- Bottom ticker styles (.bottom-ticker, .ticker-track, .ticker-item, .ticker-fade-l/r, .bt-item, .bt-badge)
- Footer styles (.footer)
- Feed badge colors (.badge-task, .badge-bid, .badge-payout, etc.)
- Button styles (.btn, .btn-cyan, .btn-green, .btn-red, .btn-amber, .nav-btn)
- Status badges (.status-badge, .status-open, .status-bidding, etc.)

**Step 1:** Create the `assets/` directory and the shared CSS file.

Extract all CSS variables and shared styles. Use the superset of variables from all three mockups (the task lifecycle mockup has the most variables — use that as the base `:root`). Include ALL animation keyframes from all three mockups.

For the nav, unify the two nav patterns:
- Landing page uses `.nav` with `.nav-brand`, `.nav-btn`
- Observatory/task use `.topnav` with `.topnav-brand`, `.topnav-link`

Keep BOTH patterns in the shared CSS since the landing page nav has a different scale/layout than the inner pages.

**Step 2:** Verify the file was created correctly.

Run: `ls -la services/ui/data/web/assets/style.css`
Expected: File exists

**Step 3:** Commit.

```bash
git add services/ui/data/web/assets/style.css
git commit -m "feat(ui): extract shared CSS into assets/style.css"
```

---

## Task 2: Extract Shared JS into `assets/shared.js`

**Files:**
- Create: `services/ui/data/web/assets/shared.js`

**What:** Extract JavaScript that's shared across pages:

1. **AGENTS roster** — the 10 mock agents array (used by all three pages). Use the richer version from the observatory mockup that includes `earned`, `spent`, `tc`, `tp`, `dq`, `sq`, `streak` fields.

2. **Economy state object `S`** — the full economy state (GDP, agents, tasks, escrow, specQ, labor, phase, rewardDist). Use the observatory version as the canonical state.

3. **Utility functions:**
   - `sparkData(n, base, variance)` — generates sparkline data arrays
   - `renderSparkSVG(data, w, h, fill)` — renders SVG sparkline
   - `genSparkline(n, base, variance)` — alias (landing page name)
   - `pick(arr)` — random array element
   - `randHex()` — random hex string
   - `timeAgo(ms)` — formats milliseconds as "Xs ago", "Xm ago"
   - `animateCounter(el, from, to, duration, suffix)` — counting animation

4. **Bottom ticker builder** — `buildBottomTicker(trackElementId)` function that renders the scrolling bottom ticker (used by observatory and task pages).

5. **Top ticker builder** — `buildTopTicker(trackElementId, state)` function (used by landing page).

6. **Live update engine** — `startEconomyPerturbation(callback)` that perturbs the economy state every 2-3 seconds and calls back so each page can update its UI.

All exports via a global `ATE` namespace object: `window.ATE = { AGENTS, S, sparkData, ... }`.

**Step 1:** Create the shared JS file with all the above.

**Step 2:** Verify the file was created.

Run: `ls -la services/ui/data/web/assets/shared.js`

**Step 3:** Commit.

```bash
git add services/ui/data/web/assets/shared.js
git commit -m "feat(ui): extract shared JS utilities into assets/shared.js"
```

---

## Task 3: Refactor Landing Page (`index.html`)

**Files:**
- Modify: `services/ui/data/web/index.html`
- Create: `services/ui/data/web/assets/landing.js`

**What:** Refactor the current monolithic `index.html` to:

1. Replace the inline `<style>` block with `<link rel="stylesheet" href="/assets/style.css">` plus a small `<style>` block for landing-page-only styles (hero, KPI strip, exchange board, story section, how-it-works, leaderboard, CTA band — styles that ONLY appear on the landing page).

2. Replace the inline `<script>` block with:
   ```html
   <script src="/assets/shared.js"></script>
   <script src="/assets/landing.js"></script>
   ```

3. Update navigation links to point to actual pages:
   - "Observatory" button → `href="/observatory.html"` (or `onclick="window.location='/observatory.html'"`)
   - "Enter the Economy" button → `href="/task.html"`
   - "Watch Live Agents" button → `href="/observatory.html"`
   - "Post a Task" button → `href="/task.html"`
   - "Register Your Agent" button → `alert('Agent registration coming soon')` for now
   - "Explore the data →" link → keep as scroll-to-board

4. Move landing-specific JS into `assets/landing.js`:
   - `buildKPIStrip()` — uses `ATE.S` and `ATE.animateCounter`
   - `buildExchangeBoard()` — uses `ATE.S` and `ATE.genSparkline`
   - `buildLeaderboard()` — uses `ATE.agentStats` or `ATE.AGENTS`
   - `buildNewsTicker()` — bottom news ticker for landing page
   - `startLiveUpdates()` — uses `ATE.startEconomyPerturbation`
   - Story rotation logic
   - Boot sequence

**Step 1:** Create `assets/landing.js` with all landing-specific JS, referencing `ATE.*` globals.

**Step 2:** Rewrite `index.html` — remove inline CSS/JS, add external links, update nav buttons.

**Step 3:** Start the UI service and test in browser.

Run from `services/ui/`: `just run` (background)
Then navigate to `http://127.0.0.1:8008/` using Playwright MCP.

**Verify:**
- Page loads without JS errors (check browser console)
- Ticker scrolls
- KPI strip animates
- Exchange board renders with sparklines
- Leaderboard shows workers and posters
- "Observatory" button navigates to `/observatory.html` (will 404 for now — that's OK)
- "Enter the Economy" button navigates to `/task.html` (will 404 for now — that's OK)

**Step 4:** Commit.

```bash
git add services/ui/data/web/index.html services/ui/data/web/assets/landing.js
git commit -m "refactor(ui): extract landing page CSS/JS into external files, wire nav links"
```

---

## Task 4: Create Observatory Page (`observatory.html`)

**Files:**
- Create: `services/ui/data/web/observatory.html`
- Create: `services/ui/data/web/assets/observatory.js`

**What:** Build the observatory page from the `nyse-live-ticker.html` mockup.

**HTML structure** (from mockup):
- Top nav (shared pattern: `.topnav` with Home / Observatory / Task Lifecycle links)
- Vitals bar (`.vitals` — horizontal strip of key metrics with LIVE indicator)
- 3-column main layout:
  - Left (30%): GDP panel with sparkline charts, economy phase, labor market stats, reward distribution bars
  - Center (flex): Live feed with filter buttons, pause/resume, scrolling event list
  - Right (230px): Tabbed leaderboard (Workers / Posters) with spec quality section
- Bottom ticker

**Step 1:** Create `observatory.html` with the HTML structure. Link shared CSS + observatory-specific inline styles (for observatory-only classes like `.gdp-panel`, `.feed-panel`, `.vitals`, etc.). Link `shared.js` + `observatory.js`.

The nav should have:
```html
<span class="topnav-link" onclick="window.location='/'">Home</span>
<span class="topnav-link active">Observatory</span>
<span class="topnav-link" onclick="window.location='/task.html'">Task Lifecycle</span>
```

**Step 2:** Create `observatory.js` with:
- `buildVitals()` — renders the vitals bar from `ATE.S`
- `buildGDPPanel()` — renders GDP section with sparklines using `ATE.renderSparkSVG`
- Live feed engine:
  - Event templates array (TASK, BID, PAYOUT, CONTRACT, ESCROW, SUBMIT, REP, DISPUTE, RULING, SALARY, AGENT)
  - Filter buttons (ALL + each type)
  - Pause/resume toggle
  - Event generation every 3 seconds
  - Feed rendering with badges and timestamps
- `renderLeaderboard()` — tabbed Workers/Posters with quality stars
- `buildBottomTicker()` — or use `ATE.buildBottomTicker` from shared
- Live state perturbation via `ATE.startEconomyPerturbation`
- Boot sequence

**Step 3:** Test in browser via Playwright MCP.

Navigate to `http://127.0.0.1:8008/observatory.html`.

**Verify:**
- Page loads, all three columns visible
- GDP panel shows sparkline charts
- Live feed populates with events every 3 seconds
- Filter buttons work (clicking "TASK" shows only task events, "ALL" shows everything)
- Pause button stops feed, Resume continues it
- Worker/Poster tabs switch in leaderboard
- Bottom ticker scrolls
- "Home" link goes back to landing page
- "Task Lifecycle" link goes to `/task.html`
- Vitals bar updates periodically

**Step 4:** Commit.

```bash
git add services/ui/data/web/observatory.html services/ui/data/web/assets/observatory.js
git commit -m "feat(ui): add observatory page with live feed, GDP panel, and leaderboard"
```

---

## Task 5: Create Task Lifecycle Page (`task.html`)

**Files:**
- Create: `services/ui/data/web/task.html`
- Create: `services/ui/data/web/assets/task.js`

**What:** Build the task lifecycle demo page from the `nyse-task-lifecycle.html` mockup.

**HTML structure** (from mockup):
- Top nav with Observatory / Task Lifecycle / Create Task links
- Phase strip (7 phases: Post, Bid, Contract, Deliver, Review, Ruling, Settle)
- 2-column main layout:
  - Left (flex): Lifecycle panel with step content (form, bids, contract, deliverable, dispute, ruling, feedback)
  - Right (360px): Live event feed (contextual to the demo steps)
- Bottom ticker
- Demo controls bar (fixed at bottom: PREV, progress bar, step label, NEXT, AUTO)

**Step 1:** Create `task.html` with the HTML structure. Link shared CSS + task-specific inline styles (for `.lifecycle-panel`, `.phase-strip`, `.card`, `.form-*`, `.bid-*`, `.escrow-bar`, `.dispute-panel`, `.ruling-card`, `.feedback-*`, `.demo-controls`, etc.).

The nav should have:
```html
<span class="topnav-link" onclick="window.location='/observatory.html'">Observatory</span>
<span class="topnav-link active">Task Lifecycle</span>
<span class="topnav-link" onclick="resetDemo()">Create Task</span>
```

**Step 2:** Create `task.js` with the full demo engine:

- **Task data** (from mockup): TASK, BIDS, DELIVERABLE, DISPUTE_REASON, REBUTTAL, RULING objects
- **12 STEPS** definition array (phase, title, status, statusCls, label)
- **12 RENDERERS** array: renderCreateForm, renderTaskPosted, renderBids(1), renderBids(2), renderContract, renderDeliverable, renderReview, renderDispute, renderRebuttal, renderRuling, renderSettlement, renderFeedback
- **Feed events per step** (FEED_EVENTS array)
- **Step navigation**: goToStep(n), renderPhaseStrip(step), addFeedEvents(step)
- **Controls**: NEXT/PREV buttons, AUTO play (3s interval), keyboard navigation (arrow keys, spacebar)
- **Bottom ticker**: using `ATE.buildBottomTicker` or inline
- **resetDemo()**: goes back to step 0

**Step 3:** Test in browser via Playwright MCP.

Navigate to `http://127.0.0.1:8008/task.html`.

**Verify:**
- Page loads with task creation form visible (step 1/12)
- NEXT button advances through all 12 steps
- PREV button goes back
- Phase strip highlights current phase and marks completed phases with ✓
- Each step shows correct content:
  - Step 1: Task creation form with title, spec, reward, deadline
  - Step 2: Task posted with escrow bar showing "LOCKED"
  - Steps 3-4: Bids appear one at a time
  - Step 5: Contract signed card with signatures
  - Step 6: Deliverable displayed
  - Step 7: Review with issue detected
  - Step 8: Dispute filed panel
  - Step 9: Worker rebuttal panel
  - Step 10: Court ruling with scores (Spec: 100%, Delivery: 40%)
  - Step 11: Settlement with payout boxes
  - Step 12: Feedback exchange + reputation update
- AUTO button plays through all steps at 3s intervals
- Event feed on right side accumulates events as steps progress
- Arrow keys navigate between steps
- "Observatory" link goes to observatory page
- "Create Task" link resets to step 0

**Step 4:** Commit.

```bash
git add services/ui/data/web/task.html services/ui/data/web/assets/task.js
git commit -m "feat(ui): add task lifecycle demo page with step-by-step walkthrough"
```

---

## Task 6: Cross-Page Testing and Polish

**Files:**
- Possibly modify: all HTML files for minor fixes

**What:** Full end-to-end testing of all three pages and their interconnections.

**Step 1:** Start the UI service.

Run from `services/ui/`: `just run`

**Step 2:** Test full navigation flow via Playwright MCP.

1. Load `http://127.0.0.1:8008/` — verify landing page
2. Click "Observatory" → verify observatory loads at `/observatory.html`
3. Click "Home" → verify back to landing
4. Click "Enter the Economy" → verify task page loads at `/task.html`
5. Step through the full 12-step demo
6. Click "Observatory" from task page → verify observatory loads
7. Navigate back to landing from observatory

**Step 3:** Test that no console errors appear on any page.

Use Playwright MCP `browser_console_messages` to check for JS errors after loading each page.

**Step 4:** Run existing UI service tests to make sure nothing broke.

Run from `services/ui/`: `just test`
Expected: All existing tests pass (health endpoint, config tests)

**Step 5:** Run CI checks.

Run from `services/ui/`: `just ci-quiet`
Expected: All checks pass

**Step 6:** Fix any issues found in testing. Commit fixes.

```bash
git add -A services/ui/data/web/
git commit -m "fix(ui): polish cross-page navigation and fix browser console errors"
```

---

## Task 7: Final Verification

**Step 1:** Run full project CI.

Run from project root: `just ci-all-quiet`
Expected: All services pass

**Step 2:** Manual browser walkthrough of the demo transcript flow.

Using Playwright MCP, simulate the 2-minute demo:
1. Landing page loads → hero visible, ticker scrolling, KPI counting up
2. Click "Observatory" → 3-column layout with live feed streaming
3. Pause feed → events stop → Resume → events continue
4. Switch leaderboard tab → Workers/Posters toggle
5. Click "Task Lifecycle" → task creation form
6. Click AUTO → watch all 12 steps play through
7. Verify dispute + ruling shows correctly
8. Final screen shows feedback exchange and reputation update

**Step 3:** Commit any remaining fixes and push.

---

## Notes for Step 2 (Future: API Endpoints)

After this plan is complete, the next step will be defining FastAPI endpoints in the UI service to replace mock data with real data from other services. The endpoints will include:

- `GET /api/economy` — aggregated economy state (GDP, tasks, escrow, labor market)
- `GET /api/agents` — agent list with stats
- `GET /api/feed` — recent economy events (with optional type filter)
- `GET /api/tasks/{task_id}` — task detail with full lifecycle state
- `POST /api/tasks` — create a new task (proxies to task-board service)
- `GET /api/leaderboard` — worker/poster rankings

These will be planned in a separate document after this UI work is complete.
