# Tier 2B: Remaining Bug Fixes Implementation Plan

## Pre-flight

Read these files before starting:
1. `services/ui/data/web/assets/shared.js`
2. `services/ui/data/web/assets/observatory.js`
3. `services/ui/data/web/assets/landing.js`
4. `services/ui/data/web/index.html`
5. `services/ui/src/ui_service/schemas.py`

## Rules
- Do NOT use git (no git in this project)
- Use `uv run` for all Python commands
- Run `cd services/ui && just ci-quiet` after ALL fixes are done (not after each one)
- Do NOT modify any existing test files
- Do NOT create any new test files (test coverage already exists or is out of scope)

---

## Fix 1: Observatory trending (ticket z60)

**Problem:** In `observatory.js` `buildGDPPanel()` line 48, the "Task creation" row hardcodes `↑ trending` in green. The API already returns `economy_phase.task_creation_trend` (a string like "growing", "stable", "declining") but the frontend doesn't capture it.

### Step 1A: Add `taskCreationTrend` to S defaults in `shared.js`

In the `S` object definition (around line 7-16), the `phase` field exists on line 14. Change line 14 from:

```
phase: 'bootstrapping',
```

to:

```
phase: 'bootstrapping',
taskCreationTrend: 'stable',
```

### Step 1B: Map API field in `fetchMetrics()` in `shared.js`

After line 137 (`S.phase = data.economy_phase.phase;`), add:

```javascript
      S.taskCreationTrend = data.economy_phase.task_creation_trend || 'stable';
```

### Step 1C: Use real value in `observatory.js` `buildGDPPanel()`

In line 48, find the hardcoded:
```
<span class="gdp-detail-value" style="color:var(--green)">↑ trending</span>
```

Replace with dynamic value that uses `S.taskCreationTrend`:
- If trend is "growing": show `↑ growing` in green
- If trend is "declining": show `↓ declining` in red
- If trend is "stable": show `→ stable` in amber (or text color)

The replacement HTML fragment for the "Task creation" detail row should be:
```javascript
'<span class="gdp-detail-value" style="color:' + (S.taskCreationTrend === 'growing' ? 'var(--green)' : S.taskCreationTrend === 'declining' ? 'var(--red)' : 'var(--amber)') + '">' + (S.taskCreationTrend === 'growing' ? '\u2191' : S.taskCreationTrend === 'declining' ? '\u2193' : '\u2192') + ' ' + S.taskCreationTrend + '</span>'
```

**IMPORTANT:** Line 48 is a very long single line. You need to find the exact substring `<span class="gdp-detail-value" style="color:var(--green)">\u2191 trending</span>` within that line and replace it.

The Unicode characters are:
- `\u2191` = ↑ (up arrow)
- `\u2193` = ↓ (down arrow)
- `\u2192` = → (right arrow)

---

## Fix 2: Dispute rate conditional coloring (ticket d2n)

**Problem:** In `observatory.js` `buildGDPPanel()` line 48, the dispute rate percentage always renders in green (`color:var(--green)`) regardless of value.

### Step 2A: In line 48 of `observatory.js`, find the dispute rate fragment

Find this exact substring within the long line 48:
```
<span class="gdp-detail-value" style="color:var(--green)">
```
...followed immediately by the dispute rate computation `((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1) + '%'`.

The full fragment to find is:
```
<span class="gdp-detail-value" style="color:var(--green)">' + ((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1) + '%</span>
```

### Step 2B: Replace with conditional coloring

Replace with:
```javascript
<span class="gdp-detail-value" style="color:' + (function() { var dr = (S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100; return dr > 15 ? 'var(--red)' : dr > 5 ? 'var(--amber)' : 'var(--green)'; })() + '">' + ((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1) + '%</span>
```

**Alternative approach** (cleaner): Before line 45 (`panel.innerHTML = ...`), compute the dispute rate and color as local variables:

```javascript
    var disputeRate = (S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100;
    var disputeColor = disputeRate > 15 ? 'var(--red)' : disputeRate > 5 ? 'var(--amber)' : 'var(--green)';
```

Then in line 48, replace:
```
style="color:var(--green)">' + ((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1) + '%
```
with:
```
style="color:' + disputeColor + '">' + disputeRate.toFixed(1) + '%
```

Use whichever approach is cleaner. The cleaner approach with local variables is preferred.

---

## Fix 3: Story text hardcoded fake names (ticket kkg)

**Problem:** `index.html` lines 243-246 contain hardcoded fake agent names ("Helix-7", "Axiom-1") and fake numbers. While `rotateStories()` in `landing.js` replaces this with real data after API calls complete, the hardcoded text is visible during page load.

### Step 3A: Replace hardcoded text in `index.html`

In `services/ui/data/web/index.html`, replace lines 243-246:
```html
      <div class="story-text" id="story-text">
        Specification quality is climbing — agents are learning that vague specs get ruled against them in court.
        Poster "Helix-7" lost 60% of escrow on a disputed haiku task after filing with an ambiguous brief.
        Meanwhile, worker "Axiom-1" leads the earnings board at 680 © after a perfect 8-task streak.
      </div>
```

With a generic loading placeholder:
```html
      <div class="story-text" id="story-text">
        Loading market data\u2026
      </div>
```

Or with a non-specific placeholder that doesn't reference fake agents:
```html
      <div class="story-text" id="story-text">
        Connecting to the economy — loading latest market activity and agent performance data.
      </div>
```

The second option is better since it reads naturally and doesn't look broken. Use that one.

---

## Validation Gate

After all three fixes are done, run:

```bash
cd services/ui && just ci-quiet
```

ALL checks must pass. If any check fails, fix the issue and re-run.

---

## Summary of changes

| File | Change |
|------|--------|
| `services/ui/data/web/assets/shared.js` | Add `taskCreationTrend: 'stable'` to S defaults; map `data.economy_phase.task_creation_trend` in fetchMetrics() |
| `services/ui/data/web/assets/observatory.js` | Use `S.taskCreationTrend` for trending display with conditional coloring; add dispute rate conditional coloring |
| `services/ui/data/web/index.html` | Replace hardcoded fake agent story text with generic placeholder |
