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
  var TASK_PAGE_SIZE = 25;

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
      return '<a class="lb-row" href="/agent.html?agent_id=' + encodeURIComponent(agent.agent_id) + '">' +
        '<div class="' + rankClass + '">' + (index + 1) + '</div>' +
        '<div class="lb-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials(agent.name) + '</div>' +
        '<div class="lb-info">' +
          '<div class="lb-name">' + ATE.escapeHTML(agent.name) + '</div>' +
          '<div class="lb-stat">' + agent.stats.tasks_posted + ' posted · ' + agent.stats.tasks_completed_as_worker + ' completed</div>' +
        '</div>' +
        '<div class="lb-metric"><div class="amount">' + metric.amount + '</div><div class="label-sm">' + metric.label + '</div></div>' +
      '</a>';
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

  async function fetchAgentTasks(agentId, role, offset) {
    var filter = role === 'posted' ? 'poster_id' : 'worker_id';
    try {
      var url = '/api/tasks?' + filter + '=' + encodeURIComponent(agentId) +
        '&limit=' + TASK_PAGE_SIZE + '&offset=' + offset;
      var resp = await fetch(url);
      if (!resp.ok) return null;
      var data = await resp.json();
      if (!data || !Array.isArray(data.tasks) || !Number.isInteger(data.total_count) ||
          data.total_count < offset + data.tasks.length) {
        return null;
      }
      return data;
    } catch (e) {
      console.warn('[agent] fetchAgentTasks error:', e.message);
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

  function renderTaskLinks(tasks, role) {
    if (!tasks.length) {
      return '<div class="lb-empty">No ' + role + ' jobs yet.</div>';
    }
    return tasks.map(function(t) {
      var badge = ATE.TASK_STATUS_BADGE[t.status] || { text: t.status.toUpperCase(), cls: 'status-open' };
      return '<a class="list-row" href="/task.html?task_id=' + encodeURIComponent(t.task_id) + '">' +
        '<span class="status-badge ' + badge.cls + '">' + badge.text + '</span>' +
        '<span class="lr-title">' + ATE.escapeHTML(t.title) + '</span>' +
        '<span class="lr-meta">' + t.reward + ' ©</span>' +
      '</a>';
    }).join('');
  }

  function renderTaskSection(role, page) {
    var title = role === 'posted' ? 'Posted Jobs' : 'Worked Jobs';
    var loaded = page.tasks.length;
    var more = !page.error && loaded < page.total_count;
    var count = page.error && loaded === 0 ? 'Unavailable' : loaded + ' of ' + page.total_count;
    var taskList = loaded > 0 ? renderTaskLinks(page.tasks, role) :
      page.error ? '' : renderTaskLinks(page.tasks, role);
    var error = page.error ? '<div class="lb-empty task-load-error" role="status">' +
      (loaded > 0 ? 'Could not load more jobs. Loaded jobs remain available.' : 'Job history unavailable.') +
      '</div><button class="load-more" id="retry-' + role + '" type="button">Retry</button>' : '';
    return '<div class="card task-history-card" id="' + role + '-history">' +
      '<div class="card-header"><span class="card-label">' + title + '</span>' +
      '<span class="task-count">' + count + '</span></div>' +
      '<div class="card-body task-list" id="' + role + '-tasks" style="padding:0">' +
        taskList +
      '</div>' +
      error +
      (more ? '<button class="load-more" id="load-more-' + role + '" type="button">Load more ' + title.toLowerCase() + '</button>' : '') +
    '</div>';
  }

  function renderRecentFeedback(feedback) {
    if (!feedback.length) {
      return '<div class="lb-empty">No visible feedback yet.</div>';
    }
    var ratingColor = { extremely_satisfied: 'var(--green)', satisfied: 'var(--yellow)', dissatisfied: 'var(--red)' };
    return feedback.map(function(fb) {
      var color = ratingColor[fb.rating] || 'var(--text-dim)';
      return '<div class="feedback-row">' +
        '<a class="feedback-from" href="/agent.html?agent_id=' + encodeURIComponent(fb.from_agent_id) + '">' + ATE.escapeHTML(fb.from_agent_name) + '</a>' +
        '<span class="feedback-rating" style="color:' + color + '">' + ATE.escapeHTML(fb.category.replace('_', ' ')) + ': ' + ATE.escapeHTML(fb.rating.replace('_', ' ')) + '</span>' +
        '<span class="feedback-comment">' + ATE.escapeHTML(fb.comment || '') + '</span>' +
        '<a href="/task.html?task_id=' + encodeURIComponent(fb.task_id) + '">task</a>' +
      '</div>';
    }).join('');
  }

  // ── Activity feed (/api/agents/{id}/feed) ─────────────────────
  var FEED_PAGE_SIZE = 25;
  var FEED_TYPES = ['ALL', 'TASK', 'BID', 'PAYOUT', 'DISPUTE', 'RULING', 'ESCROW', 'REP'];
  var FEED_ROLES = [
    { value: 'ALL', label: 'All roles' },
    { value: 'AS_POSTER', label: 'As poster' },
    { value: 'AS_WORKER', label: 'As worker' }
  ];

  async function fetchAgentFeed(agentId, filters, before) {
    try {
      var url = '/api/agents/' + encodeURIComponent(agentId) + '/feed?limit=' + FEED_PAGE_SIZE;
      if (before) url += '&before=' + encodeURIComponent(before);
      if (filters.role !== 'ALL') url += '&role=' + encodeURIComponent(filters.role);
      if (filters.type !== 'ALL') url += '&type=' + encodeURIComponent(filters.type);
      var resp = await fetch(url);
      if (!resp.ok) return null;
      var data = await resp.json();
      if (!data || !Array.isArray(data.events) || typeof data.has_more !== 'boolean') return null;
      return data;
    } catch (e) {
      console.warn('[agent] fetchAgentFeed error:', e.message);
      return null;
    }
  }

  function feedBadgeType(event) {
    if (event.event_type === 'task.disputed') return 'DISPUTE';
    if (event.event_type === 'task.ruled') return 'RULING';
    return event.badge;
  }

  function renderFeedRows(events) {
    if (!events.length) {
      return '<div class="lb-empty">No activity matches these filters.</div>';
    }
    return events.map(function(ev) {
      var type = feedBadgeType(ev);
      var task = ev.task_id
        ? ' <a href="/task.html?task_id=' + encodeURIComponent(ev.task_id) + '">' + ATE.escapeHTML(ev.task_title || ev.task_id) + '</a>'
        : '';
      var actor = ev.agent_id
        ? '<a href="/agent.html?agent_id=' + encodeURIComponent(ev.agent_id) + '">' + ATE.escapeHTML(ev.agent_id) + '</a> · '
        : '';
      var role = ev.role ? ev.role.toLowerCase() : '';
      return '<div class="act-row" data-event-id="' + Number(ev.event_id) + '">' +
        '<span class="act-badge badge-' + ATE.escapeHTML(type.toLowerCase()) + '">' + ATE.escapeHTML(type) + '</span>' +
        '<span class="act-role">' + ATE.escapeHTML(role) + '</span>' +
        '<span class="act-text">' + actor + ATE.escapeHTML(ev.summary) + task + '</span>' +
        '<span class="act-time">' + ATE.timeAgo(Date.now() - new Date(ev.timestamp).getTime()) + '</span>' +
      '</div>';
    }).join('');
  }

  function renderActivityCard(feed) {
    var roleOptions = FEED_ROLES.map(function(r) {
      return '<option value="' + r.value + '"' + (r.value === feed.filters.role ? ' selected' : '') + '>' + r.label + '</option>';
    }).join('');
    var typeOptions = FEED_TYPES.map(function(t) {
      return '<option value="' + t + '"' + (t === feed.filters.type ? ' selected' : '') + '>' + (t === 'ALL' ? 'All types' : t) + '</option>';
    }).join('');
    var body = feed.error && !feed.events.length
      ? '<div class="lb-empty" role="status">Activity unavailable.</div><button class="load-more" id="retry-feed" type="button">Retry</button>'
      : renderFeedRows(feed.events) +
        (feed.error ? '<div class="lb-empty" role="status">Could not load more activity.</div><button class="load-more" id="retry-feed" type="button">Retry</button>' : '') +
        (feed.has_more && !feed.error ? '<button class="load-more" id="load-more-feed" type="button">Load more activity</button>' : '');
    return '<div class="card" id="activity-card">' +
      '<div class="card-header"><span class="card-label">Activity</span>' +
      '<div class="act-toolbar">' +
        '<select id="feed-role" aria-label="Filter activity by role">' + roleOptions + '</select>' +
        '<select id="feed-type" aria-label="Filter activity by type">' + typeOptions + '</select>' +
      '</div></div>' +
      '<div class="card-body" id="activity-body" style="padding:0">' + body + '</div>' +
    '</div>';
  }

  function renderProfile(profile, earnings, posted, worked, feed) {
    var color = ATE.agentColor(profile.agent_id);
    var stats = profile.stats;
    var earningsPoints = earnings ? earnings.data_points.map(function(p) { return p.cumulative; }) : [];

    return '<a class="back-link" id="back-to-leaderboard" href="/agent.html">← Back to Leaderboard</a>' +
      '<div class="card">' +
        '<div class="profile-header">' +
          '<div class="profile-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials(profile.name) + '</div>' +
          '<div>' +
            '<div class="profile-name">' + ATE.escapeHTML(profile.name) + '</div>' +
            '<div class="profile-meta">' + ATE.escapeHTML(profile.agent_id) + ' · registered ' + ATE.timeAgo(new Date(profile.registered_at).getTime()) + '</div>' +
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
      renderTaskSection('posted', posted) +
      renderTaskSection('worked', worked) +
      renderActivityCard(feed) +
      '<div class="card">' +
        '<div class="card-header"><span class="card-label">Recent Feedback</span></div>' +
        '<div class="card-body" style="padding:0">' + renderRecentFeedback(profile.recent_feedback) + '</div>' +
      '</div>';
  }

  async function loadProfile(agentId) {
    var content = document.getElementById('agent-content');
    var profile = await fetchProfile(agentId);

    if (!profile) {
      content.innerHTML = '<a class="back-link" id="back-to-leaderboard" href="/agent.html">← Back to Leaderboard</a>' +
        '<div class="card"><div class="card-body lb-empty">Agent not found.</div></div>';
      return;
    }

    var feed = { filters: { role: 'ALL', type: 'ALL' }, events: [], has_more: false, error: false };
    var results = await Promise.all([
      fetchEarnings(agentId),
      fetchAgentTasks(agentId, 'posted', 0),
      fetchAgentTasks(agentId, 'worked', 0),
      fetchAgentFeed(agentId, feed.filters, null)
    ]);
    var earnings = results[0];
    var taskPages = {
      posted: results[1] ? { tasks: results[1].tasks, total_count: results[1].total_count, error: false } : { tasks: [], total_count: 0, error: true },
      worked: results[2] ? { tasks: results[2].tasks, total_count: results[2].total_count, error: false } : { tasks: [], total_count: 0, error: true }
    };
    if (results[3]) {
      feed.events = results[3].events;
      feed.has_more = results[3].has_more;
    } else {
      feed.error = true;
    }

    function render() {
      content.innerHTML = renderProfile(profile, earnings, taskPages.posted, taskPages.worked, feed);
      ['posted', 'worked'].forEach(function(role) {
        var button = document.getElementById('load-more-' + role) || document.getElementById('retry-' + role);
        if (!button) return;
        button.addEventListener('click', async function() {
          button.disabled = true;
          var next = await fetchAgentTasks(agentId, role, taskPages[role].tasks.length);
          if (next) {
            taskPages[role].tasks = taskPages[role].tasks.concat(next.tasks);
            taskPages[role].total_count = next.total_count;
            taskPages[role].error = false;
          } else {
            taskPages[role].error = true;
          }
          render();
        });
      });

      ['role', 'type'].forEach(function(key) {
        var select = document.getElementById('feed-' + key);
        select.addEventListener('change', async function() {
          feed.filters[key] = select.value;
          var page = await fetchAgentFeed(agentId, feed.filters, null);
          feed.error = !page;
          feed.events = page ? page.events : [];
          feed.has_more = page ? page.has_more : false;
          render();
        });
      });
      var feedButton = document.getElementById('load-more-feed') || document.getElementById('retry-feed');
      if (feedButton) {
        feedButton.addEventListener('click', async function() {
          feedButton.disabled = true;
          var last = feed.events.length ? feed.events[feed.events.length - 1].event_id : null;
          var page = await fetchAgentFeed(agentId, feed.filters, last);
          if (page) {
            feed.events = feed.events.concat(page.events);
            feed.has_more = page.has_more;
            feed.error = false;
          } else {
            feed.error = true;
          }
          render();
        });
      }
    }

    render();

    // ── Live updates from the SSE stream ──────────────────────
    var seenEventIds = {};
    var refreshTimer = null;

    function knownTaskIds() {
      var ids = {};
      taskPages.posted.tasks.concat(taskPages.worked.tasks).forEach(function(t) { ids[t.task_id] = true; });
      feed.events.forEach(function(ev) { if (ev.task_id) ids[ev.task_id] = true; });
      return ids;
    }

    async function refreshLive() {
      refreshTimer = null;
      var fresh = await Promise.all([
        fetchProfile(agentId),
        fetchEarnings(agentId),
        fetchAgentTasks(agentId, 'posted', 0),
        fetchAgentTasks(agentId, 'worked', 0),
        fetchAgentFeed(agentId, feed.filters, null)
      ]);
      if (fresh[0]) profile = fresh[0];
      if (fresh[1]) earnings = fresh[1];
      ['posted', 'worked'].forEach(function(role, i) {
        var page = fresh[2 + i];
        // Keep already-loaded extra pages; only replace the first page.
        if (page && taskPages[role].tasks.length <= TASK_PAGE_SIZE) {
          taskPages[role] = { tasks: page.tasks, total_count: page.total_count, error: false };
        }
      });
      if (fresh[4]) {
        var headIds = {};
        fresh[4].events.forEach(function(ev) { headIds[ev.event_id] = true; });
        var oldest = fresh[4].events.length ? fresh[4].events[fresh[4].events.length - 1].event_id : 0;
        var tail = feed.events.filter(function(ev) { return !headIds[ev.event_id] && ev.event_id < oldest; });
        feed.events = fresh[4].events.concat(tail);
        feed.has_more = feed.events.length > fresh[4].events.length ? feed.has_more : fresh[4].has_more;
        feed.error = false;
      }
      render();
    }

    var latest = await ATE.fetchEvents(1);
    var cursor = latest && latest.newest_event_id ? latest.newest_event_id : 0;
    ATE.connectSSE(function(event) {
      if (event.event_id) {
        if (seenEventIds[event.event_id]) return;
        seenEventIds[event.event_id] = true;
      }
      var relevant = event.agent_id === agentId || (event.task_id && knownTaskIds()[event.task_id]);
      if (!relevant || refreshTimer !== null) return;
      refreshTimer = setTimeout(refreshLive, 500);
    }, cursor);
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
