# UI Multi-Page Website — Codex Implementation Plan

## Files to Read First

1. `AGENTS.md` — project conventions (MUST read first)
2. `docs/plans/2026-03-02-ui-multi-page-website.md` — the design plan this task implements
3. `services/ui/src/ui_service/app.py` — the FastAPI app that serves static files
4. `services/ui/data/web/index.html` — current landing page (monolithic, to be refactored)
5. `docs/mockups/nyse-landing-page.html` — landing page mockup (source of truth for landing)
6. `docs/mockups/nyse-live-ticker.html` — observatory mockup (source of truth for observatory)
7. `docs/mockups/nyse-task-lifecycle.html` — task lifecycle mockup (source of truth for task demo)

## What You Are Doing

You are transforming three standalone HTML mockups into a functioning multi-page website. The existing `index.html` is a self-contained 839-line file with all CSS and JS inline. You will:

1. Extract shared CSS into `assets/style.css`
2. Extract shared JS into `assets/shared.js`
3. Refactor landing page to use external CSS/JS
4. Create observatory page from its mockup
5. Create task lifecycle page from its mockup
6. Wire all navigation links between pages

**No Python/FastAPI changes are needed.** The existing `app.py` already:
- Mounts `/assets/` as a static file directory
- Serves any file in `data/web/` by path (e.g., `/observatory.html`)
- Falls back to `index.html` for unknown paths

## File Structure After Implementation

```
services/ui/data/web/
├── index.html                # Landing page (refactored: CSS/JS extracted)
├── observatory.html          # Platform overview page (NEW)
├── task.html                 # Task lifecycle demo page (NEW)
└── assets/
    ├── style.css             # Shared CSS (NEW)
    ├── shared.js             # Shared JS utilities + data (NEW)
    ├── landing.js            # Landing page JS (NEW)
    ├── observatory.js        # Observatory page JS (NEW)
    └── task.js               # Task lifecycle page JS (NEW)
```

## Rules

- **Source of truth**: The three mockup HTML files in `docs/mockups/`. Copy CSS and JS from them — do NOT invent new styling or logic.
- **Do NOT modify** any Python source files or test files.
- **Do NOT modify** `app.py`, `config.py`, or any file under `src/`.
- **Use `uv run` for all Python execution** — never raw `python` or `pip install`.
- All three pages must use the same shared `assets/style.css` and `assets/shared.js`.
- Every page must link: `<link rel="stylesheet" href="/assets/style.css">` and `<script src="/assets/shared.js"></script>`.
- The mockups are self-contained — all CSS and JS is inline. Your job is to split them into shared + page-specific files.
- Keep all mock data exactly as it appears in the mockups. Do not simplify or remove features.
- Run tests after each phase. Commit after each phase.

---

## Phase 1: Create Shared CSS (`assets/style.css`)

### What to Do

Create `services/ui/data/web/assets/style.css` by extracting the CSS that is common across all three mockups.

### How to Extract

1. Read ALL THREE mockup files' `<style>` blocks completely.
2. Identify CSS that appears in 2+ mockups (or is clearly shared infrastructure).
3. The task lifecycle mockup (`nyse-task-lifecycle.html`) has the most complete `:root` variables — use it as the base.

### What Goes in `style.css`

**1. Reset & base styles** (from any mockup — they're identical):
```css
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 14px; }
body { font-family: var(--mono); background: var(--bg); color: var(--text); overflow-x: hidden; -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration: none; }
```

**2. CSS custom properties** — merge `:root` from ALL THREE mockups into one superset. The task lifecycle mockup has the most variables. Include ALL of these:
- From landing: `--bg`, `--bg-card`, `--bg-row`, `--border`, `--border-hi`, `--text`, `--text-mid`, `--text-dim`, `--green`, `--green-dim`, `--red`, `--red-dim`, `--cyan`, `--cyan-dim`, `--yellow`, `--orange`, `--accent`, `--mono`, `--scale`, `--hero-scale`
- From observatory (additional): `--bg-hover`, `--bg-off`, `--text-faint`, `--green-fill`, `--amber`, `--violet`
- From task lifecycle (additional): `--red-fill`, `--cyan-fill`, `--yellow-dim`, `--yellow-fill`, `--amber-dim`

**3. Utility classes** (from landing mockup):
```css
.up { color: var(--green); }
.down { color: var(--red); }
.muted { color: var(--text-mid); }
.dim { color: var(--text-dim); }
.cyan { color: var(--cyan); }
.yellow { color: var(--yellow); }
.orange { color: var(--orange); }
.bold { font-weight: 700; }
.mono { font-family: var(--mono); }
.label { font-size: calc(10px * var(--scale)); ... }
```

**4. ALL animation @keyframes** — collect from ALL THREE mockups. Include at minimum:
- `pulse-glow`, `ticker-scroll`, `fade-in-up`, `slide-in`, `flash-green`, `flash-red`
- `bar-rise`, `counter-glow`, `sparkle`, `slide-right`, `scale-in`
- `typing`, `blink-caret`, `progress-fill`, `gavel-swing`
- Any others found in the mockups

**5. Scrollbar styles** (from landing):
```css
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 3px; }
```

**6. Navigation styles** — BOTH patterns:
- `.nav` (landing page nav) — from `nyse-landing-page.html`
- `.topnav` (inner page nav) — from `nyse-live-ticker.html` and `nyse-task-lifecycle.html`
- Include `.nav-brand`, `.nav-btn`, `.topnav-brand`, `.topnav-link`, `.topnav-link.active`, `.live-dot`

**7. Bottom ticker styles** — from observatory/task mockups:
- `.bottom-ticker`, `.ticker-track`, `.ticker-item`, `.bt-item`, `.bt-badge`
- `.ticker-fade-l`, `.ticker-fade-r`

**8. Footer styles** — `.footer` (from landing mockup)

**9. Feed badge colors** — from observatory and task mockups:
- `.badge-task`, `.badge-bid`, `.badge-payout`, `.badge-contract`, `.badge-escrow`
- `.badge-submit`, `.badge-rep`, `.badge-dispute`, `.badge-ruling`, `.badge-salary`, `.badge-agent`

**10. Button styles** — `.btn`, `.btn-cyan`, `.btn-green`, `.btn-red`, `.btn-amber`, `.btn-outline`, button hover states

**11. Status badges** — `.status-badge`, `.status-open`, `.status-bidding`, `.status-in-progress`, `.status-review`, `.status-disputed`, `.status-settled`, `.status-approved`

### How to Verify

```bash
ls -la services/ui/data/web/assets/style.css
wc -l services/ui/data/web/assets/style.css
```

Expected: File exists, ~400-600 lines.

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/assets/style.css
git commit -m "feat(ui): extract shared CSS into assets/style.css"
```

---

## Phase 2: Create Shared JS (`assets/shared.js`)

### What to Do

Create `services/ui/data/web/assets/shared.js` with all JavaScript shared across pages.

### What Goes in `shared.js`

Everything is exported via `window.ATE = { ... }` at the bottom.

**1. AGENTS roster** — use the RICHER version from `nyse-live-ticker.html` (observatory mockup). It has these fields per agent: `name`, `role`, `earned`, `spent`, `tc` (tasks completed), `tp` (tasks posted), `dq` (delivery quality), `sq` (spec quality), `streak`. The landing page mockup has a simpler version — DO NOT use that. Use the observatory version because it's a superset.

**2. Economy state `S`** — use the version from `nyse-live-ticker.html`:
```javascript
const S = {
  gdp: ..., agents: ..., tasks: ..., openTasks: ...,
  escrow: ..., specQ: ..., laborMarket: ...,
  phase: ..., rewardDist: { ... }
};
```

**3. Utility functions** — copy these from the mockups:
- `pick(arr)` — random element from array
- `randHex()` — random hex string
- `timeAgo(ms)` — format milliseconds as "Xs ago"
- `sparkData(n, base, variance)` — generate array of random sparkline data
- `renderSparkSVG(data, w, h, color)` — render SVG sparkline string (from observatory mockup, this is the more complete version)
- `genSparkline(n, base, variance)` — alias used by landing page (calls `sparkData` + `renderSparkSVG` or generates its own inline SVG — check both mockups for the right version)
- `animateCounter(el, from, to, duration, suffix)` — counting animation

**4. Economy perturbation engine** — copy from observatory mockup. This function mutates `S` every 2-3 seconds (random GDP changes, task counts, escrow, spec quality). It takes a callback so each page can update its UI when state changes.

**5. Bottom ticker builder** — `buildBottomTicker(trackEl)` — generates the scrolling news ticker at the bottom. Used by observatory and task pages. Copy from the observatory mockup.

**6. Top ticker data generator** — the landing page has a top ticker with different content. Include a function that generates ticker items from the economy state.

### Export Pattern

```javascript
window.ATE = {
  AGENTS, S,
  pick, randHex, timeAgo,
  sparkData, renderSparkSVG, genSparkline,
  animateCounter,
  perturbEconomy,   // or startEconomyPerturbation
  buildBottomTicker,
};
```

### How to Verify

```bash
ls -la services/ui/data/web/assets/shared.js
wc -l services/ui/data/web/assets/shared.js
```

Expected: File exists, ~200-350 lines.

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/assets/shared.js
git commit -m "feat(ui): extract shared JS utilities into assets/shared.js"
```

---

## Phase 3: Create Landing Page JS (`assets/landing.js`) and Refactor `index.html`

### What to Do

1. Create `services/ui/data/web/assets/landing.js` with landing-page-specific JavaScript.
2. Rewrite `services/ui/data/web/index.html` — remove inline CSS/JS, add external links, wire navigation.

### `assets/landing.js` Contents

Move all landing-page-specific JS from the current `index.html` into this file. References `ATE.*` globals from `shared.js`. Include:

- `buildKPIStrip()` — builds KPI cards using `ATE.S` and `ATE.animateCounter`
- `buildExchangeBoard()` — builds the 15-cell exchange grid using `ATE.S` and `ATE.genSparkline`
- `buildLeaderboard()` — builds the agent leaderboard table
- `buildNewsTrack()` — builds the bottom news ticker for landing page
- `buildTopTicker()` — builds the top scrolling ticker strip
- Story rotation logic (if any)
- `startLiveUpdates()` — uses `ATE.perturbEconomy` or `ATE.startEconomyPerturbation`
- Boot/init sequence: `document.addEventListener('DOMContentLoaded', ...)` calling all the builders

### `index.html` Changes

1. **Remove** the entire `<style>...</style>` block (~300+ lines of CSS).
2. **Add** at the top of `<head>`: `<link rel="stylesheet" href="/assets/style.css">`
3. **Add** a small `<style>` block for LANDING-ONLY styles (styles that appear ONLY on the landing page and NOT on observatory or task pages). These include:
   - `.hero`, `.hero h1`, `.hero .sub`, `.hero .tagline`
   - `.kpi-strip`, `.kpi-card`, `.kpi-val`, `.kpi-label`, `.kpi-delta`
   - `.exchange-board`, `.cell`, `.cell-header`, `.cell-val`, `.cell-change`, `.cell-spark`
   - `.how-it-works`, `.hw-steps`, `.hw-step`, `.hw-num`, `.hw-title`, `.hw-desc`
   - `.story-section`, `.story-card`, `.story-header`, `.story-body`
   - `.leaderboard`, `.lb-header`, `.lb-row`, `.lb-rank`, `.lb-agent`, `.lb-score`
   - `.cta-band`
   - Any other styles unique to the landing page
4. **Remove** the entire `<script>...</script>` block (~200+ lines of JS).
5. **Add** before `</body>`:
   ```html
   <script src="/assets/shared.js"></script>
   <script src="/assets/landing.js"></script>
   ```
6. **Wire navigation links** — update buttons/links in the HTML:
   - "Observatory" nav button → `onclick="window.location='/observatory.html'"`
   - "Enter the Economy" button (hero CTA) → `onclick="window.location='/task.html'"`
   - "Watch Live Agents" button → `onclick="window.location='/observatory.html'"`
   - "Post a Task" button → `onclick="window.location='/task.html'"`
   - "Register Your Agent" button → `onclick="alert('Agent registration coming soon')"`
   - "Explore the data →" → keep as scroll-to-section (already works)

### How to Verify

Start the UI service and load the landing page:

```bash
cd /Users/flo/Developer/github/agent-economy/services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port 8008 &
```

Then open `http://127.0.0.1:8008/` in a browser and verify:
- Page loads without errors
- Top ticker scrolls
- KPI strip shows animated numbers
- Exchange board renders with sparklines
- Leaderboard shows agent rankings
- Navigation buttons are clickable
- "Observatory" button goes to `/observatory.html` (will 404 — that's OK for now)

Kill the server when done: `kill %1` or `pkill -f "uvicorn ui_service"`

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/index.html services/ui/data/web/assets/landing.js
git commit -m "refactor(ui): extract landing page CSS/JS into external files"
```

---

## Phase 4: Create Observatory Page

### What to Do

Create two new files:
1. `services/ui/data/web/observatory.html`
2. `services/ui/data/web/assets/observatory.js`

### Source

Copy from `docs/mockups/nyse-live-ticker.html`. This mockup is ~836 lines with inline CSS and JS. You need to split it.

### `observatory.html` Contents

The HTML structure from the mockup, with these changes:
- **Remove** the `<style>` block. Replace with `<link rel="stylesheet" href="/assets/style.css">`.
- **Add** a small `<style>` block for OBSERVATORY-ONLY styles. These are styles that appear ONLY in the observatory mockup and not in the landing or task mockups:
  - `.vitals`, `.vital-item`, `.vital-label`, `.vital-val`
  - `.main-3col` (3-column layout)
  - `.gdp-panel`, `.gdp-title`, `.gdp-block`, `.gdp-big-val`, `.gdp-row`, `.gdp-label`, `.gdp-val`
  - `.chart-box`, `.phase-label`, `.labor-bar`, `.labor-fill`
  - `.reward-bar`, `.reward-fill`, `.reward-label`, `.reward-pct`
  - `.feed-panel`, `.feed-header`, `.feed-filters`, `.filter-btn`, `.filter-btn.active`
  - `.feed-list`, `.feed-item`, `.feed-badge`, `.feed-text`, `.feed-time`
  - `.pause-btn`
  - `.lb-panel`, `.lb-tabs`, `.lb-tab`, `.lb-tab.active`, `.lb-list`, `.lb-card`
  - `.lb-rank`, `.lb-name`, `.lb-stats`, `.lb-bar`, `.lb-fill`
  - `.spec-section`, `.sq-meter`, `.sq-bar`, `.sq-fill`
  - Any other observatory-only styles
- **Remove** the `<script>` block. Replace with:
  ```html
  <script src="/assets/shared.js"></script>
  <script src="/assets/observatory.js"></script>
  ```
- **Update nav links** in the `.topnav`:
  ```html
  <span class="topnav-link" onclick="window.location='/'">Home</span>
  <span class="topnav-link active">Observatory</span>
  <span class="topnav-link" onclick="window.location='/task.html'">Task Lifecycle</span>
  ```

### `observatory.js` Contents

All observatory-specific JS from the mockup, referencing `ATE.*` globals:

- `buildVitals()` — renders vitals bar from `ATE.S`
- `buildGDPPanel()` — renders GDP section with sparklines via `ATE.renderSparkSVG`
- Live feed engine:
  - Event template array (11 types: TASK, BID, PAYOUT, CONTRACT, ESCROW, SUBMIT, REP, DISPUTE, RULING, SALARY, AGENT)
  - `generateEvent()` — creates random event from templates using `ATE.pick`, `ATE.AGENTS`
  - `renderFeedItem(event)` — renders a feed item DOM element
  - `addEvent()` — adds event to feed list, prunes old events
  - Filter button handlers (ALL + each type)
  - Pause/resume toggle
  - Interval that fires every 2-3 seconds
- `renderLeaderboard(tab)` — tabbed Workers/Posters from `ATE.AGENTS`
- Spec quality meter rendering
- `ATE.buildBottomTicker(...)` call for bottom ticker
- Economy state perturbation via `ATE.perturbEconomy` or `ATE.startEconomyPerturbation`
- Boot sequence: `document.addEventListener('DOMContentLoaded', ...)`

### How to Verify

Start the UI service (same as Phase 3) and open `http://127.0.0.1:8008/observatory.html`. Verify:
- Three columns visible: GDP panel (left), live feed (center), leaderboard (right)
- Vitals bar across the top with economy metrics
- GDP panel has sparkline charts
- Live feed shows events appearing every 2-3 seconds
- Filter buttons work (click "TASK" to see only task events, "ALL" for everything)
- Pause button stops new events, Resume continues
- Worker/Poster tabs switch in leaderboard
- Bottom ticker scrolls
- "Home" link navigates to `/` (landing page)
- "Task Lifecycle" link navigates to `/task.html` (will 404 until Phase 5)
- No console errors

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/observatory.html services/ui/data/web/assets/observatory.js
git commit -m "feat(ui): add observatory page with live feed, GDP panel, and leaderboard"
```

---

## Phase 5: Create Task Lifecycle Page

### What to Do

Create two new files:
1. `services/ui/data/web/task.html`
2. `services/ui/data/web/assets/task.js`

### Source

Copy from `docs/mockups/nyse-task-lifecycle.html`. This mockup is ~1069 lines with inline CSS and JS. You need to split it.

### `task.html` Contents

The HTML structure from the mockup, with these changes:
- **Remove** the `<style>` block. Replace with `<link rel="stylesheet" href="/assets/style.css">`.
- **Add** a small `<style>` block for TASK-ONLY styles:
  - `.lifecycle-panel`, `.phase-strip`, `.phase-item`, `.phase-item.active`, `.phase-item.done`
  - `.step-content`, `.card` (as used in task context)
  - `.form-group`, `.form-label`, `.form-input`, `.form-textarea`, `.form-row`, `.form-hint`
  - `.task-posted`, `.escrow-bar`, `.escrow-locked`, `.escrow-fill`
  - `.bid-card`, `.bid-header`, `.bid-body`, `.bid-stat`, `.bid-accept-btn`
  - `.contract-card`, `.contract-row`, `.sig-line`, `.sig-check`
  - `.deliverable-card`, `.deliverable-body`, `.deliverable-file`
  - `.review-card`, `.review-issue`
  - `.dispute-panel`, `.dispute-header`, `.dispute-body`
  - `.rebuttal-panel`
  - `.ruling-card`, `.ruling-header`, `.ruling-scores`, `.score-ring`, `.score-label`
  - `.settlement-card`, `.payout-boxes`, `.payout-box`, `.payout-amount`, `.payout-label`
  - `.feedback-card`, `.feedback-row`, `.feedback-stars`, `.reputation-update`
  - `.demo-controls`, `.demo-bar`, `.demo-progress`, `.demo-fill`, `.demo-label`
  - `.ctrl-btn`, `.auto-btn`
  - `.event-feed-panel`, `.ef-header`, `.ef-list`, `.ef-item`
  - Any other task-only styles
- **Remove** the `<script>` block. Replace with:
  ```html
  <script src="/assets/shared.js"></script>
  <script src="/assets/task.js"></script>
  ```
- **Update nav links** in the `.topnav`:
  ```html
  <span class="topnav-link" onclick="window.location='/'">Home</span>
  <span class="topnav-link" onclick="window.location='/observatory.html'">Observatory</span>
  <span class="topnav-link active">Task Lifecycle</span>
  <span class="topnav-link" onclick="resetDemo()">Create Task</span>
  ```

### `task.js` Contents

All task-lifecycle-specific JS from the mockup, referencing `ATE.*` globals:

**1. Demo data objects** — copy EXACTLY from the mockup:
- `TASK` object (title, spec, reward, deadline, poster)
- `BIDS` array (2 bids with agent, price, eta, pitch, quality)
- `DELIVERABLE` object (content, files, delivered_at)
- `DISPUTE_REASON` string
- `REBUTTAL` string
- `RULING` object (spec_score, delivery_score, payout_split, summary, judges)

**2. STEPS array** — 12 steps, each with: `phase` (0-6), `title`, `status`, `statusCls`, `label`

**3. FEED_EVENTS array** — events for each step (array of arrays, 12 entries)

**4. 12 renderer functions**:
- `renderCreateForm()` — task creation form
- `renderTaskPosted()` — task posted confirmation with escrow
- `renderBids1()` — first bid appears
- `renderBids2()` — second bid appears
- `renderContract()` — contract signed
- `renderDeliverable()` — deliverable submitted
- `renderReview()` — review with issue detected
- `renderDispute()` — dispute filed
- `renderRebuttal()` — worker rebuttal
- `renderRuling()` — court ruling with scores
- `renderSettlement()` — payout distribution
- `renderFeedback()` — feedback exchange + reputation update

**5. RENDERERS array** — maps step index to renderer function

**6. Navigation functions**:
- `goToStep(n)` — sets current step, calls renderer, updates phase strip, adds feed events
- `renderPhaseStrip(step)` — highlights current phase, marks completed phases with checkmark
- `addFeedEvents(step)` — adds contextual events to the right-side feed
- `nextStep()`, `prevStep()` — increment/decrement step
- `toggleAuto()` — starts/stops auto-play at 3-second intervals
- `resetDemo()` — goes back to step 0

**7. Keyboard navigation**: arrow keys (left/right), spacebar (toggle auto)

**8. Bottom ticker**: call `ATE.buildBottomTicker(...)` if available

**9. Boot sequence**: `document.addEventListener('DOMContentLoaded', ...)` — initialize at step 0

### How to Verify

Start the UI service and open `http://127.0.0.1:8008/task.html`. Verify:
- Page loads with task creation form (step 1 of 12)
- Phase strip shows "Post" highlighted
- NEXT button advances through all 12 steps
- PREV button goes back
- Phase strip updates: current phase highlighted, completed phases show ✓
- Each step shows its correct content (form → posted → bids → contract → deliver → review → dispute → rebuttal → ruling → settlement → feedback)
- Event feed on right side accumulates events as steps progress
- AUTO button plays through all steps at 3-second intervals
- Arrow keys navigate (left = prev, right = next)
- Spacebar toggles auto-play
- "Home" link goes to `/`
- "Observatory" link goes to `/observatory.html`
- "Create Task" resets to step 0
- No console errors

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/task.html services/ui/data/web/assets/task.js
git commit -m "feat(ui): add task lifecycle demo page with 12-step walkthrough"
```

---

## Phase 6: Cross-Page Navigation Testing and Fixes

### What to Do

Test the full navigation flow between all three pages. Fix any issues.

### Test Procedure

1. Start the UI service:
   ```bash
   cd /Users/flo/Developer/github/agent-economy/services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port 8008 &
   ```

2. Test navigation flow:
   - Load `http://127.0.0.1:8008/` → landing page loads
   - Click "Observatory" → `/observatory.html` loads
   - Click "Home" → back to landing
   - Click "Enter the Economy" → `/task.html` loads
   - Click NEXT a few times → steps advance
   - Click "Observatory" → observatory loads
   - Click "Task Lifecycle" → back to task page
   - Click "Home" from observatory → landing loads

3. Check browser console on each page for JS errors.

4. Verify no broken styles on any page (things should look correct and match the mockups).

### Run Existing Tests

```bash
cd /Users/flo/Developer/github/agent-economy/services/ui && just test
```

Expected: All existing tests pass (health endpoint, config).

### Run CI

```bash
cd /Users/flo/Developer/github/agent-economy/services/ui && just ci-quiet
```

Expected: All checks pass. If there are any failures, fix them.

### Commit (if fixes were needed)

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/
git commit -m "fix(ui): polish cross-page navigation and fix browser errors"
```

---

## Phase 7: Final Verification

### Run Full Project CI

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

All 8 services must pass. The UI service changes are static files only, so other services should be unaffected.

### Final Commit (if any fixes)

```bash
cd /Users/flo/Developer/github/agent-economy
git add -A services/ui/data/web/
git commit -m "fix(ui): final polish for multi-page website"
```

---

## Important Notes

- The mockup files in `docs/mockups/` are the SINGLE SOURCE OF TRUTH for styling and behavior. Copy from them faithfully.
- The current `index.html` is already identical to `docs/mockups/nyse-landing-page.html`. You can verify: `diff services/ui/data/web/index.html docs/mockups/nyse-landing-page.html` (they should match).
- Do NOT add any npm, webpack, vite, or other build tooling. This is plain HTML/CSS/JS.
- Do NOT create any new Python files or modify existing Python files.
- Do NOT create any test files for HTML/CSS/JS — there is no frontend test infrastructure.
- The `assets/` directory does not exist yet — you need to create it: `mkdir -p services/ui/data/web/assets/`
- When extracting CSS, if a style appears in multiple mockups with slight differences, use the version from the mockup where that style is most heavily used.
- The landing page mockup uses `.nav` for navigation. The observatory and task mockups use `.topnav`. Keep BOTH in the shared CSS.
- The economy state object `S` and AGENTS array must be identical across all pages — that's why they're in `shared.js`.

## Total: 5 new files + 1 refactored file across 7 phases
