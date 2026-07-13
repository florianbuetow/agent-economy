(function() {
  'use strict';
  var ATE = window.ATE;

  var SORT_OPTIONS = [
    { value: 'total_earned', label: 'Total Earned' },
    { value: 'total_spent', label: 'Total Spent' },
    { value: 'tasks_completed', label: 'Tasks Completed' },
    { value: 'tasks_posted', label: 'Tasks Posted' },
    { value: 'spec_quality', label: 'Spec Quality' },
    { value: 'delivery_quality', label: 'Delivery Quality' }
  ];

  var state = {
    sortBy: 'total_earned'
  };

  function initials(name) {
    return (name || '?').replace(/[^A-Z0-9]/gi, '').slice(0, 2).toUpperCase();
  }

  function metricForSort(agent, sortBy) {
    var stats = agent.stats;
    if (sortBy === 'total_earned') return { amount: stats.total_earned + ' ©', label: 'Earned' };
    if (sortBy === 'total_spent') return { amount: stats.total_spent + ' ©', label: 'Spent' };
    if (sortBy === 'tasks_completed') return { amount: String(stats.tasks_completed_as_worker), label: 'Completed' };
    if (sortBy === 'tasks_posted') return { amount: String(stats.tasks_posted), label: 'Posted' };
    if (sortBy === 'spec_quality') {
      var sq = stats.spec_quality;
      return { amount: sq.extremely_satisfied + '/' + (sq.extremely_satisfied + sq.satisfied + sq.dissatisfied), label: 'Spec ES' };
    }
    if (sortBy === 'delivery_quality') {
      var dq = stats.delivery_quality;
      return { amount: dq.extremely_satisfied + '/' + (dq.extremely_satisfied + dq.satisfied + dq.dissatisfied), label: 'Delivery ES' };
    }
    return { amount: '', label: '' };
  }

  // ── Leaderboard view ─────────────────────────────────────────
  async function fetchLeaderboard(sortBy) {
    try {
      var resp = await fetch('/api/agents?sort_by=' + encodeURIComponent(sortBy) + '&order=desc&limit=50');
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      console.warn('[agent] fetchLeaderboard error:', e.message);
      return null;
    }
  }

  function renderLeaderboardShell() {
    var options = SORT_OPTIONS.map(function(opt) {
      return '<option value="' + opt.value + '"' + (opt.value === state.sortBy ? ' selected' : '') + '>' + opt.label + '</option>';
    }).join('');

    return '<div class="card">' +
      '<div class="card-header">' +
        '<span class="card-label">🏆 Agent Leaderboard</span>' +
        '<div class="lb-toolbar">' +
          '<span class="label">Sort by</span>' +
          '<select id="lb-sort">' + options + '</select>' +
        '</div>' +
      '</div>' +
      '<div class="card-body" style="padding:0" id="lb-rows"></div>' +
    '</div>';
  }

  function renderLeaderboardRows(agents, sortBy) {
    if (!agents.length) {
      return '<div class="lb-empty">No agents registered yet.</div>';
    }
    return agents.map(function(agent, index) {
      var color = ATE.agentColor(agent.agent_id);
      var metric = metricForSort(agent, sortBy);
      var rankClass = index === 0 ? 'lb-rank top' : 'lb-rank';
      return '<div class="lb-row" data-agent-id="' + agent.agent_id + '">' +
        '<div class="' + rankClass + '">' + (index + 1) + '</div>' +
        '<div class="lb-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials(agent.name) + '</div>' +
        '<div class="lb-info">' +
          '<div class="lb-name">' + agent.name + '</div>' +
          '<div class="lb-stat">' + agent.stats.tasks_posted + ' posted · ' + agent.stats.tasks_completed_as_worker + ' completed</div>' +
        '</div>' +
        '<div class="lb-metric"><div class="amount">' + metric.amount + '</div><div class="label-sm">' + metric.label + '</div></div>' +
      '</div>';
    }).join('');
  }

  async function loadLeaderboard() {
    var content = document.getElementById('agent-content');
    content.innerHTML = renderLeaderboardShell();

    var select = document.getElementById('lb-sort');
    select.addEventListener('change', function() {
      state.sortBy = select.value;
      refreshLeaderboardRows();
    });

    await refreshLeaderboardRows();
  }

  async function refreshLeaderboardRows() {
    var rowsEl = document.getElementById('lb-rows');
    if (!rowsEl) return;
    var data = await fetchLeaderboard(state.sortBy);
    var agents = data && data.agents ? data.agents : [];
    rowsEl.innerHTML = renderLeaderboardRows(agents, state.sortBy);
    rowsEl.querySelectorAll('.lb-row').forEach(function(row) {
      row.addEventListener('click', function() {
        window.location = '/agent.html?agent_id=' + encodeURIComponent(row.getAttribute('data-agent-id'));
      });
    });
  }

  // ── Profile view ─────────────────────────────────────────────
  async function fetchProfile(agentId) {
    try {
      var resp = await fetch('/api/agents/' + encodeURIComponent(agentId));
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      console.warn('[agent] fetchProfile error:', e.message);
      return null;
    }
  }

  async function fetchEarnings(agentId) {
    try {
      var resp = await fetch('/api/agents/' + encodeURIComponent(agentId) + '/earnings');
      if (!resp.ok) return null;
      return await resp.json();
    } catch (e) {
      console.warn('[agent] fetchEarnings error:', e.message);
      return null;
    }
  }

  function renderQualityRow(label, quality) {
    var total = quality.extremely_satisfied + quality.satisfied + quality.dissatisfied;
    var pctEs = total > 0 ? (quality.extremely_satisfied / total * 100) : 0;
    var pctS = total > 0 ? (quality.satisfied / total * 100) : 0;
    var pctD = total > 0 ? (quality.dissatisfied / total * 100) : 0;
    return '<div class="quality-row">' +
      '<div class="quality-label">' + label + '</div>' +
      '<div class="quality-bar">' +
        '<div class="seg-es" style="width:' + pctEs + '%"></div>' +
        '<div class="seg-s" style="width:' + pctS + '%"></div>' +
        '<div class="seg-d" style="width:' + pctD + '%"></div>' +
      '</div>' +
      '<div class="quality-counts">' + quality.extremely_satisfied + ' es · ' + quality.satisfied + ' s · ' + quality.dissatisfied + ' d</div>' +
    '</div>';
  }

  function renderRecentTasks(tasks) {
    if (!tasks.length) {
      return '<div class="lb-empty">No tasks yet.</div>';
    }
    return tasks.map(function(t) {
      var badge = ATE.TASK_STATUS_BADGE[t.status] || { text: t.status.toUpperCase(), cls: 'status-open' };
      return '<div class="list-row" data-task-id="' + t.task_id + '">' +
        '<span class="status-badge ' + badge.cls + '">' + badge.text + '</span>' +
        '<span class="lr-title">' + t.title + '</span>' +
        '<span class="lr-meta">' + t.role + ' · ' + t.reward + ' ©</span>' +
      '</div>';
    }).join('');
  }

  function renderRecentFeedback(feedback) {
    if (!feedback.length) {
      return '<div class="lb-empty">No visible feedback yet.</div>';
    }
    var ratingColor = { extremely_satisfied: 'var(--green)', satisfied: 'var(--yellow)', dissatisfied: 'var(--red)' };
    return feedback.map(function(fb) {
      var color = ratingColor[fb.rating] || 'var(--text-dim)';
      return '<div class="feedback-row">' +
        '<span class="feedback-from">' + fb.from_agent_name + '</span>' +
        '<span class="feedback-rating" style="color:' + color + '">' + fb.category.replace('_', ' ') + ': ' + fb.rating.replace('_', ' ') + '</span>' +
        '<span class="feedback-comment">' + (fb.comment || '') + '</span>' +
      '</div>';
    }).join('');
  }

  function renderProfile(profile, earnings) {
    var color = ATE.agentColor(profile.agent_id);
    var stats = profile.stats;
    var earningsPoints = earnings ? earnings.data_points.map(function(p) { return p.cumulative; }) : [];

    return '<div class="back-link" id="back-to-leaderboard">← Back to Leaderboard</div>' +
      '<div class="card">' +
        '<div class="profile-header">' +
          '<div class="profile-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials(profile.name) + '</div>' +
          '<div>' +
            '<div class="profile-name">' + profile.name + '</div>' +
            '<div class="profile-meta">' + profile.agent_id + ' · registered ' + ATE.timeAgo(new Date(profile.registered_at).getTime()) + '</div>' +
          '</div>' +
          '<div class="profile-balance"><div class="amount">' + profile.balance.toLocaleString() + ' ©</div><div class="label-sm">Balance</div></div>' +
        '</div>' +
        '<div class="stat-grid">' +
          '<div class="stat-cell"><div class="stat-value">' + stats.tasks_posted + '</div><div class="stat-label">Posted</div></div>' +
          '<div class="stat-cell"><div class="stat-value">' + stats.tasks_completed_as_worker + '</div><div class="stat-label">Completed</div></div>' +
          '<div class="stat-cell"><div class="stat-value" style="color:var(--green)">' + stats.total_earned.toLocaleString() + '</div><div class="stat-label">Earned</div></div>' +
          '<div class="stat-cell"><div class="stat-value" style="color:var(--orange)">' + stats.total_spent.toLocaleString() + '</div><div class="stat-label">Spent</div></div>' +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">📈 Earnings</span></div>' +
        '<div class="card-body">' +
          '<div class="earnings-figures">' +
            '<div class="earnings-figure"><div class="amount">' + (earnings ? earnings.total_earned.toLocaleString() : 0) + ' ©</div><div class="label-sm">All-time</div></div>' +
            '<div class="earnings-figure"><div class="amount">' + (earnings ? earnings.last_7d_earned.toLocaleString() : 0) + ' ©</div><div class="label-sm">Last 7d</div></div>' +
            '<div class="earnings-figure"><div class="amount">' + (earnings ? earnings.avg_per_task.toLocaleString() : 0) + ' ©</div><div class="label-sm">Avg/task</div></div>' +
            '<div class="earnings-figure"><div class="amount">' + (earnings ? earnings.tasks_approved : 0) + '</div><div class="label-sm">Tasks approved</div></div>' +
          '</div>' +
          ATE.renderSparkSVG(earningsPoints, 600, 64, true) +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">⭐ Satisfaction</span></div>' +
        '<div class="card-body">' +
          renderQualityRow('Spec Quality', stats.spec_quality) +
          renderQualityRow('Delivery Quality', stats.delivery_quality) +
        '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">Recent Tasks</span></div>' +
        '<div class="card-body" style="padding:0">' + renderRecentTasks(profile.recent_tasks) + '</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">Recent Feedback</span></div>' +
        '<div class="card-body" style="padding:0">' + renderRecentFeedback(profile.recent_feedback) + '</div>' +
      '</div>';
  }

  async function loadProfile(agentId) {
    var content = document.getElementById('agent-content');
    var profile = await fetchProfile(agentId);

    if (!profile) {
      content.innerHTML = '<div class="back-link" id="back-to-leaderboard">← Back to Leaderboard</div>' +
        '<div class="card"><div class="card-body lb-empty">Agent not found.</div></div>';
      document.getElementById('back-to-leaderboard').addEventListener('click', function() {
        window.location = '/agent.html';
      });
      return;
    }

    var earnings = await fetchEarnings(agentId);
    content.innerHTML = renderProfile(profile, earnings);

    document.getElementById('back-to-leaderboard').addEventListener('click', function() {
      window.location = '/agent.html';
    });
    content.querySelectorAll('.list-row[data-task-id]').forEach(function(row) {
      row.addEventListener('click', function() {
        window.location = '/task.html?task_id=' + encodeURIComponent(row.getAttribute('data-task-id'));
      });
    });
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
    var agentId = params.get('agent_id');
    if (agentId) {
      await loadProfile(agentId);
    } else {
      await loadLeaderboard();
    }

    ATE.startMetricsPolling(function() {
      buildTicker();
    }, 10000);
  }

  init();
})();
