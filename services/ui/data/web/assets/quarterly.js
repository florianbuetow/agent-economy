(function() {
  'use strict';
  var ATE = window.ATE;

  var state = {
    quarter: null // "YYYY-QN"; null until first response tells us the current quarter
  };

  function parseQuarter(label) {
    var m = /^(\d{4})-Q([1-4])$/.exec(label);
    return { year: parseInt(m[1], 10), q: parseInt(m[2], 10) };
  }

  function formatQuarter(year, q) {
    return year + '-Q' + q;
  }

  function shiftQuarter(label, delta) {
    var parsed = parseQuarter(label);
    var total = (parsed.year * 4 + (parsed.q - 1)) + delta;
    var year = Math.floor(total / 4);
    var q = (total % 4) + 1;
    return formatQuarter(year, q);
  }

  // ── Data fetching ────────────────────────────────────────────
  async function fetchQuarterlyReport(quarter) {
    try {
      var url = '/api/quarterly-report' + (quarter ? '?quarter=' + encodeURIComponent(quarter) : '');
      var resp = await fetch(url);
      if (resp.status === 404) {
        // The server resolves "current quarter" even when the request omits
        // it, and reports which quarter it resolved to in details.quarter —
        // use that so an implicit "no data yet" still shows the right label.
        var errorBody = await resp.json().catch(function() { return null; });
        var resolvedQuarter = (errorBody && errorBody.details && errorBody.details.quarter) || quarter;
        return { notFound: true, quarter: resolvedQuarter };
      }
      if (!resp.ok) {
        return null;
      }
      return await resp.json();
    } catch (e) {
      console.warn('[quarterly] fetchQuarterlyReport error:', e.message);
      return null;
    }
  }

  // ── Rendering ────────────────────────────────────────────────
  function deltaSpan(pct) {
    if (pct === null || pct === undefined) return '<span class="metric-delta muted">—</span>';
    var cls = pct > 0 ? 'up' : pct < 0 ? 'down' : 'muted';
    var arrow = pct > 0 ? '↑' : pct < 0 ? '↓' : '–';
    return '<span class="metric-delta ' + cls + '">' + arrow + ' ' + Math.abs(pct).toFixed(1) + '% vs prior</span>';
  }

  function renderNotableTask(label, task) {
    if (!task) {
      return '<div class="notable-row"><span class="nr-title dim">' + label + ': none this quarter</span></div>';
    }
    var meta = task.reward !== null && task.reward !== undefined
      ? task.reward + ' ©'
      : (task.bid_count !== null && task.bid_count !== undefined ? task.bid_count + ' bids' : '');
    return '<div class="notable-row" data-task-id="' + task.task_id + '">' +
      '<span class="label" style="width:130px;flex-shrink:0">' + label + '</span>' +
      '<span class="nr-title">' + task.title + '</span>' +
      '<span class="nr-meta">' + meta + '</span>' +
    '</div>';
  }

  function renderAgentList(agents, amountKey, amountLabel) {
    if (!agents.length) {
      return '<div class="notable-empty">No data this quarter.</div>';
    }
    return agents.map(function(a, index) {
      var amount = a[amountKey];
      return '<div class="notable-row" data-agent-id="' + a.agent_id + '">' +
        '<span class="label" style="width:20px;flex-shrink:0">' + (index + 1) + '</span>' +
        '<span class="nr-title">' + a.name + '</span>' +
        '<span class="nr-meta">' + (amount !== null && amount !== undefined ? amount.toLocaleString() + ' ©' : '') + ' ' + amountLabel + '</span>' +
      '</div>';
    }).join('');
  }

  function renderReport(report) {
    var gdp = report.gdp, tasks = report.tasks, labor = report.labor_market;
    var specQ = report.spec_quality, agents = report.agents, notable = report.notable;

    return '<div class="card">' +
        '<div class="card-header"><span class="card-label">Period</span></div>' +
        '<div class="card-body" style="font-size:11px;color:var(--text-mid)">' +
          report.period.start + ' → ' + report.period.end +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">💰 GDP</span></div>' +
        '<div class="metric-grid">' +
          '<div class="metric-cell"><div class="metric-value">' + gdp.total.toLocaleString() + ' ©</div><div class="metric-label">Total</div>' + deltaSpan(gdp.delta_pct) + '</div>' +
          '<div class="metric-cell"><div class="metric-value">' + gdp.previous_quarter.toLocaleString() + ' ©</div><div class="metric-label">Previous Quarter</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + Math.round(gdp.per_agent).toLocaleString() + ' ©</div><div class="metric-label">Per Agent</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + agents.new_registrations + '</div><div class="metric-label">New Agents</div></div>' +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">📋 Tasks &amp; Labor Market</span></div>' +
        '<div class="metric-grid">' +
          '<div class="metric-cell"><div class="metric-value">' + tasks.posted + '</div><div class="metric-label">Posted</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + tasks.completed + '</div><div class="metric-label">Completed</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + tasks.disputed + '</div><div class="metric-label">Disputed</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + (tasks.completion_rate * 100).toFixed(0) + '%</div><div class="metric-label">Completion Rate</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + labor.avg_bids_per_task.toFixed(1) + '</div><div class="metric-label">Avg Bids/Task</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + labor.avg_time_to_acceptance_minutes.toFixed(0) + 'm</div><div class="metric-label">Avg Time to Accept</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + Math.round(labor.avg_reward).toLocaleString() + ' ©</div><div class="metric-label">Avg Reward</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + agents.total_at_quarter_end + '</div><div class="metric-label">Agents at Quarter End</div></div>' +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">⭐ Specification Quality</span></div>' +
        '<div class="metric-grid" style="grid-template-columns:repeat(3,1fr)">' +
          '<div class="metric-cell"><div class="metric-value">' + (specQ.avg_score * 100).toFixed(0) + '%</div><div class="metric-label">Avg Score</div></div>' +
          '<div class="metric-cell"><div class="metric-value">' + (specQ.previous_quarter_avg * 100).toFixed(0) + '%</div><div class="metric-label">Previous Quarter</div></div>' +
          '<div class="metric-cell">' + deltaSpan(specQ.delta_pct) + '<div class="metric-label" style="margin-top:8px">Change</div></div>' +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">🏅 Notable</span></div>' +
        '<div class="card-body" style="padding:0">' +
          renderNotableTask('Highest Value Task', notable.highest_value_task) +
          renderNotableTask('Most Competitive Task', notable.most_competitive_task) +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">Top Agents This Quarter</span></div>' +
        '<div class="card-body">' +
          '<div class="top-list">' +
            '<div><div class="label" style="margin-bottom:6px">Top Workers (by earnings)</div>' + renderAgentList(notable.top_workers, 'earned', 'earned') + '</div>' +
            '<div><div class="label" style="margin-bottom:6px">Top Posters (by spend)</div>' + renderAgentList(notable.top_posters, 'spent', 'spent') + '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
  }

  function wireNotableLinks(root) {
    root.querySelectorAll('[data-task-id]').forEach(function(el) {
      el.style.cursor = 'pointer';
      el.addEventListener('click', function() {
        window.location = '/task.html?task_id=' + encodeURIComponent(el.getAttribute('data-task-id'));
      });
    });
    root.querySelectorAll('[data-agent-id]').forEach(function(el) {
      el.style.cursor = 'pointer';
      el.addEventListener('click', function() {
        window.location = '/agent.html?agent_id=' + encodeURIComponent(el.getAttribute('data-agent-id'));
      });
    });
  }

  async function loadQuarter(quarter) {
    var content = document.getElementById('qr-content');
    var label = document.getElementById('qr-quarter-label');

    var report = await fetchQuarterlyReport(quarter);

    if (report === null) {
      content.innerHTML = '<div class="card"><div class="card-body notable-empty">Could not load the quarterly report.</div></div>';
      return;
    }

    if (report.notFound) {
      state.quarter = report.quarter;
      label.textContent = report.quarter;
      content.innerHTML = '<div class="card"><div class="card-body notable-empty">No economy data exists for ' + report.quarter + '.</div></div>';
      return;
    }

    state.quarter = report.quarter;
    label.textContent = report.quarter;
    content.innerHTML = renderReport(report);
    wireNotableLinks(content);
  }

  // ── Bottom ticker ────────────────────────────────────────────
  function buildTicker() {
    if (ATE && typeof ATE.buildBottomTicker === 'function') {
      ATE.buildBottomTicker(document.getElementById('ticker-track'));
    }
  }

  // ── Boot ─────────────────────────────────────────────────────
  async function init() {
    await ATE.fetchMetrics();
    await ATE.fetchAgents();
    buildTicker();

    var params = new URLSearchParams(window.location.search);
    var requestedQuarter = params.get('quarter');
    await loadQuarter(requestedQuarter);

    document.getElementById('qr-prev').addEventListener('click', function() {
      if (!state.quarter) return;
      loadQuarter(shiftQuarter(state.quarter, -1));
    });
    document.getElementById('qr-next').addEventListener('click', function() {
      if (!state.quarter) return;
      loadQuarter(shiftQuarter(state.quarter, 1));
    });
  }

  init();
})();
