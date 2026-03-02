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
    var gdpSpark = S.gdpHistory.length >= 2 ? S.gdpHistory : [];
    var perAgentSpark = S.gdpHistory.length >= 2 && S.agents.active > 0 ? S.gdpHistory.map(function(v) { return v / S.agents.active; }) : [];
    var trendColor = S.gdp.rate > 0 ? 'var(--green)' : 'var(--red)';
    var phaseColor = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-mid)';
    var phaseBorder = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-dim)';
    var distTotal = Object.values(S.rewardDist).reduce(function(acc, val) { return acc + val; }, 0) || 1;

    var trendArrow = S.taskCreationTrend === 'growing' ? '\u2191' : S.taskCreationTrend === 'declining' ? '\u2193' : '\u2192';
    var trendLabel = S.taskCreationTrend;
    var trendClr = S.taskCreationTrend === 'growing' ? 'var(--green)' : S.taskCreationTrend === 'declining' ? 'var(--red)' : 'var(--amber)';
    var disputeRate = (S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100;
    var disputeColor = disputeRate > 15 ? 'var(--red)' : disputeRate > 5 ? 'var(--amber)' : 'var(--green)';

    var panel = document.getElementById('gdp-panel');
    if (!panel) return;

    panel.innerHTML =
      '<div class="gdp-section"><div class="gdp-section-label">Economy Output</div><div class="gdp-spark-row"><div><div class="gdp-big" style="color:' + trendColor + '">' + S.gdp.total.toLocaleString() + '</div><div class="gdp-unit">\u00a9 total GDP</div></div><div style="display:flex;align-items:center;gap:4px"><span style="font-size:10px;font-weight:600;color:' + trendColor + '">\u2191 ' + S.gdp.rate.toFixed(1) + '/hr</span></div></div><div style="margin-top:10px">' + ATE.renderSparkSVG(gdpSpark, 300, 56, true) + '</div><div style="margin-top:8px"><div class="gdp-detail-row"><span class="gdp-detail-label">Rate</span><span class="gdp-detail-value" style="color:' + trendColor + '">\u2191 ' + S.gdp.rate.toFixed(1) + ' \u00a9/hr</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Last 24h</span><span class="gdp-detail-value">' + S.gdp.last24h.toLocaleString() + ' \u00a9</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Last 7d</span><span class="gdp-detail-value">' + S.gdp.last7d.toLocaleString() + ' \u00a9</span></div></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">GDP / Agent</div><div class="gdp-spark-row"><div><div style="font-size:20px;font-weight:700;color:' + trendColor + '">' + Math.round(S.gdp.perAgent).toLocaleString() + '</div></div></div><div style="margin-top:8px">' + ATE.renderSparkSVG(perAgentSpark, 300, 40, false) + '</div><div style="margin-top:6px"><div class="gdp-detail-row"><span class="gdp-detail-label">Active</span><span class="gdp-detail-value">' + S.agents.active + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Registered</span><span class="gdp-detail-value">' + S.agents.total + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">With completed</span><span class="gdp-detail-value">' + S.agents.withCompleted + '</span></div></div></div>' +
      '<div class="gdp-section"><div class="gdp-section-label">Economy Phase</div><div style="margin-bottom:6px"><span class="gdp-phase-badge" style="color:' + phaseColor + ';border-color:' + phaseBorder + '">' + S.phase.toUpperCase() + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Task creation</span><span class="gdp-detail-value" style="color:' + trendClr + '">' + trendArrow + ' ' + trendLabel + '</span></div><div class="gdp-detail-row"><span class="gdp-detail-label">Dispute rate</span><span class="gdp-detail-value" style="color:' + disputeColor + '">' + disputeRate.toFixed(1) + '%</span></div></div>' +
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
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory()]);

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
