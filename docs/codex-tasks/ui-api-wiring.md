# UI Frontend API Wiring — Codex Implementation Plan

## Files to Read First

1. `AGENTS.md` — project conventions (MUST read first)
2. This plan file — `docs/codex-tasks/ui-api-wiring.md`
3. `services/ui/data/web/assets/shared.js` — current hardcoded data layer (you will rewrite this)
4. `services/ui/data/web/assets/landing.js` — landing page JS (you will modify this)
5. `services/ui/data/web/assets/observatory.js` — observatory page JS (you will modify this)
6. `services/ui/src/ui_service/schemas.py` — Pydantic models defining the API response shapes

## What You Are Doing

The UI frontend currently runs on **hardcoded simulated data** in `shared.js`. The backend has 12 fully-implemented API endpoints that return real economy data. You are wiring the frontend JavaScript to call these real API endpoints via `fetch()` instead of using fake data.

**You are modifying 3 JavaScript files only:**
- `services/ui/data/web/assets/shared.js` — replace hardcoded data with API client functions
- `services/ui/data/web/assets/landing.js` — use API data instead of `ATE.S` constants
- `services/ui/data/web/assets/observatory.js` — use SSE stream + API data instead of fake events

**You are NOT modifying:**
- Any Python source files or test files
- `task.js` or `task.html` (this is a scripted demo walkthrough, not a data-driven page)
- `index.html` or `observatory.html` (HTML structure stays the same)
- `style.css` (no CSS changes)

## API Endpoints Available

All endpoints are on the same origin (relative URLs). All return JSON.

| Endpoint | Returns |
|---|---|
| `GET /api/metrics` | Aggregated economy metrics (GDP, tasks, escrow, labor, spec quality, phase) |
| `GET /api/agents?sort_by=total_earned&order=desc&limit=20` | Paginated agent list with stats |
| `GET /api/events?limit=50` | Paginated event history (reverse chronological) |
| `GET /api/events/stream?last_event_id=0` | SSE real-time event stream |
| `GET /api/metrics/gdp/history?window=1h&resolution=1m` | GDP time series data points |

## API Response Shapes

### `GET /api/metrics`

```json
{
  "gdp": { "total": 50000, "last_24h": 2500, "last_7d": 18000, "per_agent": 1234.56, "rate_per_hour": 104.17 },
  "agents": { "total_registered": 42, "active": 28, "with_completed_tasks": 15 },
  "tasks": { "total_created": 150, "completed_all_time": 120, "completed_24h": 5, "open": 10, "in_execution": 15, "disputed": 5, "completion_rate": 0.96 },
  "escrow": { "total_locked": 12500 },
  "spec_quality": { "avg_score": 0.85, "extremely_satisfied_pct": 0.85, "satisfied_pct": 0.12, "dissatisfied_pct": 0.03, "trend_direction": "improving", "trend_delta": 0.05 },
  "labor_market": { "avg_bids_per_task": 3.5, "avg_reward": 445.5, "task_posting_rate": 5.0, "acceptance_latency_minutes": 45.3, "unemployment_rate": 0.33, "reward_distribution": { "0_to_10": 20, "11_to_50": 60, "51_to_100": 50, "over_100": 20 } },
  "economy_phase": { "phase": "growing", "task_creation_trend": "increasing", "dispute_rate": 0.033 },
  "computed_at": "2026-03-02T06:35:00Z"
}
```

### `GET /api/agents?sort_by=total_earned&order=desc&limit=50`

```json
{
  "agents": [
    {
      "agent_id": "agent-uuid-1",
      "name": "Alice",
      "registered_at": "2026-02-01T08:00:00Z",
      "stats": {
        "tasks_posted": 5,
        "tasks_completed_as_worker": 12,
        "total_earned": 5600,
        "total_spent": 2500,
        "spec_quality": { "extremely_satisfied": 10, "satisfied": 2, "dissatisfied": 0 },
        "delivery_quality": { "extremely_satisfied": 11, "satisfied": 1, "dissatisfied": 0 }
      }
    }
  ],
  "total_count": 42,
  "limit": 50,
  "offset": 0
}
```

### `GET /api/events?limit=50`

```json
{
  "events": [
    {
      "event_id": 5432,
      "event_source": "task-board",
      "event_type": "task.approved",
      "timestamp": "2026-03-02T06:30:00Z",
      "task_id": "task-uuid-1",
      "agent_id": "agent-uuid-2",
      "summary": "Task completed",
      "payload": {}
    }
  ],
  "has_more": true,
  "oldest_event_id": 4500,
  "newest_event_id": 5432
}
```

### `GET /api/events/stream?last_event_id=0` (SSE)

Server-Sent Events stream. Each event:
```
event: economy_event
data: {"event_id": 5433, "event_source": "task-board", "event_type": "task.created", "timestamp": "...", "task_id": "...", "agent_id": "...", "summary": "New task posted", "payload": {...}}
id: 5433
```

Keepalive comments (`:keepalive`) sent every 15 seconds. Retry directive: `retry: 3000`.

---

## Rules

- Do NOT modify any Python source files or test files.
- Do NOT modify `task.js` or `task.html`.
- Do NOT modify `index.html` or `observatory.html`.
- Do NOT modify `style.css`.
- Do NOT add npm, webpack, or any build tooling. This is plain browser JavaScript.
- All `fetch()` calls use relative URLs (e.g., `/api/metrics`, not `http://localhost:8008/api/metrics`).
- Handle API errors gracefully: if a fetch fails, log the error to console and continue with empty/default data. Never crash the page.
- Keep ALL existing utility functions that are still used (pick, randHex, timeAgo, sparkData, renderSparkSVG, genSparkline, animateCounter).
- The `window.ATE` export object must still exist with the same utility function names so `task.js` (which you are NOT modifying) continues to work.
- Use `'use strict'` in all IIFEs.
- Use `async/await` for all fetch calls (not `.then()` chains).

---

## Phase 1: Rewrite `shared.js` — Replace Hardcoded Data with API Client

### What to Do

Rewrite `services/ui/data/web/assets/shared.js` to:
1. Keep ALL utility functions unchanged
2. Replace the hardcoded `AGENTS` array and `S` state object with empty defaults
3. Add API client functions that populate `ATE.S` and `ATE.AGENTS` from real endpoints
4. Replace `perturbEconomy()` with a periodic metrics refresh function
5. Add an SSE connection function for live events

### Exact Implementation

```javascript
(function() {
  'use strict';

  // ── Default empty state (populated by API calls) ──────────
  const AGENTS = [];

  const S = {
    gdp: { total: 0, last24h: 0, last7d: 0, rate: 0, perAgent: 0 },
    agents: { total: 0, active: 0, withCompleted: 0 },
    tasks: { completed24h: 0, completedAll: 0, open: 0, inExec: 0, disputed: 0, completionRate: 0, postingRate: 0 },
    escrow: { locked: 0 },
    specQ: { avg: 0, esPct: 0, sPct: 0, dPct: 0, trend: 'stable', delta: 0 },
    labor: { avgBids: 0, avgReward: 0, unemployment: 0, acceptLatency: 0 },
    phase: 'bootstrapping',
    rewardDist: { '0-10': 0, '11-50': 0, '51-100': 0, '100+': 0 }
  };

  // ── Utility functions (KEEP ALL OF THESE UNCHANGED) ───────
  // Copy ALL of these from the current shared.js exactly as-is:
  // pick, randHex, timeAgo, sparkData, renderSparkSVG, genSparkline,
  // animateCounter

  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  function randHex() {
    return Math.random().toString(16).slice(2, 10);
  }

  function timeAgo(ms) {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    return Math.floor(seconds / 3600) + 'h ago';
  }

  function sparkData(n, base, variance) {
    const out = [];
    let value = base;
    for (let i = 0; i < n; i += 1) {
      value += (Math.random() - 0.42) * variance;
      value = Math.max(base * 0.3, value);
      out.push(value);
    }
    return out;
  }

  function renderSparkSVG(data, w, h, fill) {
    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;
    const points = data.map(function(v, i) {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return x.toFixed(1) + ',' + y.toFixed(1);
    });
    const polyline = points.join(' ');
    const fillPoly = fill ? '<polygon points="0,' + h + ' ' + polyline + ' ' + w + ',' + h + '" fill="var(--green-fill)" />' : '';
    return '<svg class="sparkline" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">' + fillPoly + '<polyline points="' + polyline + '" fill="none" stroke="var(--green)" stroke-width="1.2" /></svg>';
  }

  function genSparkline(n, base, variance) {
    return sparkData(n, base, variance);
  }

  function animateCounter(el, from, to, duration, suffix) {
    var start = performance.now();
    el.classList.add('counting');

    function tick(now) {
      var progress = Math.min((now - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      var current = Math.round(from + (to - from) * eased);
      el.textContent = current.toLocaleString() + suffix;
      if (progress < 1) {
        requestAnimationFrame(tick);
      } else {
        el.classList.remove('counting');
      }
    }

    requestAnimationFrame(tick);
  }

  // ── API Client Functions (NEW) ────────────────────────────

  /**
   * Fetch metrics from /api/metrics and populate ATE.S.
   * Returns the raw API response or null on error.
   */
  async function fetchMetrics() {
    try {
      var response = await fetch('/api/metrics');
      if (!response.ok) {
        console.warn('[ATE] fetchMetrics failed:', response.status);
        return null;
      }
      var data = await response.json();

      // Map API response to ATE.S shape
      S.gdp.total = data.gdp.total;
      S.gdp.last24h = data.gdp.last_24h;
      S.gdp.last7d = data.gdp.last_7d;
      S.gdp.rate = data.gdp.rate_per_hour;
      S.gdp.perAgent = data.gdp.per_agent;

      S.agents.total = data.agents.total_registered;
      S.agents.active = data.agents.active;
      S.agents.withCompleted = data.agents.with_completed_tasks;

      S.tasks.completed24h = data.tasks.completed_24h;
      S.tasks.completedAll = data.tasks.completed_all_time;
      S.tasks.open = data.tasks.open;
      S.tasks.inExec = data.tasks.in_execution;
      S.tasks.disputed = data.tasks.disputed;
      S.tasks.completionRate = data.tasks.completion_rate;
      S.tasks.postingRate = data.labor_market.task_posting_rate;

      S.escrow.locked = data.escrow.total_locked;

      S.specQ.avg = data.spec_quality.avg_score * 100;
      S.specQ.esPct = data.spec_quality.extremely_satisfied_pct;
      S.specQ.sPct = data.spec_quality.satisfied_pct;
      S.specQ.dPct = data.spec_quality.dissatisfied_pct;
      S.specQ.trend = data.spec_quality.trend_direction === 'improving' ? 'up' : data.spec_quality.trend_direction === 'declining' ? 'down' : 'stable';
      S.specQ.delta = data.spec_quality.trend_delta * 100;

      S.labor.avgBids = data.labor_market.avg_bids_per_task;
      S.labor.avgReward = data.labor_market.avg_reward;
      S.labor.unemployment = data.labor_market.unemployment_rate;
      S.labor.acceptLatency = data.labor_market.acceptance_latency_minutes;

      S.phase = data.economy_phase.phase;

      var rd = data.labor_market.reward_distribution;
      var rdTotal = (rd['0_to_10'] || 0) + (rd['11_to_50'] || 0) + (rd['51_to_100'] || 0) + (rd['over_100'] || 0);
      if (rdTotal > 0) {
        S.rewardDist['0-10'] = Math.round((rd['0_to_10'] || 0) / rdTotal * 100);
        S.rewardDist['11-50'] = Math.round((rd['11_to_50'] || 0) / rdTotal * 100);
        S.rewardDist['51-100'] = Math.round((rd['51_to_100'] || 0) / rdTotal * 100);
        S.rewardDist['100+'] = Math.round((rd['over_100'] || 0) / rdTotal * 100);
      }

      return data;
    } catch (err) {
      console.warn('[ATE] fetchMetrics error:', err.message);
      return null;
    }
  }

  /**
   * Generate a deterministic color from an agent ID string.
   */
  function agentColor(agentId) {
    var hash = 0;
    for (var i = 0; i < agentId.length; i++) {
      hash = agentId.charCodeAt(i) + ((hash << 5) - hash);
    }
    var hue = Math.abs(hash) % 360;
    return 'hsl(' + hue + ', 80%, 65%)';
  }

  /**
   * Fetch agents from /api/agents and populate ATE.AGENTS.
   * Returns the raw API response or null on error.
   */
  async function fetchAgents() {
    try {
      var response = await fetch('/api/agents?sort_by=total_earned&order=desc&limit=50');
      if (!response.ok) {
        console.warn('[ATE] fetchAgents failed:', response.status);
        return null;
      }
      var data = await response.json();

      AGENTS.length = 0;
      data.agents.forEach(function(a) {
        var isWorker = a.stats.total_earned >= a.stats.total_spent;
        AGENTS.push({
          id: a.agent_id,
          name: a.name,
          role: isWorker ? 'worker' : 'poster',
          color: agentColor(a.agent_id),
          earned: a.stats.total_earned,
          spent: a.stats.total_spent,
          tc: a.stats.tasks_completed_as_worker,
          tp: a.stats.tasks_posted,
          dq: {
            es: a.stats.delivery_quality.extremely_satisfied,
            s: a.stats.delivery_quality.satisfied,
            d: a.stats.delivery_quality.dissatisfied
          },
          sq: {
            es: a.stats.spec_quality.extremely_satisfied,
            s: a.stats.spec_quality.satisfied,
            d: a.stats.spec_quality.dissatisfied
          },
          streak: 0
        });
      });

      return data;
    } catch (err) {
      console.warn('[ATE] fetchAgents error:', err.message);
      return null;
    }
  }

  /**
   * Fetch recent events from /api/events.
   * Returns array of event objects or empty array on error.
   */
  async function fetchEvents(limit, before) {
    try {
      var url = '/api/events?limit=' + (limit || 50);
      if (before) {
        url += '&before=' + before;
      }
      var response = await fetch(url);
      if (!response.ok) {
        console.warn('[ATE] fetchEvents failed:', response.status);
        return { events: [], has_more: false };
      }
      return await response.json();
    } catch (err) {
      console.warn('[ATE] fetchEvents error:', err.message);
      return { events: [], has_more: false };
    }
  }

  /**
   * Connect to SSE event stream. Calls onEvent(eventData) for each event.
   * Returns the EventSource object (call .close() to disconnect).
   */
  function connectSSE(onEvent, lastEventId) {
    var url = '/api/events/stream?last_event_id=' + (lastEventId || 0);
    var source = new EventSource(url);

    source.addEventListener('economy_event', function(e) {
      try {
        var data = JSON.parse(e.data);
        onEvent(data);
      } catch (err) {
        console.warn('[ATE] SSE parse error:', err.message);
      }
    });

    source.onerror = function() {
      console.warn('[ATE] SSE connection error, will auto-reconnect');
    };

    return source;
  }

  /**
   * Map an API event object to a feed display object.
   * Returns { type, badge, text, time }.
   */
  function mapEventToFeed(event) {
    var typeMap = {
      'task.created': 'TASK',
      'bid.submitted': 'BID',
      'task.accepted': 'CONTRACT',
      'asset.uploaded': 'SUBMIT',
      'task.submitted': 'SUBMIT',
      'task.approved': 'PAYOUT',
      'task.auto_approved': 'PAYOUT',
      'task.disputed': 'DISPUTE',
      'task.ruled': 'RULING',
      'task.cancelled': 'CANCEL',
      'task.expired': 'CANCEL',
      'escrow.locked': 'ESCROW',
      'escrow.released': 'PAYOUT',
      'escrow.split': 'PAYOUT',
      'feedback.revealed': 'REP',
      'salary.paid': 'SALARY',
      'agent.registered': 'AGENT'
    };

    var badgeMap = {
      'TASK': 'badge-task',
      'BID': 'badge-bid',
      'CONTRACT': 'badge-contract',
      'SUBMIT': 'badge-submit',
      'PAYOUT': 'badge-payout',
      'DISPUTE': 'badge-dispute',
      'RULING': 'badge-ruling',
      'CANCEL': 'badge-cancel',
      'ESCROW': 'badge-escrow',
      'REP': 'badge-rep',
      'SALARY': 'badge-salary',
      'AGENT': 'badge-agent'
    };

    var feedType = typeMap[event.event_type] || 'TASK';
    return {
      type: feedType,
      badge: badgeMap[feedType] || 'badge-task',
      text: event.summary || event.event_type,
      time: new Date(event.timestamp).getTime(),
      eventId: event.event_id
    };
  }

  /**
   * Periodically re-fetch metrics and call onUpdate callback.
   * Returns interval ID (call clearInterval() to stop).
   */
  function startMetricsPolling(onUpdate, intervalMs) {
    return setInterval(async function() {
      var result = await fetchMetrics();
      if (result !== null && typeof onUpdate === 'function') {
        onUpdate(S);
      }
    }, intervalMs);
  }

  // ── Backward-compat: perturbEconomy still works for task.js ──
  // task.js uses ATE.perturbEconomy and ATE.buildBottomTicker.
  // Keep a version that mutates S randomly (for task.js demo page only).
  function perturbEconomy(onUpdate, intervalMs) {
    return setInterval(function() {
      S.gdp.total += Math.floor(Math.random() * 10) - 3;
      S.gdp.rate += (Math.random() - 0.45) * 1.5;
      S.gdp.rate = Math.max(60, S.gdp.rate);
      S.gdp.perAgent = S.gdp.total / Math.max(S.agents.total, 1);
      S.escrow.locked += Math.floor(Math.random() * 15) - 5;
      S.escrow.locked = Math.max(0, S.escrow.locked);
      if (Math.random() > 0.88) {
        S.tasks.open += Math.random() > 0.5 ? 1 : -1;
        S.tasks.open = Math.max(0, S.tasks.open);
      }
      if (Math.random() > 0.92) {
        S.tasks.completed24h += 1;
        S.tasks.completedAll += 1;
      }
      S.specQ.avg = Math.max(0, Math.min(100, S.specQ.avg + (Math.random() - 0.48) * 0.4));
      S.labor.avgBids = Math.max(0.5, S.labor.avgBids + (Math.random() - 0.48) * 0.08);
      S.labor.avgReward = Math.max(1, S.labor.avgReward + (Math.random() - 0.5) * 1.4);
      S.labor.unemployment = Math.max(0, Math.min(1, S.labor.unemployment + (Math.random() - 0.5) * 0.01));
      if (typeof onUpdate === 'function') {
        onUpdate(S);
      }
    }, intervalMs);
  }

  // ── Ticker builders (KEEP from current shared.js) ─────────
  // Copy buildTopTicker and buildBottomTicker exactly from current shared.js

  function buildTopTicker(trackEl) {
    var pairs = [
      { sym: 'GDP/TOTAL', val: S.gdp.total.toLocaleString(), chg: 2.4 },
      { sym: 'TASK/OPEN', val: S.tasks.open, chg: S.tasks.open > 10 ? -1 : 1 },
      { sym: 'ESCROW/LOCK', val: S.escrow.locked.toLocaleString() + ' \u00a9', chg: 5.1 },
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: S.specQ.delta },
      { sym: 'BID/AVG', val: S.labor.avgBids.toFixed(1), chg: 0.3 },
      { sym: 'AGENTS/ACT', val: S.agents.active, chg: 0 },
      { sym: 'COMP/RATE', val: (S.tasks.completionRate * 100).toFixed(0) + '%', chg: 1.2 },
      { sym: 'GDP/RATE', val: S.gdp.rate.toFixed(1) + '/hr', chg: 3.8 },
      { sym: 'RWD/AVG', val: Math.round(S.labor.avgReward) + ' \u00a9', chg: -0.5 },
      { sym: 'UNEMP', val: (S.labor.unemployment * 100).toFixed(1) + '%', chg: -1.1 },
      { sym: 'DISPUTES', val: S.tasks.disputed, chg: 1 },
      { sym: 'GDP/AGENT', val: Math.round(S.gdp.perAgent).toLocaleString(), chg: 1.5 }
    ];

    var items = pairs.concat(pairs);
    trackEl.innerHTML = items.map(function(item) {
      var cls = item.chg > 0 ? 'up' : item.chg < 0 ? 'down' : 'muted';
      var arrow = item.chg > 0 ? '\u25b2' : item.chg < 0 ? '\u25bc' : '\u2013';
      return '<span class="ticker-item"><span class="sym">' + item.sym + '</span><span>' + item.val + '</span><span class="chg ' + cls + '">' + arrow + ' ' + Math.abs(item.chg).toFixed(1) + '%</span></span>';
    }).join('');
  }

  function buildBottomTicker(trackEl) {
    var totalPaidOut = S.gdp.total - S.escrow.locked;
    var topEarner = AGENTS.filter(function(a) { return a.role === 'worker'; }).sort(function(a, b) { return b.earned - a.earned; })[0];
    var topPoster = AGENTS.filter(function(a) { return a.role === 'poster'; }).sort(function(a, b) { return b.spent - a.spent; })[0];

    var items = [
      { sym: 'TASKS/ALL', val: S.tasks.completedAll.toLocaleString(), chg: '+' + S.tasks.completed24h + ' today', up: true },
      { sym: 'GDP/TOTAL', val: S.gdp.total.toLocaleString() + ' \u00a9', chg: '+' + S.gdp.last24h.toLocaleString() + ' 24h', up: true },
      { sym: 'ESCROW/LOCK', val: S.escrow.locked.toLocaleString() + ' \u00a9', chg: 'in escrow', up: null },
      { sym: 'PAID/OUT', val: totalPaidOut.toLocaleString() + ' \u00a9', chg: 'released', up: true },
      { sym: 'GDP/RATE', val: S.gdp.rate.toFixed(1) + ' \u00a9/hr', chg: '+3.8%', up: true },
      { sym: 'POST/RATE', val: S.tasks.postingRate.toFixed(1) + '/hr', chg: 'new tasks', up: null },
      { sym: 'BID/AVG', val: S.labor.avgBids.toFixed(1) + '/task', chg: '+0.3', up: true },
      { sym: 'COMP/RATE', val: (S.tasks.completionRate * 100).toFixed(0) + '%', chg: '+1.2%', up: true },
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: '\u2191' + S.specQ.delta.toFixed(1) + '%', up: true },
      { sym: 'UNEMP', val: (S.labor.unemployment * 100).toFixed(1) + '%', chg: '-1.1%', up: true },
      { sym: 'LATENCY', val: S.labor.acceptLatency.toFixed(0) + ' min', chg: 'avg accept', up: null },
      { sym: 'AVG/RWD', val: Math.round(S.labor.avgReward) + ' \u00a9', chg: 'per task', up: null }
    ];

    if (topEarner) {
      items.push({ sym: 'TOP/EARNER', val: topEarner.name, chg: topEarner.earned + ' \u00a9 earned', up: true });
    }
    if (topPoster) {
      items.push({ sym: 'TOP/POSTER', val: topPoster.name, chg: topPoster.spent + ' \u00a9 spent', up: null });
    }

    items.push({ sym: 'AGENTS/REG', val: String(S.agents.total), chg: S.agents.active + ' active', up: null });

    var doubled = items.concat(items);
    trackEl.innerHTML = doubled.map(function(item) {
      if (item.alert) {
        var color = item.alert === 'alert' ? 'var(--amber)' : 'var(--cyan)';
        return '<span class="bt-item"><span class="bt-alert" style="border-color:' + color + ';color:' + color + '">' + (item.alert === 'alert' ? '\u26a1 ALERT' : '\u2139 INFO') + '</span><span>' + item.text + '</span><span class="bt-sep">\u00b7</span></span>';
      }
      var color2 = item.up === true ? 'var(--green)' : item.up === false ? 'var(--red)' : 'var(--text-dim)';
      return '<span class="bt-item"><span class="bt-sym">' + item.sym + '</span><span class="bt-val">' + item.val + '</span><span class="bt-chg" style="color:' + color2 + '">' + item.chg + '</span><span class="bt-sep">\u00b7</span></span>';
    }).join('');
  }

  // ── Export ────────────────────────────────────────────────
  window.ATE = {
    AGENTS: AGENTS,
    S: S,
    // Utilities
    pick: pick,
    randHex: randHex,
    timeAgo: timeAgo,
    sparkData: sparkData,
    renderSparkSVG: renderSparkSVG,
    genSparkline: genSparkline,
    animateCounter: animateCounter,
    agentColor: agentColor,
    // API client
    fetchMetrics: fetchMetrics,
    fetchAgents: fetchAgents,
    fetchEvents: fetchEvents,
    connectSSE: connectSSE,
    mapEventToFeed: mapEventToFeed,
    startMetricsPolling: startMetricsPolling,
    // Backward-compat for task.js demo
    perturbEconomy: perturbEconomy,
    startEconomyPerturbation: perturbEconomy,
    // Ticker builders
    buildTopTicker: buildTopTicker,
    buildBottomTicker: buildBottomTicker
  };
})();
```

### How to Verify

```bash
# File should exist and be ~300-400 lines
wc -l services/ui/data/web/assets/shared.js
# Should have no syntax errors
node --check services/ui/data/web/assets/shared.js 2>&1 || echo "Node not required, just verify manually"
```

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/assets/shared.js
git commit -m "feat(ui): replace hardcoded data with API client layer in shared.js"
```

---

## Phase 2: Wire `landing.js` — Load Real Data on Page Load

### What to Do

Rewrite `services/ui/data/web/assets/landing.js` to:
1. On DOMContentLoaded, call `ATE.fetchMetrics()` and `ATE.fetchAgents()` first
2. Then build all UI sections from the now-populated `ATE.S` and `ATE.AGENTS`
3. Replace `ATE.perturbEconomy()` with `ATE.startMetricsPolling()` for periodic refresh
4. Re-fetch agents every 30 seconds for leaderboard updates

### Exact Implementation

```javascript
(function() {
  'use strict';

  var ATE = window.ATE;
  var S = ATE.S;

  function buildTopTicker() {
    var track = document.getElementById('ticker-track');
    if (!track) return;
    ATE.buildTopTicker(track);
  }

  function buildKPIStrip() {
    var kpis = [
      { label: 'Economy GDP', value: S.gdp.total, suffix: ' \u00a9', note: '+' + S.gdp.rate.toFixed(0) + '/hr', noteUp: true },
      { label: 'Active Agents', value: S.agents.active, suffix: '', note: 'of ' + S.agents.total + ' registered', noteUp: null },
      { label: 'Tasks Completed', value: S.tasks.completedAll, suffix: '+', note: 'all-time', noteUp: null },
      { label: 'Spec Quality', value: Math.round(S.specQ.avg), suffix: '%', note: '\u2191 ' + S.specQ.delta.toFixed(1) + '% this week', noteUp: true },
      { label: 'Economy Phase', value: null, text: S.phase.toUpperCase(), suffix: '', note: 'tasks \u2191 disputes \u2193', noteUp: true }
    ];

    var strip = document.getElementById('kpi-strip');
    if (!strip) return;

    strip.innerHTML = kpis.map(function(kpi, index) {
      var noteClass = kpi.noteUp === true ? 'up' : kpi.noteUp === false ? 'down' : 'muted';
      var display = kpi.text || '0';
      return '<div class="kpi-cell" style="animation-delay:' + (index * 0.08) + 's" data-target="' + (kpi.value || 0) + '" data-suffix="' + kpi.suffix + '" data-text="' + (kpi.text || '') + '"><div class="kpi-label">' + kpi.label + '</div><div class="kpi-value" id="kpi-' + index + '">' + display + '</div><div class="kpi-note ' + noteClass + '">' + kpi.note + '</div></div>';
    }).join('');

    document.querySelectorAll('.kpi-cell').forEach(function(cell, index) {
      var target = parseInt(cell.dataset.target, 10);
      var suffix = cell.dataset.suffix;
      var text = cell.dataset.text;
      if (text) return;
      var valueEl = cell.querySelector('.kpi-value');
      ATE.animateCounter(valueEl, 0, target, 1800 + index * 200, suffix);
    });
  }

  function buildExchangeBoard() {
    var cells = [
      { label: 'GDP Total', value: S.gdp.total.toLocaleString() + ' \u00a9', delta: '+2.4%', up: true, spark: ATE.genSparkline(16, 40, 8) },
      { label: 'GDP Last 24h', value: S.gdp.last24h.toLocaleString() + ' \u00a9', delta: '+5.1%', up: true, spark: ATE.genSparkline(16, 30, 10) },
      { label: 'GDP / Agent', value: Math.round(S.gdp.perAgent).toLocaleString(), delta: '+1.5%', up: true, spark: ATE.genSparkline(16, 42, 6) },
      { label: 'GDP Rate', value: S.gdp.rate.toFixed(1) + ' \u00a9/hr', delta: '+3.8%', up: true, spark: ATE.genSparkline(16, 13, 4) },
      { label: 'Open Tasks', value: String(S.tasks.open), delta: '-1', up: false, spark: ATE.genSparkline(16, 14, 5) },
      { label: 'In Execution', value: String(S.tasks.inExec), delta: '+2', up: true, spark: ATE.genSparkline(16, 6, 3) },
      { label: 'Completion Rate', value: (S.tasks.completionRate * 100).toFixed(0) + '%', delta: '+1.2%', up: true, spark: ATE.genSparkline(16, 85, 8) },
      { label: 'Disputes Active', value: String(S.tasks.disputed), delta: '+1', up: false, spark: ATE.genSparkline(16, 2, 2) },
      { label: 'Escrow Locked', value: S.escrow.locked.toLocaleString() + ' \u00a9', delta: '+5.1%', up: true, spark: ATE.genSparkline(16, 24, 7) },
      { label: 'Avg Bids/Task', value: S.labor.avgBids.toFixed(1), delta: '+0.3', up: true, spark: ATE.genSparkline(16, 3, 1.5) },
      { label: 'Avg Reward', value: Math.round(S.labor.avgReward) + ' \u00a9', delta: '-0.5%', up: false, spark: ATE.genSparkline(16, 52, 12) },
      { label: 'Unemployment', value: (S.labor.unemployment * 100).toFixed(1) + '%', delta: '-1.1%', up: true, spark: ATE.genSparkline(16, 12, 5) },
      { label: 'Spec Quality', value: Math.round(S.specQ.avg) + '%', delta: '+' + S.specQ.delta.toFixed(1) + '%', up: true, spark: ATE.genSparkline(16, 68, 8) },
      { label: 'Registered', value: String(S.agents.total), delta: '+0', up: null, spark: ATE.genSparkline(16, 10, 2) },
      { label: 'Rewards 51-100\u00a9', value: S.rewardDist['51-100'] + '%', delta: '', up: null, spark: ATE.genSparkline(16, 42, 6) }
    ];

    var grid = document.getElementById('board-grid');
    if (!grid) return;

    grid.innerHTML = cells.map(function(cell, index) {
      var deltaClass = cell.up === true ? 'up' : cell.up === false ? 'down' : 'muted';
      var max = Math.max.apply(null, cell.spark);
      var sparkColor = cell.up === true ? 'var(--green)' : cell.up === false ? 'var(--red)' : 'var(--text-dim)';
      var bars = cell.spark.map(function(value, barIndex) {
        return '<div class="bar" style="height:' + (value / max * 100).toFixed(0) + '%;background:' + sparkColor + ';opacity:' + (0.4 + 0.6 * barIndex / cell.spark.length) + ';animation-delay:' + (barIndex * 0.03) + 's"></div>';
      }).join('');
      var arrow = cell.up === true ? '\u25b2' : cell.up === false ? '\u25bc' : '\u2013';
      var valueColor = cell.up === true ? 'var(--green)' : cell.up === false ? 'var(--red)' : 'var(--text)';
      return '<div class="board-cell" style="animation: fade-in-up .5s ease-out ' + (index * 0.04) + 's both"><div class="cell-label">' + cell.label + '</div><div class="cell-value" style="color:' + valueColor + '">' + cell.value + '</div><div class="cell-delta ' + deltaClass + '">' + arrow + ' ' + cell.delta + '</div><div class="cell-spark">' + bars + '</div></div>';
    }).join('');

    var clockEl = document.getElementById('board-clock');
    function updateClock() {
      if (!clockEl) return;
      var now = new Date();
      clockEl.textContent = now.toLocaleTimeString('en-US', { hour12: false }) + ' UTC';
    }
    updateClock();
    setInterval(updateClock, 1000);
  }

  function buildLeaderboard() {
    var AGENTS = ATE.AGENTS;
    var workers = AGENTS.filter(function(a) { return a.role === 'worker'; }).sort(function(a, b) { return b.earned - a.earned; });
    var posters = AGENTS.filter(function(a) { return a.role === 'poster'; }).sort(function(a, b) { return b.spent - a.spent; });
    var container = document.getElementById('lb-container');
    if (!container) return;

    function renderPanel(title, entries, isWorker) {
      var rows = entries.map(function(agent, index) {
        var rankClass = index === 0 ? 'lb-rank top' : 'lb-rank';
        var initials = agent.name.replace(/[^A-Z0-9]/gi, '').slice(0, 2).toUpperCase();
        var quality = isWorker ? agent.dq : agent.sq;
        var stat = isWorker ? agent.tc + ' tasks completed' : agent.tp + ' tasks posted';
        var amount = isWorker ? agent.earned : agent.spent;
        var amountLabel = isWorker ? 'EARNED' : 'SPENT';
        var amountColor = isWorker ? 'var(--green)' : 'var(--orange)';
        var streak = isWorker && agent.streak >= 3 ? '<span style="font-size:8px;color:var(--yellow);margin-left:4px">\ud83d\udd25' + agent.streak + '</span>' : '';
        return '<div class="lb-row" style="animation: slide-right .4s ease-out ' + (index * 0.08) + 's both"><div class="' + rankClass + '">' + (index + 1) + '</div><div class="lb-avatar" style="background:' + agent.color + '22;color:' + agent.color + ';border:1px solid ' + agent.color + '44">' + initials + '</div><div class="lb-info"><div class="lb-name">' + agent.name + streak + '</div><div class="lb-stat">' + stat + '</div><div class="lb-quality"><span class="star-group"><span class="stars">\u2605\u2605\u2605</span>' + quality.es + '</span><span class="star-group"><span class="stars">\u2605\u2605</span>' + quality.s + '</span><span class="star-group"><span class="stars">\u2605</span>' + quality.d + '</span></div></div><div class="lb-earnings"><div class="amount" style="color:' + amountColor + '">' + amount.toLocaleString() + ' \u00a9</div><div class="label-sm">' + amountLabel + '</div></div></div>';
      }).join('');
      return '<div class="lb-panel"><div class="lb-panel-header"><span class="lb-panel-title" style="color:' + (isWorker ? 'var(--green)' : 'var(--orange)') + '">' + title + '</span><span class="label">' + entries.length + ' agents</span></div>' + rows + '</div>';
    }

    container.innerHTML = renderPanel('\ud83c\udfd7 Top Workers', workers, true) + renderPanel('\ud83d\udccb Top Posters', posters, false);
  }

  function buildNewsTrack() {
    var news = [
      { badge: 'alert', text: 'Economy running — ' + ATE.S.agents.active + ' agents active' },
      { badge: 'info', text: 'Specification quality at ' + Math.round(ATE.S.specQ.avg) + '% — market rewards precision' },
      { badge: 'alert', text: ATE.S.tasks.open + ' open tasks awaiting bids' },
      { badge: 'info', text: 'GDP rate: ' + ATE.S.gdp.rate.toFixed(1) + ' \u00a9/hr — economy ' + ATE.S.phase },
      { badge: 'alert', text: ATE.S.tasks.disputed + ' active disputes in court' },
      { badge: 'info', text: 'Escrow volume: ' + ATE.S.escrow.locked.toLocaleString() + ' \u00a9 locked' },
      { badge: 'alert', text: 'Avg ' + ATE.S.labor.avgBids.toFixed(1) + ' bids per task — competition is ' + (ATE.S.labor.avgBids > 3 ? 'high' : 'moderate') },
      { badge: 'info', text: 'Completion rate: ' + (ATE.S.tasks.completionRate * 100).toFixed(0) + '% — market health strong' }
    ];

    var track = document.getElementById('news-track');
    if (!track) return;

    var doubled = news.concat(news);
    track.innerHTML = doubled.map(function(item) {
      return '<span class="bt-item"><span class="bt-badge ' + item.badge + '">' + (item.badge === 'alert' ? '\u26a1 ALERT' : '\u2139 INFO') + '</span><span>' + item.text + '</span><span style="color:var(--border-hi)">\u00b7</span></span>';
    }).join('');
  }

  function startLiveUpdates() {
    // Poll metrics every 10 seconds and refresh UI
    ATE.startMetricsPolling(function() {
      var kpiVals = [S.gdp.total, S.agents.active, S.tasks.completedAll, Math.round(S.specQ.avg)];
      var suffixes = [' \u00a9', '', '+', '%'];
      kpiVals.forEach(function(value, index) {
        var el = document.getElementById('kpi-' + index);
        if (el) {
          el.textContent = value.toLocaleString() + suffixes[index];
        }
      });

      var cells = document.querySelectorAll('.board-cell');
      if (cells.length > 0) {
        var cell = cells[Math.floor(Math.random() * cells.length)];
        cell.style.background = '#1a2338';
        setTimeout(function() { cell.style.background = ''; }, 400);
      }

      buildTopTicker();
      buildNewsTrack();
    }, 10000);

    // Re-fetch agents every 30 seconds for leaderboard
    setInterval(async function() {
      await ATE.fetchAgents();
      buildLeaderboard();
    }, 30000);
  }

  function rotateStories() {
    var stories = [
      'The economy is live — ' + ATE.S.agents.active + ' agents competing for ' + ATE.S.tasks.open + ' open tasks. Specification quality determines who wins disputes.',
      'Competitive bidding active — average ' + ATE.S.labor.avgBids.toFixed(1) + ' bids per task. Workers compete on quality and price.',
      'Court activity: ' + ATE.S.tasks.disputed + ' disputes pending. Vague specs lose — the market punishes ambiguity.',
      ATE.S.agents.total + ' agents registered. GDP at ' + ATE.S.gdp.total.toLocaleString() + ' \u00a9 and growing at ' + ATE.S.gdp.rate.toFixed(1) + ' \u00a9/hr.'
    ];

    var textEl = document.getElementById('story-text');
    if (!textEl) return;

    // Set initial story from real data
    textEl.textContent = stories[0];

    var index = 0;
    textEl.style.transition = 'opacity .3s';
    setInterval(function() {
      index = (index + 1) % stories.length;
      textEl.style.opacity = '0';
      setTimeout(function() {
        textEl.textContent = stories[index];
        textEl.style.opacity = '1';
      }, 300);
    }, 12000);
  }

  // ── Boot sequence ─────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async function() {
    // Fetch real data first, THEN build UI
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents()]);

    buildTopTicker();
    buildKPIStrip();
    buildExchangeBoard();
    buildLeaderboard();
    buildNewsTrack();
    startLiveUpdates();
    rotateStories();
  });
})();
```

### How to Verify

```bash
wc -l services/ui/data/web/assets/landing.js
```

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/assets/landing.js
git commit -m "feat(ui): wire landing page to real API endpoints"
```

---

## Phase 3: Wire `observatory.js` — SSE Stream + Real Data

### What to Do

Rewrite `services/ui/data/web/assets/observatory.js` to:
1. On DOMContentLoaded, fetch metrics and agents from API
2. Replace fake event generation with SSE connection to `/api/events/stream`
3. Also fetch initial event history from `/api/events`
4. Poll metrics periodically (every 10 seconds) to update GDP panel and vitals
5. Re-fetch agents every 30 seconds for leaderboard

### Exact Implementation

```javascript
(function() {
  'use strict';

  var ATE = window.ATE;
  var S = ATE.S;

  var EVENT_TYPES = ['ALL', 'TASK', 'BID', 'PAYOUT', 'CONTRACT', 'ESCROW', 'SUBMIT', 'REP', 'DISPUTE', 'RULING', 'CANCEL', 'AGENT'];

  var activeFilter = 'ALL';
  var paused = false;
  var currentTab = 'workers';
  var feedEvents = [];
  var lastEventId = 0;
  var sseSource = null;

  function buildVitals() {
    var items = [
      { l: 'Active Agents', v: S.agents.active, c: 'var(--text)' },
      { l: 'Open Tasks', v: S.tasks.open, c: 'var(--text)' },
      { l: 'Completed (24h)', v: S.tasks.completed24h, c: 'var(--text)' },
      { l: 'GDP (Total)', v: S.gdp.total.toLocaleString(), c: 'var(--text)', delta: '\u2191' + S.gdp.rate.toFixed(1) + '/hr', dc: 'var(--green)' },
      { l: 'GDP / Agent', v: Math.round(S.gdp.perAgent).toLocaleString(), c: 'var(--text)' },
      { l: 'Unemployment', v: (S.labor.unemployment * 100).toFixed(1) + '%', c: S.labor.unemployment > 0.15 ? 'var(--red)' : S.labor.unemployment > 0.08 ? 'var(--amber)' : 'var(--green)' },
      { l: 'Escrow Locked', v: S.escrow.locked.toLocaleString() + ' \u00a9', c: 'var(--amber)' }
    ];

    var el = document.getElementById('vitals-bar');
    if (!el) return;
    el.innerHTML = items.map(function(item) {
      return '<div class="vital-item"><div><div class="vital-label">' + item.l + '</div><div style="display:flex;align-items:baseline;gap:3px"><span class="vital-value" style="color:' + item.c + '">' + item.v + '</span>' + (item.delta ? '<span class="vital-delta" style="color:' + item.dc + '">' + item.delta + '</span>' : '') + '</div></div></div>';
    }).join('') + '<div class="live-indicator"><div class="live-dot"></div><span class="live-label">LIVE</span></div>';
  }

  function buildGDPPanel() {
    var gdpSpark = ATE.sparkData(24, S.gdp.total || 100, (S.gdp.total || 100) * 0.02);
    var perAgentSpark = ATE.sparkData(24, S.gdp.perAgent || 100, (S.gdp.perAgent || 100) * 0.05);
    var trendColor = S.gdp.rate > 0 ? 'var(--green)' : 'var(--red)';
    var phaseColor = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-mid)';
    var phaseBorder = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-dim)';
    var distTotal = Object.values(S.rewardDist).reduce(function(acc, val) { return acc + val; }, 0) || 1;

    var panel = document.getElementById('gdp-panel');
    if (!panel) return;

    panel.innerHTML =
      '<div class="gdp-section"><div class="gdp-section-label">Economy Output</div><div class="gdp-spark-row"><div><div class="gdp-big" style="color:' + trendColor + '">' + S.gdp.total.toLocaleString() + '</div><div class="gdp-unit">\u00a9 total GDP</div></div><div style="display:flex;align-items:center;gap:4px"><span style="font-size:10px;font-weight:600;color:' + trendColor + '">\u2191 ' + S.gdp.rate.toFixed(1) + '/hr</span></div></div><div style="margin-top:10px">' + ATE.renderSparkSVG(gdpSpark, 300, 56, true) + '</div><div style="margin-top:8px"><div class="gdp-detail-row"><span class="gdp-detail-label">Rate</span><span class="gdp-detail-value" style="color:' + trendColor + '">\u2191 ' + S.gdp.rate.toFixed(1) + ' \u00a9/hr</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Last 24h</span><span class="gdp-detail-value">' + S.gdp.last24h.toLocaleString() + ' \u00a9</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Last 7d</span><span class="gdp-detail-value">' + S.gdp.last7d.toLocaleString() + ' \u00a9</span></div></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">GDP / Agent</div><div class="gdp-spark-row"><div><div style="font-size:20px;font-weight:700;color:' + trendColor + '">' + Math.round(S.gdp.perAgent).toLocaleString() + '</div></div></div><div style="margin-top:8px">' + ATE.renderSparkSVG(perAgentSpark, 300, 40, false) + '</div><div style="margin-top:6px"><div class="gdp-detail-row"><span class="gdp-detail-label">Active</span><span class="gdp-detail-value">' + S.agents.active + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Registered</span><span class="gdp-detail-value">' + S.agents.total + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">With completed</span><span class="gdp-detail-value">' + S.agents.withCompleted + '</span></div></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">Economy Phase</div><div style="margin-bottom:6px"><span class="gdp-phase-badge" style="color:' + phaseColor + ';border-color:' + phaseBorder + '">' + S.phase.toUpperCase() + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Task creation</span><span class="gdp-detail-value" style="color:var(--green)">\u2191 trending</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Dispute rate</span><span class="gdp-detail-value" style="color:var(--green)">' + ((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1) + '%</span></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">Labor Market</div><div class="gdp-detail-row"><span class="gdp-detail-label">Avg bids / task</span><span class="gdp-detail-value">' + S.labor.avgBids.toFixed(1) + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Accept latency</span><span class="gdp-detail-value">' + S.labor.acceptLatency.toFixed(0) + ' min</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Completion rate</span><span class="gdp-detail-value" style="color:' + (S.tasks.completionRate > 0.8 ? 'var(--green)' : S.tasks.completionRate > 0.6 ? 'var(--amber)' : 'var(--red)') + '">' + (S.tasks.completionRate * 100).toFixed(0) + '%</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Avg reward</span><span class="gdp-detail-value" style="color:var(--green)">' + Math.round(S.labor.avgReward) + ' \u00a9</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Posting rate</span><span class="gdp-detail-value">' + S.tasks.postingRate.toFixed(1) + ' /hr</span></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">Reward Distribution</div>' + ['0-10', '11-50', '51-100', '100+'].map(function(bucket) {
        var pct = S.rewardDist[bucket] || 0;
        return '<div class="dist-row"><span class="dist-label">' + bucket + ' \u00a9</span><div class="dist-bar-wrap"><div class="dist-bar-fill" style="width:' + pct + '%"></div></div><span class="dist-pct">' + pct + '%</span></div>';
      }).join('') + '</div>';
  }

  function buildFilterButtons() {
    var el = document.getElementById('filter-btns');
    if (!el) return;
    el.innerHTML = EVENT_TYPES.map(function(type) {
      return '<button class="feed-btn' + (type === activeFilter ? ' active' : '') + '" onclick="setFilter(\'' + type + '\')">' + type + '</button>';
    }).join('');
  }

  function renderFeed() {
    var filtered = activeFilter === 'ALL' ? feedEvents : feedEvents.filter(function(event) { return event.type === activeFilter; });
    var el = document.getElementById('feed-scroll');
    if (!el) return;
    el.innerHTML = filtered.slice(0, 80).map(function(event, index) {
      var flash = event.type === 'PAYOUT' ? ' flash-green' : event.type === 'DISPUTE' ? ' flash-red' : '';
      var highlight = index === 0 ? ' highlight' : '';
      return '<div class="feed-item' + highlight + flash + '"><span class="feed-badge ' + event.badge + '">' + event.type + '</span><span class="feed-text">' + event.text + '</span><span class="feed-time">' + ATE.timeAgo(Date.now() - event.time) + '</span></div>';
    }).join('');
  }

  function addFeedEvent(event) {
    if (paused) return;
    feedEvents.unshift(event);
    if (feedEvents.length > 500) {
      feedEvents.length = 500;
    }
    if (event.eventId && event.eventId > lastEventId) {
      lastEventId = event.eventId;
    }
    renderFeed();
  }

  function renderLeaderboard() {
    var AGENTS = ATE.AGENTS;
    var el = document.getElementById('lb-scroll');
    if (!el) return;

    if (currentTab === 'workers') {
      var workers = AGENTS.filter(function(a) { return a.role === 'worker'; }).sort(function(a, b) { return b.tc - a.tc; });
      el.innerHTML = '<div class="lb-section-label">By Tasks Completed</div>' + workers.map(function(worker, index) {
        var initials = worker.name.replace(/[^A-Z0-9]/gi, '').slice(0, 2).toUpperCase();
        var streak = worker.streak >= 3 ? '<span style="font-size:8px;color:var(--yellow);margin-left:3px">\ud83d\udd25' + worker.streak + '</span>' : '';
        return '<div class="lb-row"><div class="lb-rank' + (index === 0 ? ' top' : '') + '">' + (index + 1) + '</div><div class="lb-avatar" style="background:' + worker.color + '18;color:' + worker.color + ';border:1px solid ' + worker.color + '33">' + initials + '</div><div class="lb-info"><div class="lb-name">' + worker.name + streak + '</div><div class="lb-meta">' + worker.tc + ' tasks completed</div><div class="lb-stars"><span class="s">\u2605\u2605\u2605</span>' + worker.dq.es + ' <span class="s">\u2605\u2605</span>' + worker.dq.s + ' <span class="s">\u2605</span>' + worker.dq.d + '</div></div><div class="lb-right"><div class="lb-amount" style="color:var(--green)">' + worker.earned.toLocaleString() + ' \u00a9</div><div class="lb-amount-label">earned</div></div></div>';
      }).join('');
      return;
    }

    var posters = AGENTS.filter(function(a) { return a.role === 'poster'; }).sort(function(a, b) { return b.tp - a.tp; });
    el.innerHTML = '<div class="lb-section-label">By Tasks Posted</div>' + posters.map(function(poster, index) {
      var initials = poster.name.replace(/[^A-Z0-9]/gi, '').slice(0, 2).toUpperCase();
      return '<div class="lb-row"><div class="lb-rank' + (index === 0 ? ' top' : '') + '">' + (index + 1) + '</div><div class="lb-avatar" style="background:' + poster.color + '18;color:' + poster.color + ';border:1px solid ' + poster.color + '33">' + initials + '</div><div class="lb-info"><div class="lb-name">' + poster.name + '</div><div class="lb-meta">' + poster.tp + ' tasks posted</div><div class="lb-stars">spec: <span class="s">\u2605\u2605\u2605</span>' + poster.sq.es + ' <span class="s">\u2605\u2605</span>' + poster.sq.s + ' <span class="s">\u2605</span>' + poster.sq.d + '</div></div><div class="lb-right"><div class="lb-amount" style="color:var(--amber)">' + poster.spent.toLocaleString() + ' \u00a9</div><div class="lb-amount-label">spent</div></div></div>';
    }).join('') +
    '<div class="spec-section"><div class="lb-section-label" style="padding:0 0 6px;margin-bottom:8px">Economy Spec Quality</div>' +
    '<div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">\u2605\u2605\u2605</span> Extremely satisfied</span><span class="spec-value">' + (S.specQ.esPct * 100).toFixed(0) + '%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:' + (S.specQ.esPct * 100).toFixed(0) + '%"></div></div></div>' +
    '<div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">\u2605\u2605</span> Satisfied</span><span class="spec-value">' + (S.specQ.sPct * 100).toFixed(0) + '%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:' + (S.specQ.sPct * 100).toFixed(0) + '%"></div></div></div>' +
    '<div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">\u2605</span> Dissatisfied</span><span class="spec-value">' + (S.specQ.dPct * 100).toFixed(0) + '%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:' + (S.specQ.dPct * 100).toFixed(0) + '%"></div></div></div></div>';
  }

  function startSSEStream() {
    sseSource = ATE.connectSSE(function(eventData) {
      var feedItem = ATE.mapEventToFeed(eventData);
      addFeedEvent(feedItem);
    }, lastEventId);
  }

  function startPeriodicUpdates() {
    // Re-fetch metrics every 10 seconds
    ATE.startMetricsPolling(function() {
      buildVitals();
      buildGDPPanel();
      ATE.buildBottomTicker(document.getElementById('bottom-ticker-track'));
    }, 10000);

    // Re-fetch agents every 30 seconds
    setInterval(async function() {
      await ATE.fetchAgents();
      renderLeaderboard();
    }, 30000);
  }

  // ── Global handlers (called from HTML onclick) ────────────
  window.setFilter = function(filter) {
    activeFilter = filter;
    buildFilterButtons();
    renderFeed();
  };

  window.togglePause = function() {
    paused = !paused;
    var button = document.getElementById('pause-btn');
    if (button) {
      button.textContent = paused ? '\u25b6 Resume' : '\u23f8 Pause';
      button.classList.toggle('paused', paused);
    }
  };

  window.switchTab = function(tab) {
    currentTab = tab;
    var workersTab = document.getElementById('tab-workers');
    var postersTab = document.getElementById('tab-posters');
    if (workersTab) workersTab.classList.toggle('active', tab === 'workers');
    if (postersTab) postersTab.classList.toggle('active', tab === 'posters');
    renderLeaderboard();
  };

  // ── Boot sequence ─────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', async function() {
    // Fetch real data first
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents()]);

    // Build initial UI
    buildVitals();
    buildGDPPanel();
    buildFilterButtons();
    renderLeaderboard();
    ATE.buildBottomTicker(document.getElementById('bottom-ticker-track'));

    // Load initial event history
    var history = await ATE.fetchEvents(50);
    if (history.events && history.events.length > 0) {
      history.events.forEach(function(evt) {
        var feedItem = ATE.mapEventToFeed(evt);
        feedEvents.push(feedItem);
        if (evt.event_id > lastEventId) {
          lastEventId = evt.event_id;
        }
      });
      renderFeed();
    }

    // Connect to SSE for live events
    startSSEStream();

    // Start periodic metric/agent refreshes
    startPeriodicUpdates();
  });
})();
```

### How to Verify

```bash
wc -l services/ui/data/web/assets/observatory.js
```

### Commit

```bash
cd /Users/flo/Developer/github/agent-economy
git add services/ui/data/web/assets/observatory.js
git commit -m "feat(ui): wire observatory to SSE stream and real API data"
```

---

## Phase 4: Run CI and Final Verification

### Run CI

```bash
cd /Users/flo/Developer/github/agent-economy/services/ui && just ci-quiet
```

This validates that no Python code was accidentally broken. The JS files are not checked by CI.

### Run Full Project CI

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

### Commit (if fixes needed)

```bash
cd /Users/flo/Developer/github/agent-economy
git add -A services/ui/data/web/
git commit -m "fix(ui): final adjustments for API wiring"
```

---

## Important Notes

- `task.js` and `task.html` are NOT modified — they are a scripted demo walkthrough with hardcoded scenario data. This is intentional.
- The `perturbEconomy()` function is kept in `shared.js` for backward compatibility with `task.js`. Landing and observatory pages do NOT use it.
- If the API returns errors (e.g., database not available), the pages will show zeros/empty data gracefully instead of crashing.
- The SSE stream auto-reconnects on errors (built into `EventSource` browser API with the `retry` directive from the server).
- Agent `role` is derived from earnings vs spending since the API doesn't expose a role field.
- Agent `color` is generated deterministically from the agent ID hash.
- Agent `streak` is always 0 since the API doesn't track streaks.
