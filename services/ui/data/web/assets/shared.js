(function() {
  'use strict';

  // ── Default empty state (populated by API calls) ──────────
  const AGENTS = [];

  const S = {
    gdp: { total: 0, last24h: 0, last7d: 0, rate: 0, perAgent: 0, delta1h: null, delta24h: null },
    agents: { total: 0, active: 0, withCompleted: 0, deltaActive: null },
    tasks: { completed24h: 0, completedAll: 0, open: 0, inExec: 0, disputed: 0, completionRate: 0, postingRate: 0, deltaOpen: null, deltaCompleted24h: null },
    escrow: { locked: 0, deltaLocked: null },
    specQ: { avg: 0, esPct: 0, sPct: 0, dPct: 0, trend: 'stable', delta: 0 },
    labor: { avgBids: 0, avgReward: 0, unemployment: 0, acceptLatency: 0, deltaAvgBids: null, deltaAvgReward: null },
    phase: 'bootstrapping',
    taskCreationTrend: 'stable',
    gdpHistory: [],
    rewardDist: { '0-10': 0, '11-50': 0, '51-100': 0, '100+': 0 },
    sparklines: {}
  };

  function timeAgo(ms) {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
    return Math.floor(seconds / 3600) + 'h ago';
  }

  function renderSparkSVG(data, w, h, fill) {
    if (!data || data.length < 2) {
      return '<svg class="sparkline" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '"></svg>';
    }
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
      S.gdp.delta1h = data.gdp.delta_1h;
      S.gdp.delta24h = data.gdp.delta_24h;

      S.agents.total = data.agents.total_registered;
      S.agents.active = data.agents.active;
      S.agents.withCompleted = data.agents.with_completed_tasks;
      S.agents.deltaActive = data.agents.delta_active;

      S.tasks.completed24h = data.tasks.completed_24h;
      S.tasks.completedAll = data.tasks.completed_all_time;
      S.tasks.open = data.tasks.open;
      S.tasks.inExec = data.tasks.in_execution;
      S.tasks.disputed = data.tasks.disputed;
      S.tasks.completionRate = data.tasks.completion_rate;
      S.tasks.postingRate = data.labor_market.task_posting_rate;
      S.tasks.deltaOpen = data.tasks.delta_open;
      S.tasks.deltaCompleted24h = data.tasks.delta_completed_24h;

      S.escrow.locked = data.escrow.total_locked;
      S.escrow.deltaLocked = data.escrow.delta_locked;

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
      S.labor.deltaAvgBids = data.labor_market.delta_avg_bids;
      S.labor.deltaAvgReward = data.labor_market.delta_avg_reward;

      S.phase = data.economy_phase.phase;
      S.taskCreationTrend = data.economy_phase.task_creation_trend || 'stable';

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
          streak: a.stats.current_streak || 0
        });
      });

      return data;
    } catch (err) {
      console.warn('[ATE] fetchAgents error:', err.message);
      return null;
    }
  }

  /**
   * Fetch GDP history for sparkline rendering.
   * Returns array of numeric GDP values or null on error.
   */
  async function fetchGDPHistory() {
    try {
      var response = await fetch('/api/metrics/gdp/history?window=24h&resolution=1h');
      if (!response.ok) {
        console.warn('[ATE] fetchGDPHistory failed:', response.status);
        return null;
      }
      var data = await response.json();
      S.gdpHistory = (data.data_points || []).map(function(point) { return point.gdp; });
      return S.gdpHistory;
    } catch (err) {
      console.warn('[ATE] fetchGDPHistory error:', err.message);
      return null;
    }
  }

  /**
   * Fetch all metric sparkline series from /api/metrics/sparklines.
   * Populates ATE.S.sparklines keyed by metric name.
   */
  async function fetchSparklines() {
    try {
      var response = await fetch('/api/metrics/sparklines?window=24h');
      if (!response.ok) {
        console.warn('[ATE] fetchSparklines failed:', response.status);
        return null;
      }
      var data = await response.json();
      S.sparklines = data.metrics || {};
      return S.sparklines;
    } catch (err) {
      console.warn('[ATE] fetchSparklines error:', err.message);
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

  // ── Ticker builders (KEEP from current shared.js) ─────────
  function buildTopTicker(trackEl) {
    var pairs = [
      { sym: 'GDP/TOTAL', val: S.gdp.total.toLocaleString(), chg: S.gdp.delta24h },
      { sym: 'TASK/OPEN', val: S.tasks.open, chg: S.tasks.deltaOpen },
      { sym: 'ESCROW/LOCK', val: S.escrow.locked.toLocaleString() + ' \u00a9', chg: S.escrow.deltaLocked },
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: S.specQ.delta },
      { sym: 'BID/AVG', val: S.labor.avgBids.toFixed(1), chg: S.labor.deltaAvgBids },
      { sym: 'AGENTS/ACT', val: S.agents.active, chg: S.agents.deltaActive },
      { sym: 'COMP/RATE', val: (S.tasks.completionRate * 100).toFixed(0) + '%', chg: S.tasks.deltaCompleted24h },
      { sym: 'GDP/RATE', val: S.gdp.rate.toFixed(1) + '/hr', chg: S.gdp.delta1h },
      { sym: 'RWD/AVG', val: Math.round(S.labor.avgReward) + ' \u00a9', chg: S.labor.deltaAvgReward },
      { sym: 'UNEMP', val: (S.labor.unemployment * 100).toFixed(1) + '%', chg: null },
      { sym: 'DISPUTES', val: S.tasks.disputed, chg: null },
      { sym: 'GDP/AGENT', val: Math.round(S.gdp.perAgent).toLocaleString(), chg: null }
    ];

    var items = pairs.concat(pairs);
    trackEl.innerHTML = items.map(function(item) {
      var chg = item.chg != null ? item.chg : 0;
      var cls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'muted';
      var arrow = chg > 0 ? '\u25b2' : chg < 0 ? '\u25bc' : '\u2013';
      var display = item.chg != null ? Math.abs(chg).toFixed(1) + '%' : '\u2013';
      return '<span class="ticker-item"><span class="sym">' + item.sym + '</span><span>' + item.val + '</span><span class="chg ' + cls + '">' + arrow + ' ' + display + '</span></span>';
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
      { sym: 'GDP/RATE', val: S.gdp.rate.toFixed(1) + ' \u00a9/hr', chg: S.gdp.delta1h != null ? (S.gdp.delta1h >= 0 ? '+' : '') + S.gdp.delta1h.toFixed(1) + '%' : '\u2013', up: S.gdp.delta1h != null ? S.gdp.delta1h >= 0 : null },
      { sym: 'POST/RATE', val: S.tasks.postingRate.toFixed(1) + '/hr', chg: 'new tasks', up: null },
      { sym: 'BID/AVG', val: S.labor.avgBids.toFixed(1) + '/task', chg: S.labor.deltaAvgBids != null ? (S.labor.deltaAvgBids >= 0 ? '+' : '') + S.labor.deltaAvgBids.toFixed(1) + '%' : '\u2013', up: S.labor.deltaAvgBids != null ? S.labor.deltaAvgBids >= 0 : null },
      { sym: 'COMP/RATE', val: (S.tasks.completionRate * 100).toFixed(0) + '%', chg: S.tasks.deltaCompleted24h != null ? (S.tasks.deltaCompleted24h >= 0 ? '+' : '') + S.tasks.deltaCompleted24h.toFixed(1) + '%' : '\u2013', up: S.tasks.deltaCompleted24h != null ? S.tasks.deltaCompleted24h >= 0 : null },
      { sym: 'SPEC/QUAL', val: Math.round(S.specQ.avg) + '%', chg: (S.specQ.delta >= 0 ? '\u2191' : '\u2193') + Math.abs(S.specQ.delta).toFixed(1) + '%', up: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null },
      { sym: 'UNEMP', val: (S.labor.unemployment * 100).toFixed(1) + '%', chg: '\u2013', up: null },
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
    timeAgo: timeAgo,
    renderSparkSVG: renderSparkSVG,
    animateCounter: animateCounter,
    agentColor: agentColor,
    // API client
    fetchMetrics: fetchMetrics,
    fetchAgents: fetchAgents,
    fetchGDPHistory: fetchGDPHistory,
    fetchSparklines: fetchSparklines,
    fetchEvents: fetchEvents,
    connectSSE: connectSSE,
    mapEventToFeed: mapEventToFeed,
    startMetricsPolling: startMetricsPolling,
    // Ticker builders
    buildTopTicker: buildTopTicker,
    buildBottomTicker: buildBottomTicker
  };
})();
