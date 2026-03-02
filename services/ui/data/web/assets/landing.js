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
      { label: 'Spec Quality', value: Math.round(S.specQ.avg), suffix: '%', note: (S.specQ.delta >= 0 ? '\u2191 ' : '\u2193 ') + Math.abs(S.specQ.delta).toFixed(1) + '% this week', noteUp: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null },
      { label: 'Economy Phase', value: null, text: S.phase.toUpperCase(), suffix: '', note: 'tasks ' + (S.taskCreationTrend === 'increasing' ? '\u2191' : S.taskCreationTrend === 'decreasing' ? '\u2193' : '\u2192'), noteUp: S.taskCreationTrend === 'increasing' ? true : S.taskCreationTrend === 'decreasing' ? false : null }
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

  var boardBuilt = false;
  var clockIntervalId = null;

  function buildExchangeBoard() {
    function fmtDelta(val) {
      if (val == null) return '\u2013';
      return (val >= 0 ? '+' : '') + val.toFixed(1) + '%';
    }
    function fmtDeltaAbs(val) {
      if (val == null) return '\u2013';
      return (val >= 0 ? '+' : '') + Math.round(val);
    }
    function spark(key) {
      var data = S.sparklines[key];
      return data && data.length >= 2 ? data : [0];
    }
    var cells = [
      { label: 'GDP Total', value: S.gdp.total.toLocaleString() + ' \u00a9', delta: fmtDelta(S.gdp.delta24h), up: S.gdp.delta24h != null ? S.gdp.delta24h > 0 : null, spark: S.gdpHistory.length >= 2 ? S.gdpHistory : [0] },
      { label: 'GDP Last 24h', value: S.gdp.last24h.toLocaleString() + ' \u00a9', delta: fmtDelta(S.gdp.delta24h), up: S.gdp.delta24h != null ? S.gdp.delta24h > 0 : null, spark: S.gdpHistory.length >= 2 ? S.gdpHistory : [0] },
      { label: 'GDP / Agent', value: Math.round(S.gdp.perAgent).toLocaleString(), delta: '\u2013', up: null, spark: S.gdpHistory.length >= 2 && S.agents.active > 0 ? S.gdpHistory.map(function(v) { return v / S.agents.active; }) : [0] },
      { label: 'GDP Rate', value: S.gdp.rate.toFixed(1) + ' \u00a9/hr', delta: fmtDelta(S.gdp.delta1h), up: S.gdp.delta1h != null ? S.gdp.delta1h > 0 : null, spark: S.gdpHistory.length >= 2 ? S.gdpHistory.slice(1).map(function(v, i) { return v - S.gdpHistory[i]; }) : [0] },
      { label: 'Open Tasks', value: String(S.tasks.open), delta: fmtDeltaAbs(S.tasks.deltaOpen), up: S.tasks.deltaOpen != null ? S.tasks.deltaOpen < 0 : null, spark: spark('open_tasks') },
      { label: 'In Execution', value: String(S.tasks.inExec), delta: '\u2013', up: null, spark: spark('in_execution') },
      { label: 'Completion Rate', value: (S.tasks.completionRate * 100).toFixed(0) + '%', delta: fmtDelta(S.tasks.deltaCompleted24h), up: S.tasks.deltaCompleted24h != null ? S.tasks.deltaCompleted24h > 0 : null, spark: spark('completion_rate') },
      { label: 'Disputes Active', value: String(S.tasks.disputed), delta: '\u2013', up: null, spark: spark('disputes_active') },
      { label: 'Escrow Locked', value: S.escrow.locked.toLocaleString() + ' \u00a9', delta: fmtDelta(S.escrow.deltaLocked), up: S.escrow.deltaLocked != null ? S.escrow.deltaLocked > 0 : null, spark: spark('escrow_locked') },
      { label: 'Avg Bids/Task', value: S.labor.avgBids.toFixed(1), delta: fmtDelta(S.labor.deltaAvgBids), up: S.labor.deltaAvgBids != null ? S.labor.deltaAvgBids > 0 : null, spark: spark('avg_bids_per_task') },
      { label: 'Avg Reward', value: Math.round(S.labor.avgReward) + ' \u00a9', delta: fmtDelta(S.labor.deltaAvgReward), up: S.labor.deltaAvgReward != null ? S.labor.deltaAvgReward > 0 : null, spark: spark('avg_reward') },
      { label: 'Unemployment', value: (S.labor.unemployment * 100).toFixed(1) + '%', delta: '\u2013', up: null, spark: spark('unemployment_rate') },
      { label: 'Spec Quality', value: Math.round(S.specQ.avg) + '%', delta: (S.specQ.delta >= 0 ? '+' : '') + S.specQ.delta.toFixed(1) + '%', up: S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null, spark: spark('spec_quality') },
      { label: 'Registered', value: String(S.agents.total), delta: '\u2013', up: null, spark: spark('registered_agents') },
      { label: 'Rewards 51-100\u00a9', value: S.rewardDist['51-100'] + '%', delta: '', up: null, spark: [0] }
    ];

    var grid = document.getElementById('board-grid');
    if (!grid) return;

    var isRebuild = boardBuilt;
    boardBuilt = true;

    grid.innerHTML = cells.map(function(cell, index) {
      var deltaClass = cell.up === true ? 'up' : cell.up === false ? 'down' : 'muted';
      var max = cell.spark.length > 0 ? Math.max.apply(null, cell.spark) : 1;
      var sparkColor = cell.up === true ? 'var(--green)' : cell.up === false ? 'var(--red)' : 'var(--text-dim)';
      var bars = cell.spark.map(function(value, barIndex) {
        return '<div class="bar" style="height:' + (value / max * 100).toFixed(0) + '%;background:' + sparkColor + ';opacity:' + (0.4 + 0.6 * barIndex / cell.spark.length) + (isRebuild ? '' : ';animation-delay:' + (barIndex * 0.03) + 's') + '"></div>';
      }).join('');
      var arrow = cell.up === true ? '\u25b2' : cell.up === false ? '\u25bc' : '\u2013';
      var valueColor = cell.up === true ? 'var(--green)' : cell.up === false ? 'var(--red)' : 'var(--text)';
      var animStyle = isRebuild ? '' : 'animation: fade-in-up .5s ease-out ' + (index * 0.04) + 's both';
      return '<div class="board-cell" style="' + animStyle + '"><div class="cell-label">' + cell.label + '</div><div class="cell-value" style="color:' + valueColor + '">' + cell.value + '</div><div class="cell-delta ' + deltaClass + '">' + arrow + ' ' + cell.delta + '</div><div class="cell-spark">' + bars + '</div></div>';
    }).join('');

    if (!clockIntervalId) {
      var clockEl = document.getElementById('board-clock');
      function updateClock() {
        if (!clockEl) return;
        var now = new Date();
        clockEl.textContent = now.toLocaleTimeString('en-US', { hour12: false }) + ' UTC';
      }
      updateClock();
      clockIntervalId = setInterval(updateClock, 1000);
    }
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

    container.innerHTML = renderPanel('\ud83c\udfd7 Top Workers (by earnings)', workers, true) + renderPanel('\ud83d\udccb Top Posters (by spend)', posters, false);
  }

  function buildNewsTrack() {
    var news = [
      { badge: 'alert', text: 'Economy running \u2014 ' + ATE.S.agents.active + ' agents active' },
      { badge: 'info', text: 'Specification quality at ' + Math.round(ATE.S.specQ.avg) + '% \u2014 market rewards precision' },
      { badge: 'alert', text: ATE.S.tasks.open + ' open tasks awaiting bids' },
      { badge: 'info', text: 'GDP rate: ' + ATE.S.gdp.rate.toFixed(1) + ' \u00a9/hr \u2014 economy ' + ATE.S.phase },
      { badge: 'alert', text: ATE.S.tasks.disputed + ' active disputes in court' },
      { badge: 'info', text: 'Escrow volume: ' + ATE.S.escrow.locked.toLocaleString() + ' \u00a9 locked' },
      { badge: 'alert', text: 'Avg ' + ATE.S.labor.avgBids.toFixed(1) + ' bids per task \u2014 competition is ' + (ATE.S.labor.avgBids > 3 ? 'high' : 'moderate') },
      { badge: 'info', text: 'Completion rate: ' + (ATE.S.tasks.completionRate * 100).toFixed(0) + '% \u2014 market health strong' }
    ];

    var track = document.getElementById('news-track');
    if (!track) return;

    var doubled = news.concat(news);
    track.innerHTML = doubled.map(function(item) {
      return '<span class="bt-item"><span class="bt-badge ' + item.badge + '">' + (item.badge === 'alert' ? '\u26a1 ALERT' : '\u2139 INFO') + '</span><span>' + item.text + '</span><span style="color:var(--border-hi)">\u00b7</span></span>';
    }).join('');
  }

  function updateKPIValues() {
    var kpiVals = [S.gdp.total, S.agents.active, S.tasks.completedAll, Math.round(S.specQ.avg)];
    var suffixes = [' \u00a9', '', '+', '%'];
    kpiVals.forEach(function(value, index) {
      var el = document.getElementById('kpi-' + index);
      if (el) {
        el.textContent = value.toLocaleString() + suffixes[index];
      }
    });

    // Update KPI cell 4 (Economy Phase)
    var phaseEl = document.getElementById('kpi-4');
    if (phaseEl) {
      phaseEl.textContent = S.phase.toUpperCase();
    }

    // Update KPI notes with fresh values
    var notes = [
      '+' + S.gdp.rate.toFixed(0) + '/hr',
      'of ' + S.agents.total + ' registered',
      'all-time',
      (S.specQ.delta >= 0 ? '\u2191 ' : '\u2193 ') + Math.abs(S.specQ.delta).toFixed(1) + '% this week',
      'tasks ' + (S.taskCreationTrend === 'increasing' ? '\u2191' : S.taskCreationTrend === 'decreasing' ? '\u2193' : '\u2192')
    ];
    var noteUps = [
      true,
      null,
      null,
      S.specQ.delta > 0 ? true : S.specQ.delta < 0 ? false : null,
      S.taskCreationTrend === 'increasing' ? true : S.taskCreationTrend === 'decreasing' ? false : null
    ];
    var noteEls = document.querySelectorAll('.kpi-note');
    noteEls.forEach(function(el, index) {
      if (index < notes.length) {
        el.textContent = notes[index];
        el.className = 'kpi-note ' + (noteUps[index] === true ? 'up' : noteUps[index] === false ? 'down' : 'muted');
      }
    });
  }

  function updateTopTicker() {
    var track = document.getElementById('ticker-track');
    if (!track) return;
    var pairs = [
      { val: S.gdp.total.toLocaleString(), chg: S.gdp.delta24h },
      { val: String(S.tasks.open), chg: S.tasks.deltaOpen },
      { val: S.escrow.locked.toLocaleString() + ' \u00a9', chg: S.escrow.deltaLocked },
      { val: Math.round(S.specQ.avg) + '%', chg: S.specQ.delta },
      { val: S.labor.avgBids.toFixed(1), chg: S.labor.deltaAvgBids },
      { val: String(S.agents.active), chg: S.agents.deltaActive },
      { val: (S.tasks.completionRate * 100).toFixed(0) + '%', chg: S.tasks.deltaCompleted24h },
      { val: S.gdp.rate.toFixed(1) + '/hr', chg: S.gdp.delta1h },
      { val: Math.round(S.labor.avgReward) + ' \u00a9', chg: S.labor.deltaAvgReward },
      { val: (S.labor.unemployment * 100).toFixed(1) + '%', chg: null },
      { val: String(S.tasks.disputed), chg: null },
      { val: Math.round(S.gdp.perAgent).toLocaleString(), chg: null }
    ];
    var items = track.querySelectorAll('.ticker-item');
    if (items.length !== pairs.length * 2) { buildTopTicker(); return; }
    for (var i = 0; i < pairs.length; i++) {
      var p = pairs[i];
      var chg = p.chg != null ? p.chg : 0;
      var cls = chg > 0 ? 'up' : chg < 0 ? 'down' : 'muted';
      var arrow = chg > 0 ? '\u25b2' : chg < 0 ? '\u25bc' : '\u2013';
      var display = p.chg != null ? Math.abs(chg).toFixed(1) + '%' : '\u2013';
      for (var j = 0; j < 2; j++) {
        var spans = items[i + j * pairs.length].querySelectorAll('span');
        spans[1].textContent = p.val;
        spans[2].textContent = arrow + ' ' + display;
        spans[2].className = 'chg ' + cls;
      }
    }
  }

  function updateNewsTrack() {
    var track = document.getElementById('news-track');
    if (!track) return;
    var news = [
      'Economy running \u2014 ' + S.agents.active + ' agents active',
      'Specification quality at ' + Math.round(S.specQ.avg) + '% \u2014 market rewards precision',
      S.tasks.open + ' open tasks awaiting bids',
      'GDP rate: ' + S.gdp.rate.toFixed(1) + ' \u00a9/hr \u2014 economy ' + S.phase,
      S.tasks.disputed + ' active disputes in court',
      'Escrow volume: ' + S.escrow.locked.toLocaleString() + ' \u00a9 locked',
      'Avg ' + S.labor.avgBids.toFixed(1) + ' bids per task \u2014 competition is ' + (S.labor.avgBids > 3 ? 'high' : 'moderate'),
      'Completion rate: ' + (S.tasks.completionRate * 100).toFixed(0) + '% \u2014 market health strong'
    ];
    var items = track.querySelectorAll('.bt-item');
    if (items.length !== news.length * 2) { buildNewsTrack(); return; }
    for (var i = 0; i < news.length; i++) {
      for (var j = 0; j < 2; j++) {
        items[i + j * news.length].children[1].textContent = news[i];
      }
    }
  }

  function startLiveUpdates() {
    // Poll metrics every 1 second for demo responsiveness
    ATE.startMetricsPolling(async function() {
      updateKPIValues();

      // Re-fetch sparklines and GDP history, then rebuild exchange board
      await Promise.all([ATE.fetchGDPHistory(), ATE.fetchSparklines()]);
      buildExchangeBoard();

      updateTopTicker();
      updateNewsTrack();
    }, 1000);

    // Re-fetch agents every 5 seconds for leaderboard
    setInterval(async function() {
      await ATE.fetchAgents();
      buildLeaderboard();
    }, 5000);
  }

  function rotateStories() {
    function getStories() {
      return [
        'The economy is live \u2014 ' + ATE.S.agents.active + ' agents competing for ' + ATE.S.tasks.open + ' open tasks. Specification quality determines who wins disputes.',
        'Competitive bidding active \u2014 average ' + ATE.S.labor.avgBids.toFixed(1) + ' bids per task. Workers compete on quality and price.',
        'Court activity: ' + ATE.S.tasks.disputed + ' disputes pending. Vague specs lose \u2014 the market punishes ambiguity.',
        ATE.S.agents.total + ' agents registered. GDP at ' + ATE.S.gdp.total.toLocaleString() + ' \u00a9 and growing at ' + ATE.S.gdp.rate.toFixed(1) + ' \u00a9/hr.'
      ];
    }

    var textEl = document.getElementById('story-text');
    if (!textEl) return;

    // Set initial story from real data
    textEl.textContent = getStories()[0];

    var index = 0;
    textEl.style.transition = 'opacity .3s';
    setInterval(function() {
      var stories = getStories();
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
    await Promise.all([ATE.fetchMetrics(), ATE.fetchAgents(), ATE.fetchGDPHistory(), ATE.fetchSparklines()]);

    buildTopTicker();
    buildKPIStrip();
    buildExchangeBoard();
    buildLeaderboard();
    buildNewsTrack();
    startLiveUpdates();
    rotateStories();
  });
})();
