(function() {
  'use strict';
  var ATE = window.ATE;

  // ── State ──────────────────────────────────────────────────
  var state = {
    mode: 'create',       // 'create' | 'view'
    taskId: null,
    task: null,            // Full drilldown from GET /api/tasks/{task_id}
    myAgentId: null,
    currentPhase: 0,
    maxPhase: 0,
    sseSource: null
  };

  // ── Phase mapping ──────────────────────────────────────────
  var PHASE_LABELS = ['Post', 'Bid', 'Contract', 'Deliver', 'Review', 'Ruling', 'Settle'];

  function taskStatusToPhase(task) {
    if (!task) return 0;
    var s = task.status;
    var bidCount = task.bids ? task.bids.length : 0;
    var assetCount = task.assets ? task.assets.length : 0;

    if (s === 'open' && bidCount === 0) return 0;
    if (s === 'open' && bidCount > 0) return 1;
    if (s === 'accepted' && assetCount === 0) return 2;
    if (s === 'accepted' && assetCount > 0) return 3;
    if (s === 'submitted') return 4;
    if (s === 'disputed') return 4;
    if (s === 'ruled') return task.feedback && task.feedback.length > 0 ? 6 : 5;
    if (s === 'approved') return 6;
    return 0;
  }

  function statusBadge(task) {
    if (!task) return { text: 'DRAFT', cls: 'status-open' };
    var map = {
      'open': { text: 'OPEN', cls: 'status-open' },
      'accepted': { text: 'ACTIVE', cls: 'status-active' },
      'submitted': { text: 'SUBMITTED', cls: 'status-submitted' },
      'disputed': { text: 'DISPUTED', cls: 'status-disputed' },
      'ruled': { text: 'RULED', cls: 'status-ruled' },
      'approved': { text: 'APPROVED', cls: 'status-approved' },
      'cancelled': { text: 'CANCELLED', cls: 'status-open' },
      'expired': { text: 'EXPIRED', cls: 'status-open' }
    };
    return map[task.status] || { text: task.status.toUpperCase(), cls: 'status-open' };
  }

  // ── Escrow bar ─────────────────────────────────────────────
  function escrowBar(task, settled) {
    if (!task) return '';
    if (settled) {
      return '<div class="escrow-bar" style="background:var(--green-dim)">' +
        '<span class="escrow-icon">&#10003;</span>' +
        '<span class="escrow-label">Escrow</span>' +
        '<span class="escrow-amount" style="color:var(--green)">' + task.reward + ' &copy; RELEASED</span>' +
        '<span class="escrow-status">SETTLED</span>' +
        '</div>';
    }
    var frozen = task.status === 'disputed';
    var icon = frozen ? '&#9878;&#65039;' : '&#128274;';
    var statusText = frozen ? 'FROZEN &middot; DISPUTE ACTIVE' : 'LOCKED';
    var statusStyle = frozen ? ' style="color:var(--red)"' : '';
    return '<div class="escrow-bar">' +
      '<span class="escrow-icon">' + icon + '</span>' +
      '<span class="escrow-label">Escrow</span>' +
      '<span class="escrow-amount">' + task.reward + ' &copy;</span>' +
      '<span class="escrow-status"' + statusStyle + '>' + statusText + '</span>' +
      '</div>';
  }

  // ── Phase renderers ────────────────────────────────────────
  function renderCreateForm() {
    return '<div class="card">' +
      '<div class="card-header"><span class="card-label">Create New Task</span></div>' +
      '<div class="card-body">' +
      '<div class="form-group">' +
        '<label class="form-label">Title</label>' +
        '<input class="form-input" id="f-title" type="text" placeholder="Task title" maxlength="200">' +
      '</div>' +
      '<div class="form-group">' +
        '<label class="form-label">Specification</label>' +
        '<textarea class="form-textarea" id="f-spec" placeholder="Detailed task specification..." maxlength="10000"></textarea>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="form-group">' +
          '<label class="form-label">Reward (&copy;)</label>' +
          '<input class="form-input" id="f-reward" type="number" min="1" placeholder="100">' +
        '</div>' +
        '<div class="form-group">' +
          '<label class="form-label">Bidding Deadline (s)</label>' +
          '<input class="form-input" id="f-bid-dl" type="number" min="1" placeholder="120">' +
        '</div>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="form-group">' +
          '<label class="form-label">Execution Deadline (s)</label>' +
          '<input class="form-input" id="f-exec-dl" type="number" min="1" placeholder="300">' +
        '</div>' +
        '<div class="form-group">' +
          '<label class="form-label">Review Deadline (s)</label>' +
          '<input class="form-input" id="f-rev-dl" type="number" min="1" placeholder="120">' +
        '</div>' +
      '</div>' +
      '<div style="margin-top:14px">' +
        '<button class="btn btn-cyan solid" id="btn-post-task">Post Task</button>' +
        '<span id="post-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
      '</div>' +
      '</div></div>';
  }

  function renderPostPhase(task) {
    return escrowBar(task) +
      '<div class="card">' +
      '<div class="card-header">' +
        '<span class="card-label">Task ' + task.task_id.slice(0, 12) + '</span>' +
        '<span style="font-size:9px;color:var(--text-dim)">Posted by ' + task.poster.name + '</span>' +
      '</div>' +
      '<div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Title</span><span class="task-detail-val">' + esc(task.title) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Reward</span><span class="task-detail-val" style="color:var(--green)">' + task.reward + ' &copy;</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Bidding Deadline</span><span class="task-detail-val">' + esc(task.deadlines.bidding_deadline) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Specification</span><span class="task-detail-val" style="font-size:10px;text-align:left;max-width:400px;color:var(--text-mid)">' + esc(task.spec) + '</span></div>' +
      '</div></div>' +
      '<div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">&#9203; WAITING FOR BIDS...</div>';
  }

  function renderBidPhase(task) {
    var bids = task.bids || [];
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = escrowBar(task) +
      '<div class="card"><div class="card-header">' +
      '<span class="card-label">Bids on ' + task.task_id.slice(0, 12) + '</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + bids.length + ' bid' + (bids.length !== 1 ? 's' : '') + ' received</span>' +
      '</div><div class="card-body no-pad">';
    bids.forEach(function(b) {
      var color = ATE.agentColor(b.bidder.agent_id);
      var initials = b.bidder.name.slice(0, 2).toUpperCase();
      var dq = b.bidder.delivery_quality;
      var total = (dq.extremely_satisfied || 0) + (dq.satisfied || 0) + (dq.dissatisfied || 0);
      var qualPct = total > 0 ? Math.round(((dq.extremely_satisfied || 0) + (dq.satisfied || 0)) / total * 100) : 0;
      html += '<div class="bid-row" style="animation:slide-in .3s ease-out">' +
        '<div class="bid-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials + '</div>' +
        '<div class="bid-info"><div class="bid-name">' + esc(b.bidder.name) + '</div>' +
        '<div class="bid-meta">Quality: ' + qualPct + '% &middot; ' + esc(b.proposal.slice(0, 60)) + '</div></div>' +
        '<div class="bid-amount" style="color:var(--green)">' + task.reward + ' &copy;</div>' +
        '<div class="bid-actions">';
      if (isMyTask && !b.accepted && task.status === 'open') {
        html += '<button class="btn btn-green btn-accept-bid" data-bid-id="' + b.bid_id + '">Accept</button>';
      }
      html += '</div></div>';
    });
    html += '</div></div>';
    return html;
  }

  function renderContractPhase(task) {
    var worker = task.worker || { name: 'Unknown', agent_id: '' };
    var workerColor = ATE.agentColor(worker.agent_id);
    return escrowBar(task) +
      '<div class="card" style="border-color:var(--green)">' +
      '<div class="card-header" style="background:var(--green-dim)">' +
        '<span class="card-label" style="color:var(--green)">&#129309; Contract Signed</span>' +
        '<span style="font-size:9px;color:var(--green)">Platform co-signed</span>' +
      '</div><div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Task</span><span class="task-detail-val">' + task.task_id.slice(0, 12) + ' &mdash; ' + esc(task.title) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Poster</span><span class="task-detail-val" style="color:var(--cyan)">' + esc(task.poster.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Worker</span><span class="task-detail-val" style="color:' + workerColor + '">' + esc(worker.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Reward</span><span class="task-detail-val" style="color:var(--green)">' + task.reward + ' &copy;</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Execution Deadline</span><span class="task-detail-val">' + esc(task.deadlines.execution_deadline || 'N/A') + '</span></div>' +
      '</div></div>';
  }

  function renderDeliverPhase(task) {
    var assets = task.assets || [];
    var html = escrowBar(task) +
      '<div class="card"><div class="card-header">' +
      '<span class="card-label">&#128230; Assets Delivered</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + assets.length + ' file' + (assets.length !== 1 ? 's' : '') + '</span>' +
      '</div><div class="card-body">';
    if (assets.length === 0) {
      html += '<div style="font-size:10px;color:var(--text-dim);text-align:center">Worker is executing... no assets yet.</div>';
    }
    assets.forEach(function(a) {
      html += '<div class="task-detail-row"><span class="task-detail-key">' + esc(a.filename) + '</span><span class="task-detail-val">' + (a.size_bytes / 1024).toFixed(1) + ' KB</span></div>';
    });
    html += '</div></div>';
    return html;
  }

  function renderReviewPhase(task) {
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = escrowBar(task);

    // Show assets/deliverable
    var assets = task.assets || [];
    if (assets.length > 0) {
      html += '<div class="card"><div class="card-header"><span class="card-label">&#128230; Deliverable</span></div><div class="card-body">';
      assets.forEach(function(a) {
        html += '<div class="task-detail-row"><span class="task-detail-key">' + esc(a.filename) + '</span><span class="task-detail-val">' + (a.size_bytes / 1024).toFixed(1) + ' KB</span></div>';
      });
      html += '</div></div>';
    }

    // Action buttons (only for poster, only if submitted)
    if (isMyTask && task.status === 'submitted') {
      html += '<div style="padding:12px 16px;display:flex;gap:10px">' +
        '<button class="btn btn-green" id="btn-approve">&#10003; Approve &mdash; Release Payout</button>' +
        '<button class="btn btn-red" id="btn-dispute-show">&#10007; Dispute</button>' +
        '</div>';
    }

    // Dispute panel (if disputed)
    if (task.dispute) {
      html += '<div class="dispute-panel">' +
        '<div class="dispute-label" style="color:var(--red)">&#128680; Dispute &mdash; Filed by ' + esc(task.poster.name) + '</div>' +
        '<div class="dispute-text">' + esc(task.dispute.reason) + '</div></div>';

      if (task.dispute.rebuttal) {
        var workerName = task.worker ? task.worker.name : 'Worker';
        html += '<div class="rebuttal-panel">' +
          '<div class="dispute-label" style="color:var(--amber)">&#128737; Rebuttal &mdash; Filed by ' + esc(workerName) + '</div>' +
          '<div class="dispute-text">' + esc(task.dispute.rebuttal.content) + '</div></div>';
      }
    }

    // Dispute form
    if (isMyTask && task.status === 'submitted') {
      html += '<div id="dispute-form" style="display:none;padding:12px 16px">' +
        '<div class="form-group"><label class="form-label">Dispute Reason</label>' +
        '<textarea class="form-textarea" id="f-dispute-reason" placeholder="Explain why the deliverable is unsatisfactory..."></textarea></div>' +
        '<button class="btn btn-red" id="btn-submit-dispute">Submit Dispute</button>' +
        '<span id="dispute-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
        '</div>';
    }

    return html;
  }

  function renderRulingPhase(task) {
    if (!task.dispute || !task.dispute.ruling) {
      return escrowBar(task) + '<div style="padding:20px;text-align:center;color:var(--text-dim)">Awaiting court ruling...</div>';
    }
    var ruling = task.dispute.ruling;
    return escrowBar(task) +
      '<div class="ruling-card" style="animation:scale-in .5s ease-out">' +
      '<div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span>' +
        '<span class="ruling-title">Court Ruling &mdash; ' + task.task_id.slice(0, 12) + '</span>' +
      '</div><div class="ruling-body">' +
        '<div class="ruling-scores">' +
          '<div class="ruling-score"><div class="ruling-score-label">Worker Payout</div>' +
          '<div class="ruling-score-value" style="color:var(--green)">' + ruling.worker_pct + '%</div></div>' +
          '<div class="ruling-score"><div class="ruling-score-label">Poster Refund</div>' +
          '<div class="ruling-score-value" style="color:var(--amber)">' + (100 - ruling.worker_pct) + '%</div></div>' +
        '</div>' +
        '<div style="margin-bottom:8px"><span class="card-label">Ruling Summary</span></div>' +
        '<div class="ruling-reasoning">' + esc(ruling.summary) + '</div>' +
      '</div></div>';
  }

  function renderSettlePhase(task) {
    var html = escrowBar(task, true);

    // If there was a ruling, show payout breakdown
    if (task.dispute && task.dispute.ruling) {
      var ruling = task.dispute.ruling;
      var workerPayout = Math.round(task.reward * ruling.worker_pct / 100);
      var posterPayout = task.reward - workerPayout;
      html += '<div class="ruling-card"><div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span><span class="ruling-title">Settlement</span>' +
        '</div><div class="ruling-body"><div class="ruling-payout">' +
        '<div class="payout-box"><div class="payout-agent">' + esc(task.poster.name) + ' (Poster)</div><div class="payout-amount" style="color:var(--green)">' + posterPayout + ' &copy;</div></div>' +
        '<div class="payout-box"><div class="payout-agent">' + esc((task.worker || {}).name || 'Worker') + ' (Worker)</div><div class="payout-amount" style="color:var(--amber)">' + workerPayout + ' &copy;</div></div>' +
        '</div></div></div>';
    }

    // Feedback
    var feedback = task.feedback || [];
    if (feedback.length > 0) {
      html += '<div class="card"><div class="card-header"><span class="card-label">&#128202; Feedback</span></div><div class="card-body no-pad">';
      feedback.forEach(function(f) {
        var stars = f.rating === 'extremely_satisfied' ? '&#9733;&#9733;&#9733;' : f.rating === 'satisfied' ? '&#9733;&#9733;&#9734;' : '&#9733;&#9734;&#9734;';
        html += '<div class="feedback-row">' +
          '<span class="feedback-from" style="color:var(--cyan)">' + esc(f.from_agent_name) + ' &rarr;</span>' +
          '<span class="feedback-stars">' + stars + '</span>' +
          '<span class="feedback-text">' + esc(f.comment || '') + '</span></div>';
      });
      html += '</div></div>';
    }

    html += '<div style="padding:20px;text-align:center">' +
      '<span style="font-size:11px;color:var(--green);font-weight:700;letter-spacing:1px">&#10003; TASK LIFECYCLE COMPLETE</span></div>';
    return html;
  }

  var RENDERERS = [renderPostPhase, renderBidPhase, renderContractPhase, renderDeliverPhase, renderReviewPhase, renderRulingPhase, renderSettlePhase];

  // ── Utility ────────────────────────────────────────────────
  function esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
  }

  // ── Phase strip rendering ──────────────────────────────────
  function renderPhaseStrip() {
    var steps = document.querySelectorAll('.phase-step');
    steps.forEach(function(el, i) {
      el.className = 'phase-step';
      if (i < state.maxPhase) {
        el.classList.add('completed');
        el.innerHTML = el.innerHTML.replace(/<span class="phase-check">.*<\/span>/, '');
        el.innerHTML += '<span class="phase-check">&#10003;</span>';
      } else if (i === state.currentPhase) {
        el.classList.add('active');
      } else {
        el.classList.add('pending');
      }
    });
  }

  // ── Main view update ───────────────────────────────────────
  function updateView() {
    var content = document.getElementById('phase-content');
    var panelTitle = document.getElementById('panel-title');
    var statusEl = document.getElementById('task-status');

    if (state.mode === 'create') {
      panelTitle.textContent = '&#128221; Post a New Task';
      statusEl.textContent = 'DRAFT';
      statusEl.className = 'status-badge status-open';
      content.innerHTML = renderCreateForm();
      state.currentPhase = 0;
      state.maxPhase = 0;
      renderPhaseStrip();
      bindCreateForm();
      return;
    }

    if (!state.task) {
      content.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-dim)">Loading task...</div>';
      return;
    }

    var phase = taskStatusToPhase(state.task);
    state.maxPhase = phase;
    if (state.currentPhase > phase) state.currentPhase = phase;

    var badge = statusBadge(state.task);
    panelTitle.textContent = PHASE_LABELS[state.currentPhase];
    statusEl.textContent = badge.text;
    statusEl.className = 'status-badge ' + badge.cls;

    if (state.currentPhase === 0 && phase === 0 && state.task.status === 'open') {
      content.innerHTML = renderPostPhase(state.task);
    } else {
      var renderer = RENDERERS[state.currentPhase];
      content.innerHTML = renderer ? renderer(state.task) : '';
    }

    renderPhaseStrip();
    bindActionButtons();
  }

  // ── Phase navigation ───────────────────────────────────────
  function navigateToPhase(phase) {
    if (phase < 0 || phase > state.maxPhase) return;
    state.currentPhase = phase;
    updateView();
  }

  document.getElementById('phase-strip').addEventListener('click', function(e) {
    var step = e.target.closest('.phase-step');
    if (!step) return;
    var phase = parseInt(step.getAttribute('data-phase'), 10);
    if (isNaN(phase)) return;
    navigateToPhase(phase);
  });

  // ── Form bindings ──────────────────────────────────────────
  function bindCreateForm() {
    var btn = document.getElementById('btn-post-task');
    if (!btn) return;
    btn.addEventListener('click', async function() {
      var title = document.getElementById('f-title').value.trim();
      var spec = document.getElementById('f-spec').value.trim();
      var reward = parseInt(document.getElementById('f-reward').value, 10);
      var bidDl = parseInt(document.getElementById('f-bid-dl').value, 10);
      var execDl = parseInt(document.getElementById('f-exec-dl').value, 10);
      var revDl = parseInt(document.getElementById('f-rev-dl').value, 10);
      var errEl = document.getElementById('post-error');

      if (!title || !spec || !reward || !bidDl || !execDl || !revDl) {
        errEl.textContent = 'All fields are required.';
        return;
      }

      btn.disabled = true;
      errEl.textContent = '';

      try {
        var resp = await fetch('/api/proxy/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: title,
            spec: spec,
            reward: reward,
            bidding_deadline_seconds: bidDl,
            execution_deadline_seconds: execDl,
            review_deadline_seconds: revDl
          })
        });
        if (!resp.ok) {
          var err = await resp.json().catch(function() { return {}; });
          throw new Error(err.message || 'Failed to create task (HTTP ' + resp.status + ')');
        }
        var data = await resp.json();
        state.taskId = data.task_id;
        state.mode = 'view';

        // Update URL without reload
        var url = new URL(window.location);
        url.searchParams.set('task_id', state.taskId);
        window.history.pushState({}, '', url);

        await refreshTaskData(state.taskId);
        connectTaskSSE(state.taskId);
      } catch (e) {
        errEl.textContent = e.message;
        btn.disabled = false;
      }
    });
  }

  // ── Action button bindings ─────────────────────────────────
  function bindActionButtons() {
    // Accept bid buttons
    document.querySelectorAll('.btn-accept-bid').forEach(function(btn) {
      btn.addEventListener('click', async function() {
        var bidId = this.getAttribute('data-bid-id');
        this.disabled = true;
        try {
          await fetch('/api/proxy/tasks/' + state.taskId + '/bids/' + bidId + '/accept', { method: 'POST' });
          await refreshTaskData(state.taskId);
        } catch (e) {
          console.error('Accept bid failed:', e);
          this.disabled = false;
        }
      });
    });

    // Approve button
    var approveBtn = document.getElementById('btn-approve');
    if (approveBtn) {
      approveBtn.addEventListener('click', async function() {
        this.disabled = true;
        try {
          await fetch('/api/proxy/tasks/' + state.taskId + '/approve', { method: 'POST' });
          await refreshTaskData(state.taskId);
        } catch (e) {
          console.error('Approve failed:', e);
          this.disabled = false;
        }
      });
    }

    // Show dispute form
    var disputeShowBtn = document.getElementById('btn-dispute-show');
    if (disputeShowBtn) {
      disputeShowBtn.addEventListener('click', function() {
        var form = document.getElementById('dispute-form');
        if (form) form.style.display = 'block';
      });
    }

    // Submit dispute
    var submitDisputeBtn = document.getElementById('btn-submit-dispute');
    if (submitDisputeBtn) {
      submitDisputeBtn.addEventListener('click', async function() {
        var reason = document.getElementById('f-dispute-reason').value.trim();
        var errEl = document.getElementById('dispute-error');
        if (!reason) { errEl.textContent = 'Reason is required.'; return; }
        this.disabled = true;
        errEl.textContent = '';
        try {
          var resp = await fetch('/api/proxy/tasks/' + state.taskId + '/dispute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: reason })
          });
          if (!resp.ok) {
            var err = await resp.json().catch(function() { return {}; });
            throw new Error(err.message || 'Failed to file dispute');
          }
          await refreshTaskData(state.taskId);
        } catch (e) {
          errEl.textContent = e.message;
          this.disabled = false;
        }
      });
    }
  }

  // ── Data fetching ──────────────────────────────────────────
  async function refreshTaskData(taskId) {
    try {
      var resp = await fetch('/api/tasks/' + taskId);
      if (resp.ok) {
        state.task = await resp.json();
        var newPhase = taskStatusToPhase(state.task);
        state.currentPhase = newPhase;
        updateView();
      }
    } catch (e) {
      console.warn('[task] refreshTaskData error:', e.message);
    }
  }

  // ── SSE ────────────────────────────────────────────────────
  function connectTaskSSE(taskId) {
    if (state.sseSource) {
      state.sseSource.close();
    }
    state.sseSource = ATE.connectSSE(function(event) {
      if (event.task_id === taskId) {
        refreshTaskData(taskId);
        addRealFeedEvent(event);
      }
    });
  }

  // ── Event feed ─────────────────────────────────────────────
  function addRealFeedEvent(event) {
    var feed = ATE.mapEventToFeed(event);
    var scroll = document.getElementById('feed-scroll');
    if (!scroll) return;
    var timeStr = new Date(event.timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    var div = document.createElement('div');
    div.className = 'feed-item';
    div.innerHTML = '<span class="feed-badge ' + feed.badge + '">' + feed.type + '</span>' +
      '<span class="feed-text">' + feed.text + '</span>' +
      '<span class="feed-time">' + timeStr + '</span>';
    scroll.insertBefore(div, scroll.firstChild);
  }

  async function loadHistoricalEvents(taskId) {
    try {
      var data = await ATE.fetchEvents(50);
      var events = (data.events || []).filter(function(e) { return e.task_id === taskId; });
      events.reverse(); // oldest first so newest ends up on top
      events.forEach(addRealFeedEvent);
    } catch (e) {
      console.warn('[task] loadHistoricalEvents error:', e.message);
    }
  }

  // ── Bottom ticker ──────────────────────────────────────────
  function buildTicker() {
    if (ATE && typeof ATE.buildBottomTicker === 'function') {
      ATE.buildBottomTicker(document.getElementById('ticker-track'));
    }
  }

  // ── Boot ───────────────────────────────────────────────────
  async function init() {
    // Fetch metrics for ticker
    await ATE.fetchMetrics();
    await ATE.fetchAgents();
    buildTicker();

    // Get our identity
    try {
      var idResp = await fetch('/api/proxy/identity');
      if (idResp.ok) {
        var idData = await idResp.json();
        state.myAgentId = idData.agent_id;
      }
    } catch (e) {
      console.warn('[task] Could not fetch proxy identity:', e.message);
    }

    // Check URL for task_id
    var params = new URLSearchParams(window.location.search);
    var taskId = params.get('task_id');
    if (taskId) {
      state.taskId = taskId;
      state.mode = 'view';
      await refreshTaskData(taskId);
      await loadHistoricalEvents(taskId);
      connectTaskSSE(taskId);
    } else {
      state.mode = 'create';
      updateView();
    }

    // Start metrics polling for ticker updates
    ATE.startMetricsPolling(function() {
      buildTicker();
    }, 10000);
  }

  init();
})();
