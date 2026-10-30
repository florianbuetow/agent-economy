(function() {
  'use strict';
  var ATE = window.ATE;

  // ── State ──────────────────────────────────────────────────
  var state = {
    mode: 'create',       // 'create' | 'list' | 'view'
    taskId: null,
    task: null,            // Full drilldown from GET /api/tasks/{task_id}
    myAgentId: null,
    bidLoadStatus: 'idle', // 'idle' | 'loading' | 'ready' | 'error'
    bidLoadError: '',
    pendingIntent: null,
    intentMessage: '',
    currentPhase: 0,
    phaseChosen: false,    // true once the user picks a phase; live refresh then keeps it
    maxPhase: 0,
    selectedUpload: null,  // { file, key } chosen by the worker, kept across re-renders
    feedbackSent: {},
    sseSource: null,
    documentsAnchorApplied: false,
    documentsSignature: null,
    inputs: { taskId: null, status: 'idle', manifest: null },
    taskList: [],
    taskListTotal: 0,
    taskListStatus: 'idle',
    taskListError: '',
    balance: null,
    taskFilter: { status: '', scope: 'all' },
    needsAction: { status: 'idle', bids: [], reviews: [], error: '' }
  };
  var TASK_LIST_PAGE_SIZE = 25;

  // ── Phase mapping ──────────────────────────────────────────
  var PHASE_LABELS = ['Post', 'Bid', 'Contract', 'Deliver', 'Review', 'Ruling', 'Settle'];

  function taskStatusToPhase(task) {
    if (!task) return 0;
    var s = task.status;
    var bidCount = Number.isInteger(task.bid_count) ? task.bid_count : (task.bids ? task.bids.length : 0);
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
    return ATE.TASK_STATUS_BADGE[task.status] || { text: task.status.toUpperCase(), cls: 'status-open' };
  }

  // ── Escrow bar ─────────────────────────────────────────────
  function financialEffect(task) {
    var effect = task.financial_effect;
    var fields = ['schema_version', 'settlement_state', 'receipt_state', 'outcome',
      'operation_id', 'receipt_reference', 'business_projection_pending', 'pending_target_status'];
    var operationId = /^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
    if (!effect || typeof effect !== 'object' || Array.isArray(effect) ||
        Object.keys(effect).length !== fields.length ||
        !fields.every(function(key) { return Object.prototype.hasOwnProperty.call(effect, key); }) ||
        effect.schema_version !== 1 || typeof effect.business_projection_pending !== 'boolean' ||
        ['locked', 'pending', 'attention_required', 'conflict', 'complete', 'historical'].indexOf(effect.settlement_state) < 0 ||
        (effect.operation_id !== null && (typeof effect.operation_id !== 'string' || !operationId.test(effect.operation_id))) ||
        (effect.business_projection_pending
          ? ['approved', 'cancelled', 'expired', 'ruled'].indexOf(effect.pending_target_status) < 0
          : effect.pending_target_status !== null)) return null;

    if (effect.receipt_state === 'confirmed') {
      if (effect.operation_id === null || effect.receipt_reference !== effect.operation_id ||
          ['worker_paid', 'poster_refunded', 'split'].indexOf(effect.outcome) < 0 ||
          ['pending', 'complete', 'conflict'].indexOf(effect.settlement_state) < 0) return null;
    } else if (effect.receipt_state === 'historical') {
      if (effect.settlement_state !== 'historical' || effect.outcome !== 'worker_paid' ||
          effect.operation_id !== null || effect.receipt_reference !== null ||
          effect.business_projection_pending) return null;
    } else if (effect.receipt_state === 'absent') {
      if (effect.outcome !== null || effect.receipt_reference !== null ||
          ['locked', 'pending', 'attention_required', 'conflict'].indexOf(effect.settlement_state) < 0 ||
          (effect.settlement_state === 'locked' &&
            (effect.operation_id !== null || effect.business_projection_pending))) return null;
    } else {
      return null;
    }
    return effect;
  }

  function settlementStatusText(task) {
    var effect = financialEffect(task);
    var statusText = 'SETTLEMENT UNCONFIRMED';
    var confirmed = effect && effect.receipt_state === 'confirmed';
    var historical = effect && effect.receipt_state === 'historical';
    if (confirmed) {
      statusText = {worker_paid: 'PAID', poster_refunded: 'REFUNDED', split: 'SPLIT'}[effect.outcome];
      if (effect.business_projection_pending) statusText += ' · TASK UPDATE PENDING';
      else if (effect.settlement_state === 'conflict') statusText += ' · TASK CONFLICT';
    } else if (historical) {
      statusText = 'PAID · HISTORICAL';
    } else if (effect) {
      statusText = {locked: 'LOCKED', pending: 'SETTLEMENT PENDING',
        attention_required: 'SETTLEMENT NEEDS ATTENTION', conflict: 'SETTLEMENT CONFLICT'}[effect.settlement_state];
      if (effect.settlement_state === 'locked' && task.status === 'disputed') {
        statusText = 'FROZEN · DISPUTE ACTIVE';
      }
    }
    return statusText;
  }

  // Plain-language payment line for the overview, derived only from settlement evidence.
  function paymentText(task) {
    var effect = financialEffect(task);
    if (!effect) return 'Payment status unavailable';
    if (effect.receipt_state === 'confirmed') {
      return {worker_paid: 'Paid to the worker', poster_refunded: 'Refunded to the poster',
        split: 'Split between worker and poster by the court ruling'}[effect.outcome];
    }
    if (effect.receipt_state === 'historical') return 'Paid to the worker';
    if (effect.settlement_state === 'locked') {
      return task.status === 'disputed' ? 'Held in escrow until the court rules' : 'Held in escrow';
    }
    return 'Settlement in progress';
  }

  function escrowBar(task) {
    if (!task) return '';
    var effect = financialEffect(task);
    var statusText = settlementStatusText(task);
    var confirmed = effect && effect.receipt_state === 'confirmed';
    var historical = effect && effect.receipt_state === 'historical';
    var amount = Number.isSafeInteger(task.reward) && task.reward > 0 ? String(task.reward) + ' &copy;' : 'Amount unavailable';
    var success = confirmed || historical;
    return '<div class="escrow-bar"' + (success ? ' style="background:var(--green-dim)"' : '') + '>' +
      '<span class="escrow-icon">' + (success ? '&#10003;' : '&#128274;') + '</span>' +
      '<span class="escrow-label">Escrow</span>' +
      '<span class="escrow-amount">' + amount + '</span>' +
      '<span class="escrow-status">' + statusText + '</span>' +
      '</div>';
  }

  // ── Phase renderers ────────────────────────────────────────
  function durationField(id, label, hint) {
    return '<div class="form-group">' +
      '<label class="form-label" for="' + id + '">' + label + ' (seconds)</label>' +
      '<input class="form-input" id="' + id + '" type="number" min="1" step="1" aria-describedby="' + id + '-hint">' +
      '<span class="form-hint" id="' + id + '-hint">' + hint + ' Enter whole seconds: 60 is one minute, 3600 is one hour.</span>' +
      '</div>';
  }

  function balanceText() {
    if (state.balance === null) return 'Your balance: not available. The platform checks it when you post.';
    return 'Your balance: ' + state.balance.toLocaleString() + ' ©';
  }

  function renderCreateForm() {
    return '<div class="card">' +
      '<div class="card-header"><span class="card-label">Create New Task</span></div>' +
      '<div class="card-body">' +
      '<div class="form-group">' +
        '<label class="form-label" for="f-title">Title</label>' +
        '<input class="form-input" id="f-title" type="text" placeholder="Task title" maxlength="200">' +
      '</div>' +
      '<div class="form-group">' +
        '<label class="form-label" for="f-spec">Specification</label>' +
        '<textarea class="form-textarea" id="f-spec" placeholder="Detailed task specification..." maxlength="10000"></textarea>' +
      '</div>' +
      '<div class="form-note"><strong id="f-balance">' + esc(balanceText()) + '</strong><br>' +
        'The reward is locked in escrow when you post. It is paid to the worker when you approve the delivery, ' +
        'and returned to you if nobody completes the task.</div>' +
      '<div class="form-group">' +
        '<label class="form-label" for="f-reward">Reward (&copy;)</label>' +
        '<input class="form-input" id="f-reward" type="number" min="1" step="1">' +
      '</div>' +
      '<div class="form-row">' +
        durationField('f-bid-dl', 'Bidding window', 'How long workers can place bids, counted from posting.') +
        durationField('f-accept-window', 'Acceptance window', 'After bidding closes, how long you have to pick a winning bid.') +
      '</div>' +
      '<div class="form-row">' +
        durationField('f-exec-dl', 'Delivery time', 'After you accept a bid, how long the worker has to deliver.') +
        durationField('f-rev-dl', 'Review time', 'After delivery, how long you have to approve or dispute before it is approved automatically.') +
      '</div>' +
      '<div class="form-group">' +
        '<label class="form-label" for="f-inputs">Input files (optional)</label>' +
        '<input class="form-input" id="f-inputs" type="file" multiple accept=".txt,.md,.json,.csv,.py,.js,.ts,.html,.css,.sql,.sh" aria-describedby="f-inputs-hint">' +
        '<span class="form-hint" id="f-inputs-hint">Files workers need for the job. Bidders can read them before bidding, and they cannot change after posting.</span>' +
      '</div>' +
      '<div style="margin-top:14px">' +
        '<button class="btn btn-cyan solid" id="btn-post-task">Post Task</button>' +
        '<span id="post-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
      '</div>' +
      '</div></div>';
  }

  function postPhaseFooter(task) {
    if (task.status === 'cancelled') {
      return '<div class="task-terminal-note">&#10007; THIS TASK WAS CANCELLED. IT NO LONGER ACCEPTS BIDS.</div>';
    }
    if (task.status === 'expired') {
      return '<div class="task-terminal-note">&#8987; THIS TASK EXPIRED BEFORE A BID WAS ACCEPTED.</div>';
    }
    return '<div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">&#9203; WAITING FOR BIDS...</div>';
  }

  // ── Overview helpers ───────────────────────────────────────
  function countdownText(iso) {
    var ms = Date.parse(iso) - Date.now();
    if (isNaN(ms)) return '';
    var total = Math.floor(Math.abs(ms) / 1000);
    var days = Math.floor(total / 86400);
    var hours = Math.floor((total % 86400) / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = total % 60;
    var span = days > 0 ? days + 'd ' + hours + 'h' : hours > 0 ? hours + 'h ' + minutes + 'm' : minutes + 'm ' + seconds + 's';
    return ms >= 0 ? 'in ' + span : span + ' ago';
  }

  function deadlineValue(iso, notStarted) {
    if (!iso) return '<span style="color:var(--text-dim)">' + esc(notStarted) + '</span>';
    return esc(iso) + ' <span class="countdown" data-deadline="' + esc(iso) + '">' + esc(countdownText(iso)) + '</span>';
  }

  function updateCountdowns() {
    document.querySelectorAll('.countdown[data-deadline]').forEach(function(element) {
      element.textContent = countdownText(element.getAttribute('data-deadline'));
    });
  }

  function detailRow(key, valueHtml) {
    return '<div class="task-detail-row"><span class="task-detail-key">' + key + '</span><span class="task-detail-val">' + valueHtml + '</span></div>';
  }

  function isPoster(task) {
    return Boolean(state.myAgentId) && task.poster.agent_id === state.myAgentId;
  }

  function isWorker(task) {
    return Boolean(state.myAgentId) && Boolean(task.worker) && task.worker.agent_id === state.myAgentId;
  }

  function operatorControls(task) {
    var html = '';
    if (isPoster(task) && task.status === 'open') {
      html += '<div class="operator-action"><span class="operator-action-label">You posted this task</span>' +
        '<button class="btn btn-red" id="btn-cancel-task">Cancel task &mdash; refund the reward</button></div>';
    }
    if (state.myAgentId && !isPoster(task) && task.status === 'open') {
      html += '<div class="operator-action"><label class="operator-action-label" for="f-bid-amount">Your bid (&copy;)</label>' +
        '<input class="form-input" id="f-bid-amount" data-preserve type="number" min="1" step="1" style="max-width:140px">' +
        '<button class="btn btn-green" id="btn-submit-bid">Place binding bid</button>' +
        '<span class="form-hint">A bid cannot be withdrawn. If the poster accepts it, you must deliver before the delivery deadline.</span></div>';
    }
    return html;
  }

  function renderPostPhase(task) {
    var deadlines = task.deadlines || {};
    var price = Number.isSafeInteger(task.accepted_price) ? task.accepted_price + ' &copy;' :
      '<span style="color:var(--text-dim)">No bid accepted yet</span>';
    var worker = task.worker ? agentLink(task.worker.agent_id, task.worker.name) :
      '<span style="color:var(--text-dim)">Not assigned</span>';
    var waiting = task.status === 'open' && !(task.bid_count > 0);
    return '<div class="card">' +
      '<div class="card-header">' +
        '<span class="card-label">Task ' + esc(task.task_id.slice(0, 12)) + '</span>' +
        '<span style="font-size:9px;color:var(--text-dim)">Posted by ' + agentLink(task.poster.agent_id, task.poster.name) + '</span>' +
      '</div>' +
      '<div class="card-body">' +
        detailRow('Title', esc(task.title)) +
        detailRow('Worker', worker) +
        detailRow('Budget', '<span style="color:var(--green)">' + task.reward + ' &copy;</span>') +
        detailRow('Accepted price', price) +
        detailRow('Payment', '<span class="payment-status">' + esc(paymentText(task)) + '</span>') +
        detailRow('Bidding closes', deadlineValue(deadlines.bidding_deadline, 'Not set')) +
        detailRow('Acceptance closes', deadlineValue(deadlines.acceptance_deadline, 'Not set')) +
        detailRow('Delivery due', deadlineValue(deadlines.execution_deadline, 'Starts when a bid is accepted')) +
        detailRow('Review closes', deadlineValue(deadlines.review_deadline, 'Starts when work is submitted')) +
        '<div class="task-detail-row"><span class="task-detail-key">Specification</span><span class="task-detail-val" style="font-size:10px;text-align:left;max-width:400px;color:var(--text-mid);white-space:pre-wrap">' + esc(task.spec) + '</span></div>' +
        operatorControls(task) +
      '</div></div>' +
      (waiting || task.status === 'cancelled' || task.status === 'expired' ? postPhaseFooter(task) : '');
  }

  function renderBidPhase(task) {
    var bids = task.bids || [];
    var bidCount = Number.isInteger(task.bid_count) ? task.bid_count : bids.length;
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = '<div class="card"><div class="card-header">' +
      '<span class="card-label">Bids on ' + esc(task.task_id.slice(0, 12)) + '</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + bidCount + ' bid' + (bidCount !== 1 ? 's' : '') + ' received</span>' +
      '</div><div class="card-body no-pad">';
    if (bids.length === 0 && isMyTask && state.bidLoadStatus === 'loading') {
      html += '<div class="bid-load-status" style="padding:14px;text-align:center;color:var(--text-dim)">Loading authorized bid details...</div>';
    } else if (bids.length === 0 && isMyTask && state.bidLoadStatus === 'error') {
      html += '<div class="bid-load-error" style="padding:14px;text-align:center;color:var(--red)">The bids could not be loaded. Next step: reload the page.</div>';
    } else if (bids.length === 0 && bidCount > 0) {
      html += '<div class="bid-sealed" style="padding:14px;text-align:center;color:var(--text-dim)">Bid details are sealed to the poster.</div>';
    }
    bids.forEach(function(b) {
      var bidder = b.bidder || { agent_id: b.bidder_id, name: b.bidder_id };
      var color = ATE.agentColor(bidder.agent_id);
      var initials = bidder.name.slice(0, 2).toUpperCase();
      var dq = bidder.delivery_quality;
      var total = dq ? (dq.extremely_satisfied || 0) + (dq.satisfied || 0) + (dq.dissatisfied || 0) : 0;
      var qualPct = total > 0 ? Math.round(((dq.extremely_satisfied || 0) + (dq.satisfied || 0)) / total * 100) : 0;
      var bidMeta = dq ? 'Quality: ' + qualPct + '%' : 'Poster-authorized sealed offer';
      html += '<div class="bid-row" style="animation:slide-in .3s ease-out">' +
        '<div class="bid-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + esc(initials) + '</div>' +
        '<div class="bid-info"><div class="bid-name">' + agentLink(bidder.agent_id, bidder.name) + '</div>' +
        '<div class="bid-meta">' + esc(bidMeta) + '</div></div>' +
        '<div class="bid-amount" style="color:var(--green)">' + b.amount + ' &copy;</div>' +
        '<div class="bid-actions">';
      if (isMyTask && !b.accepted && task.status === 'open') {
        html += '<button class="btn btn-green btn-accept-bid" data-bid-id="' + ATE.escapeHTML(b.bid_id) + '">Accept</button>';
      }
      html += '</div></div>';
    });
    html += '</div></div>';
    return html;
  }

  function renderContractPhase(task) {
    var worker = task.worker || { name: 'Unknown', agent_id: '' };
    var workerColor = ATE.agentColor(worker.agent_id);
    return '<div class="card" style="border-color:var(--green)">' +
      '<div class="card-header" style="background:var(--green-dim)">' +
        '<span class="card-label" style="color:var(--green)">&#129309; Contract Signed</span>' +
        '<span style="font-size:9px;color:var(--green)">Platform co-signed</span>' +
      '</div><div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Task</span><span class="task-detail-val">' + esc(task.task_id.slice(0, 12)) + ' &mdash; ' + esc(task.title) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Poster</span><span class="task-detail-val" style="color:var(--cyan)">' + agentLink(task.poster.agent_id, task.poster.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Worker</span><span class="task-detail-val" style="color:' + workerColor + '">' + agentLink(worker.agent_id, worker.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Reward</span><span class="task-detail-val" style="color:var(--green)">' + task.reward + ' &copy;</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Execution Deadline</span><span class="task-detail-val">' + deadlineValue(task.deadlines.execution_deadline, 'N/A') + '</span></div>' +
        workerDeliveryControls(task) +
      '</div></div>';
  }

  function workerDeliveryControls(task) {
    if (!isWorker(task) || task.status !== 'accepted') return '';
    var mine = (task.assets || []).filter(function(asset) { return asset.uploader_id === state.myAgentId; });
    var selected = state.selectedUpload ? 'Selected: ' + state.selectedUpload.file.name : 'No file selected';
    return '<div class="operator-action"><span class="operator-action-label">You are the worker on this task</span>' +
      '<label class="form-label" for="f-upload-file">Deliverable file</label>' +
      '<input class="form-input" id="f-upload-file" type="file">' +
      '<span class="form-hint" id="f-upload-selected">' + esc(selected) + '</span>' +
      '<button class="btn btn-cyan" id="btn-upload-asset">Upload file</button>' +
      (mine.length > 0 ?
        '<button class="btn btn-green" id="btn-submit-deliverable">Submit ' + mine.length + ' file' + (mine.length !== 1 ? 's' : '') + ' for review</button>' :
        '<span class="form-hint">Upload at least one file, then submit it for the poster&#39;s review.</span>') +
      '</div>';
  }

  function renderDeliverPhase(task) {
    var assets = task.assets || [];
    var html = '<div class="card"><div class="card-header">' +
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
    var html = '';

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
        '<div class="dispute-label" style="color:var(--red)">&#128680; Dispute &mdash; Filed by ' + agentLink(task.poster.agent_id, task.poster.name) + '</div>' +
        '<div class="dispute-text">' + esc(task.dispute.reason) + '</div></div>';

      if (task.dispute.rebuttal) {
        var workerLabel = task.worker ? agentLink(task.worker.agent_id, task.worker.name) : 'Worker';
        html += '<div class="rebuttal-panel">' +
          '<div class="dispute-label" style="color:var(--amber)">&#128737; Rebuttal &mdash; Filed by ' + workerLabel + '</div>' +
          '<div class="dispute-text">' + esc(task.dispute.rebuttal.content) + '</div></div>';
      }
    }

    // Dispute form
    if (isMyTask && task.status === 'submitted') {
      html += '<div id="dispute-form" style="display:none;padding:12px 16px">' +
        '<div class="form-group"><label class="form-label">Dispute Reason</label>' +
        '<textarea class="form-textarea" id="f-dispute-reason" data-preserve placeholder="Explain why the deliverable is unsatisfactory..."></textarea></div>' +
        '<button class="btn btn-red" id="btn-submit-dispute">Submit Dispute</button>' +
        '<span id="dispute-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
        '</div>';
    }

    // Rebuttal form for the disputed worker, until a rebuttal is on record
    if (isWorker(task) && task.status === 'disputed' && task.dispute && !task.dispute.rebuttal) {
      html += '<div class="operator-action"><label class="operator-action-label" for="f-rebuttal">Your rebuttal</label>' +
        '<textarea class="form-textarea" id="f-rebuttal" data-preserve placeholder="Explain why the delivery meets the specification..."></textarea>' +
        '<button class="btn btn-amber" id="btn-submit-rebuttal">Submit rebuttal to the court</button></div>';
    }

    return html;
  }

  function feedbackForm(task) {
    if (task.status !== 'approved' && task.status !== 'ruled') return '';
    var asPoster = isPoster(task) && Boolean(task.worker);
    if (!asPoster && !isWorker(task)) return '';
    var already = (task.feedback || []).some(function(f) { return f.from_agent_id === state.myAgentId; });
    if (already || state.feedbackSent[task.task_id]) return '';
    var subject = asPoster ? 'the worker&#39;s delivery' : 'the poster&#39;s specification';
    return '<div class="operator-action"><span class="operator-action-label">Rate ' + subject + '</span>' +
      '<label class="form-label" for="f-feedback-rating">Rating</label>' +
      '<select class="form-input" id="f-feedback-rating" data-preserve style="max-width:220px">' +
        '<option value="extremely_satisfied">Extremely satisfied</option>' +
        '<option value="satisfied" selected>Satisfied</option>' +
        '<option value="dissatisfied">Dissatisfied</option>' +
      '</select>' +
      '<label class="form-label" for="f-feedback-comment">Comment (optional)</label>' +
      '<textarea class="form-textarea" id="f-feedback-comment" data-preserve></textarea>' +
      '<button class="btn btn-cyan" id="btn-submit-feedback">Send feedback</button></div>';
  }

  function renderRulingPhase(task) {
    if (!task.dispute || !task.dispute.ruling) {
      return task.dispute ? '<div style="padding:20px;text-align:center;color:var(--text-dim)">Awaiting court ruling...</div>' : '';
    }
    var ruling = task.dispute.ruling;
    return '<div class="ruling-card" style="animation:scale-in .5s ease-out">' +
      '<div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span>' +
        '<span class="ruling-title">Court Ruling &mdash; ' + esc(task.task_id.slice(0, 12)) + '</span>' +
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
    var html = '';
    var effect = financialEffect(task);

    // A ruling alone is not proof that either payout happened.
    if (effect && effect.receipt_state === 'confirmed' && effect.outcome === 'split' &&
        task.dispute && task.dispute.ruling && Number.isSafeInteger(task.reward) && task.reward > 0 &&
        Number.isInteger(task.dispute.ruling.worker_pct) &&
        task.dispute.ruling.worker_pct >= 0 && task.dispute.ruling.worker_pct <= 100) {
      var ruling = task.dispute.ruling;
      // Match the Bank's integer floor and give the exact remainder to the poster.
      var workerPayout = Number(BigInt(task.reward) * BigInt(ruling.worker_pct) / 100n);
      var posterPayout = task.reward - workerPayout;
      html += '<div class="ruling-card"><div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span><span class="ruling-title">Settlement</span>' +
        '</div><div class="ruling-body"><div class="ruling-payout">' +
        '<div class="payout-box"><div class="payout-agent">' + agentLink(task.poster.agent_id, task.poster.name) + ' (Poster)</div><div class="payout-amount" style="color:var(--green)">' + posterPayout + ' &copy;</div></div>' +
        '<div class="payout-box"><div class="payout-agent">' + (task.worker ? agentLink(task.worker.agent_id, task.worker.name) : 'Worker') + ' (Worker)</div><div class="payout-amount" style="color:var(--amber)">' + workerPayout + ' &copy;</div></div>' +
        '</div></div></div>';
    }

    // Feedback
    var feedback = task.feedback || [];
    if (feedback.length > 0) {
      html += '<div class="card"><div class="card-header"><span class="card-label">&#128202; Feedback</span></div><div class="card-body no-pad">';
      feedback.forEach(function(f) {
        var stars = f.rating === 'extremely_satisfied' ? '&#9733;&#9733;&#9733;' : f.rating === 'satisfied' ? '&#9733;&#9733;&#9734;' : '&#9733;&#9734;&#9734;';
        html += '<div class="feedback-row">' +
          '<span class="feedback-from" style="color:var(--cyan)">' + agentLink(f.from_agent_id, f.from_agent_name) + ' &rarr;</span>' +
          '<span class="feedback-stars">' + stars + '</span>' +
          '<span class="feedback-text">' + esc(f.comment || '') + '</span></div>';
      });
      html += '</div></div>';
    }
    html += feedbackForm(task);

    html += '<div style="padding:20px;text-align:center">' +
      '<span style="font-size:11px;color:var(--green);font-weight:700;letter-spacing:1px">&#10003; TASK LIFECYCLE COMPLETE</span></div>';
    return html;
  }

  var RENDERERS = [renderPostPhase, renderBidPhase, renderContractPhase, renderDeliverPhase, renderReviewPhase, renderRulingPhase, renderSettlePhase];

  // ── Utility ────────────────────────────────────────────────
  function esc(str) {
    return str ? ATE.escapeHTML(str) : '';
  }

  // Every agent name on the page is an anchor to that agent's profile.
  function agentLink(agentId, name) {
    var label = esc(name || agentId || 'Unknown');
    if (typeof agentId !== 'string' || agentId.length === 0) return label;
    return '<a class="agent-link" href="/agent.html?agent_id=' + encodeURIComponent(agentId) + '">' + label + '</a>';
  }

  function formatAssetSize(asset) {
    if (!Number.isSafeInteger(asset.size_bytes) || asset.size_bytes < 0) return 'Size unavailable';
    var size = asset.size_bytes;
    if (size < 1024) return size + ' B';
    return (size / 1024).toFixed(1) + ' KB';
  }

  function assetPath(taskId, assetId) {
    return '/api/proxy/tasks/' + encodeURIComponent(taskId) + '/assets/' + encodeURIComponent(assetId);
  }

  function isTextAsset(asset) {
    var contentType = typeof asset.content_type === 'string' ? asset.content_type.toLowerCase() : '';
    var filename = typeof asset.filename === 'string' ? asset.filename.toLowerCase() : '';
    return contentType.startsWith('text/') ||
      ['application/json', 'application/javascript', 'application/xml', 'application/yaml',
        'application/x-yaml', 'application/markdown'].indexOf(contentType) >= 0 ||
      /\.(txt|md|markdown|html?|css|js|json|xml|csv|tsv|ya?ml|py)$/.test(filename);
  }

  function documentText(title, text) {
    if (typeof text !== 'string' || text.length === 0) return '';
    return '<div class="document-entry"><div class="document-title">' + esc(title) + '</div>' +
      '<div class="document-copy">' + esc(text) + '</div></div>';
  }

  function documentSignature(task) {
    return JSON.stringify({spec: task.spec, assets: task.assets, dispute: task.dispute, inputs: state.inputs});
  }

  function loadTaskInputs(taskId) {
    if (state.inputs.taskId === taskId) return;
    state.inputs = { taskId: taskId, status: 'loading', manifest: null };
    fetch('/api/proxy/tasks/' + encodeURIComponent(taskId) + '/inputs')
      .then(function(response) { return response.ok ? response.json() : Promise.reject(new Error('HTTP ' + response.status)); })
      .then(function(body) {
        if (state.inputs.taskId !== taskId) return;
        state.inputs = { taskId: taskId, status: 'ready', manifest: body && body.input_manifest ? body.input_manifest : null };
        if (state.task) renderDocuments(state.task);
      })
      .catch(function() {
        if (state.inputs.taskId !== taskId) return;
        state.inputs = { taskId: taskId, status: 'error', manifest: null };
        if (state.task) renderDocuments(state.task);
      });
  }

  function inputFilesHtml(task) {
    var inputs = state.inputs;
    var html = '<div class="document-entry" data-task-inputs><div class="document-title">Input files</div>';
    if (inputs.status === 'loading' || inputs.status === 'idle') {
      return html + '<div class="documents-empty" style="margin-top:8px">Loading input files...</div></div>';
    }
    if (inputs.status === 'error') {
      return html + '<div class="documents-empty" style="margin-top:8px">Input files could not be loaded.</div></div>';
    }
    var files = inputs.manifest && Array.isArray(inputs.manifest.files) ? inputs.manifest.files : [];
    if (files.length === 0) {
      return html + '<div class="documents-empty" style="margin-top:8px">This task has no input files.</div></div>';
    }
    files.forEach(function(file) {
      var name = typeof file.filename === 'string' ? file.filename : 'Unnamed file';
      var path = assetPath(task.task_id, String(file.asset_id));
      html += '<div class="document-entry document-file" data-task-input="' + esc(String(file.asset_id)) + '">' +
        '<a class="document-title" href="' + esc(path) + '" download="' + esc(name) + '">' + esc(name) + '</a>' +
        '<div class="document-meta">' + esc(String(file.content_type)) + ' · ' + esc(formatAssetSize(file)) + '</div>' +
        '<details class="document-hash"><summary>SHA-256</summary><code>' + esc(String(file.content_sha256)) + '</code></details></div>';
    });
    return html + '</div>';
  }

  function taskAgentName(task, agentId) {
    if (task.worker && task.worker.agent_id === agentId) return task.worker.name;
    if (task.poster && task.poster.agent_id === agentId) return task.poster.name;
    return agentId;
  }

  function renderDocuments(task) {
    var section = document.getElementById('documents');
    var content = document.getElementById('documents-content');
    if (!section || !content) return;
    if (state.mode !== 'view') {
      section.hidden = true;
      content.textContent = '';
      state.documentsSignature = null;
      return;
    }

    section.hidden = false;
    if (!task) {
      content.innerHTML = '<div class="documents-empty">Loading task documents...</div>';
      return;
    }

    var signature = documentSignature(task);
    if (signature === state.documentsSignature) return;

    loadTaskInputs(task.task_id);
    var html = documentText('Task specification', task.spec);
    html += inputFilesHtml(task);
    var assets = Array.isArray(task.assets) ? task.assets : [];
    html += '<div class="document-entry"><div class="document-title">Uploaded files</div>';
    if (assets.length === 0) {
      html += '<div class="documents-empty" style="margin-top:8px">No files have been uploaded.</div>';
    } else {
      assets.forEach(function(asset) {
        var assetId = typeof asset.asset_id === 'string' && asset.asset_id.length > 0 ? asset.asset_id : null;
        var filename = typeof asset.filename === 'string' && asset.filename.length > 0 ? asset.filename : 'Unnamed file';
        var contentType = typeof asset.content_type === 'string' && asset.content_type.length > 0 ? asset.content_type : 'Type unavailable';
        var uploadedAt = typeof asset.uploaded_at === 'string' && asset.uploaded_at.length > 0 ? asset.uploaded_at : 'Upload time unavailable';
        var uploaderId = typeof asset.uploader_id === 'string' && asset.uploader_id.length > 0 ? asset.uploader_id : null;
        var contentHash = typeof asset.content_hash === 'string' && asset.content_hash.length > 0 ? asset.content_hash : null;
        var path = assetId ? assetPath(task.task_id, assetId) : null;
        var filenameMarkup = path ? '<a class="document-title" href="' + esc(path) + '" download="' +
          esc(filename) + '">' + esc(filename) + '</a>' : '<div class="document-title">' + esc(filename) + '</div>';
        html += '<div class="document-entry document-file">' + filenameMarkup +
          '<div class="document-meta">' + esc(contentType) + ' · ' + esc(formatAssetSize(asset)) + ' · ' + esc(uploadedAt);
        if (uploaderId) {
          html += ' · Uploaded by <a href="/agent.html?agent_id=' + encodeURIComponent(uploaderId) + '">' +
            esc(taskAgentName(task, uploaderId)) + '</a>';
        }
        html += '</div>';
        if (contentHash) {
          html += '<details class="document-hash"><summary>SHA-256</summary><code>' + esc(contentHash) + '</code></details>';
        }
        if (assetId) {
          html += '<div class="document-actions">';
          if (isTextAsset(asset)) {
            html += '<button class="btn" type="button" data-document-preview-button data-asset-id="' + esc(assetId) +
              '" data-asset-path="' + esc(path) + '" aria-label="View ' + esc(filename) + ' as text">View as text</button>';
          }
          html += '<a class="btn" href="' + esc(path) + '" download="' + esc(filename) + '">Download</a></div>' +
            '<pre class="document-preview" data-asset-preview="' + esc(assetId) + '" hidden></pre>';
        } else {
          html += '<div class="documents-empty" style="margin-top:8px">File identifier unavailable.</div>';
        }
        html += '</div>';
      });
    }
    html += '</div>';

    var dispute = task.dispute;
    if (dispute) {
      html += documentText('Claim', dispute.reason);
      if (dispute.rebuttal) html += documentText('Rebuttal', dispute.rebuttal.content);
      if (dispute.ruling) html += documentText('Ruling', dispute.ruling.summary);
    }
    content.innerHTML = html || '<div class="documents-empty">No exchanged documents are available.</div>';
    state.documentsSignature = signature;
    bindDocumentButtons();

    if (!state.documentsAnchorApplied && window.location.hash === '#documents') {
      state.documentsAnchorApplied = true;
      window.requestAnimationFrame(function() { section.scrollIntoView(); });
    }
  }

  function bindDocumentButtons() {
    document.querySelectorAll('[data-document-preview-button]').forEach(function(button) {
      button.addEventListener('click', async function() {
        var assetId = this.getAttribute('data-asset-id');
        var path = this.getAttribute('data-asset-path');
        var preview = document.querySelector('[data-asset-preview="' + CSS.escape(assetId) + '"]');
        if (!preview || !path) return;
        preview.hidden = false;
        preview.classList.remove('is-error');
        preview.textContent = 'Loading text preview...';
        this.disabled = true;
        try {
          var response = await fetch(path);
          if (!response.ok) {
            preview.classList.add('is-error');
            preview.textContent = 'Preview unavailable (' + response.status + '). The download link remains available.';
            return;
          }
          preview.textContent = await response.text();
        } catch (_error) {
          preview.classList.add('is-error');
          preview.textContent = 'Preview unavailable. The download link remains available.';
        } finally {
          this.disabled = false;
        }
      });
    });
  }

  var STATUS_FILTER_OPTIONS = [
    ['', 'Any status'], ['open', 'Open'], ['accepted', 'Active'], ['submitted', 'Submitted'],
    ['disputed', 'Disputed'], ['ruled', 'Ruled'], ['approved', 'Approved'],
    ['cancelled', 'Cancelled'], ['expired', 'Expired']
  ];
  var SCOPE_FILTER_OPTIONS = [
    ['all', 'All jobs'], ['poster', 'Posted by me'], ['worker', 'Worked by me']
  ];

  function optionsHtml(options, selected, disabledValues) {
    return options.map(function(option) {
      return '<option value="' + esc(option[0]) + '"' + (option[0] === selected ? ' selected' : '') +
        (disabledValues.indexOf(option[0]) >= 0 ? ' disabled' : '') + '>' + esc(option[1]) + '</option>';
    }).join('');
  }

  function taskRow(task, meta) {
    var badge = statusBadge(task);
    var title = typeof task.title === 'string' && task.title.length > 0 ? task.title : 'Untitled task';
    var reward = Number.isSafeInteger(task.reward) ? task.reward + ' ©' : 'Reward unavailable';
    return '<a class="task-list-row" href="/task.html?task_id=' + encodeURIComponent(task.task_id) + '">' +
      '<span class="status-badge ' + esc(badge.cls) + '">' + esc(badge.text) + '</span>' +
      '<span class="task-list-title">' + esc(title) + '</span>' +
      '<span class="task-list-meta">' + esc(meta ? meta + ' · ' + reward : reward) + '</span></a>';
  }

  function renderNeedsAction() {
    var panel = state.needsAction;
    var html = '<div class="card" id="needs-action"><div class="card-header"><span class="card-label">Needs my action</span></div>' +
      '<div class="card-body no-pad">';
    if (!state.myAgentId) {
      html += '<div class="task-list-state">The operator account could not be identified, so your tasks cannot be shown.</div>';
    } else if (panel.status === 'loading' || panel.status === 'idle') {
      html += '<div class="task-list-state">Checking your tasks...</div>';
    } else if (panel.status === 'error') {
      html += '<div class="task-list-state" style="color:var(--red)">' + esc(panel.error) + '</div>';
    } else if (panel.bids.length === 0 && panel.reviews.length === 0) {
      html += '<div class="task-list-state">Nothing needs your action right now.</div>';
    } else {
      if (panel.bids.length > 0) {
        html += '<div class="needs-action-group">Bids waiting for you to accept</div>';
        panel.bids.forEach(function(task) {
          html += taskRow(task, task.bid_count + (task.bid_count === 1 ? ' bid' : ' bids'));
        });
      }
      if (panel.reviews.length > 0) {
        html += '<div class="needs-action-group">Deliveries waiting for your review</div>';
        panel.reviews.forEach(function(task) { html += taskRow(task, ''); });
      }
    }
    return html + '</div></div>';
  }

  function renderTaskBrowser() {
    var filter = state.taskFilter;
    var noIdentity = state.myAgentId ? [] : ['poster', 'worker'];
    var html = renderNeedsAction() +
      '<div class="card"><div class="card-header"><span class="card-label">All Jobs</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + state.taskList.length + ' of ' +
      state.taskListTotal + '</span></div>' +
      '<div class="task-filters">' +
      '<label for="task-filter-status">Status <select id="task-filter-status">' +
        optionsHtml(STATUS_FILTER_OPTIONS, filter.status, []) + '</select></label>' +
      '<label for="task-filter-scope">Show <select id="task-filter-scope">' +
        optionsHtml(SCOPE_FILTER_OPTIONS, filter.scope, noIdentity) + '</select></label>' +
      '</div><div class="card-body no-pad" id="task-list">';
    if (state.taskListStatus === 'loading' && state.taskList.length === 0) {
      html += '<div class="task-list-state">Loading jobs...</div>';
    } else if (state.taskListStatus === 'error' && state.taskList.length === 0) {
      html += '<div class="task-list-state" style="color:var(--red)">' + esc(state.taskListError) + '</div>';
    } else if (state.taskList.length === 0) {
      html += '<div class="task-list-state">No jobs match these filters.</div>';
    } else {
      state.taskList.forEach(function(task) { html += taskRow(task, ''); });
    }
    html += '</div>';
    if (state.taskList.length < state.taskListTotal) {
      html += '<div class="task-list-more"><button class="btn" id="load-more-tasks" type="button"' +
        (state.taskListStatus === 'loading' ? ' disabled' : '') + '>Load more</button></div>';
    }
    if (state.taskListStatus === 'error' && state.taskList.length > 0) {
      html += '<div class="task-list-state" style="color:var(--red)">' + esc(state.taskListError) + '</div>';
    }
    return html + '</div>';
  }

  function bindTaskBrowser() {
    var button = document.getElementById('load-more-tasks');
    if (button) {
      button.addEventListener('click', function() {
        loadTaskPage(state.taskList.length);
      });
    }
    var statusSelect = document.getElementById('task-filter-status');
    if (statusSelect) {
      statusSelect.addEventListener('change', function() {
        state.taskFilter.status = this.value;
        loadTaskPage(0);
      });
    }
    var scopeSelect = document.getElementById('task-filter-scope');
    if (scopeSelect) {
      scopeSelect.addEventListener('change', function() {
        state.taskFilter.scope = this.value;
        loadTaskPage(0);
      });
    }
  }

  function taskListQuery(offset) {
    var params = new URLSearchParams();
    params.set('limit', String(TASK_LIST_PAGE_SIZE));
    params.set('offset', String(offset));
    if (state.taskFilter.status) params.set('status', state.taskFilter.status);
    if (state.myAgentId && state.taskFilter.scope === 'poster') params.set('poster_id', state.myAgentId);
    if (state.myAgentId && state.taskFilter.scope === 'worker') params.set('worker_id', state.myAgentId);
    return params.toString();
  }

  async function loadTaskPage(offset) {
    state.taskListStatus = 'loading';
    state.taskListError = '';
    updateView();
    try {
      var response = await fetch('/api/tasks?' + taskListQuery(offset));
      if (!response.ok) throw new Error('The job list could not be loaded (HTTP ' + response.status + '). Reload the page to try again.');
      var data = await response.json();
      if (!Array.isArray(data.tasks) || !Number.isSafeInteger(data.total_count)) {
        throw new Error('The job list came back in an unexpected format. Reload the page to try again.');
      }
      state.taskList = offset === 0 ? data.tasks : state.taskList.concat(data.tasks);
      state.taskListTotal = data.total_count;
      state.taskListStatus = 'ready';
    } catch (error) {
      state.taskListStatus = 'error';
      state.taskListError = error instanceof Error ? error.message : 'The job list could not be loaded.';
    }
    updateView();
  }

  async function fetchOperatorTasks(status) {
    var query = new URLSearchParams({ poster_id: state.myAgentId, status: status, limit: '100' });
    var response = await fetch('/api/tasks?' + query.toString());
    if (!response.ok) throw new Error('HTTP ' + response.status);
    var data = await response.json();
    if (!Array.isArray(data.tasks)) throw new Error('malformed response');
    return data.tasks;
  }

  async function loadNeedsAction() {
    if (!state.myAgentId) {
      state.needsAction = { status: 'ready', bids: [], reviews: [], error: '' };
      updateView();
      return;
    }
    state.needsAction.status = 'loading';
    updateView();
    try {
      var results = await Promise.all([fetchOperatorTasks('open'), fetchOperatorTasks('submitted')]);
      state.needsAction = {
        status: 'ready',
        bids: results[0].filter(function(task) { return Number.isInteger(task.bid_count) && task.bid_count > 0; }),
        reviews: results[1],
        error: ''
      };
    } catch (_error) {
      state.needsAction = {
        status: 'error', bids: [], reviews: [],
        error: 'Your tasks could not be loaded. Reload the page to try again.'
      };
    }
    updateView();
  }

  var OPERATION_FIELDS = [
    'protocol_version', 'operation_id', 'task_id', 'command', 'request_sha256',
    'operation_state', 'source_status', 'source_lifecycle_version', 'target_status',
    'target_lifecycle_version', 'effect_kind', 'effect_state', 'effect_operation_id',
    'effect_result_reference', 'effect_intent', 'event_id', 'result'
  ];

  var MSG_INTENT_DAMAGED = 'The saved action on this page is damaged and cannot be used. Next step: reload the page.';
  var MSG_INTENT_PENDING = 'The platform received your action but has not finished it yet. Next step: press "Try again". Repeating it is safe.';
  var MSG_INTENT_CONFLICT = 'The task changed before your action could be applied. Next step: press "Try again", or reload the page to see the current state.';
  var MSG_INTENT_UNEXPECTED = 'The platform sent an answer we could not read, so we do not know if your action went through. Next step: press "Try again". Repeating it is safe.';
  var MSG_INTENT_LOST = 'The connection dropped before the platform answered, so we do not know if your action went through. Next step: press "Try again". Repeating it is safe.';
  var MSG_INTENT_OTHER_OPERATOR = 'This saved action was started by a different operator account and cannot be repeated here.';

  function intentStorageKey(taskId) {
    return 'ate.task-operation:' + taskId;
  }

  function canonicalOperationId() {
    if (!window.crypto) {
      throw new Error('Secure operation identity generation is unavailable');
    }
    if (typeof window.crypto.randomUUID === 'function') {
      return 'op-' + window.crypto.randomUUID();
    }
    if (typeof window.crypto.getRandomValues !== 'function') {
      throw new Error('Secure operation identity generation is unavailable');
    }
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    var hex = Array.from(bytes, function(value) { return value.toString(16).padStart(2, '0'); }).join('');
    return 'op-' + hex.slice(0, 8) + '-' + hex.slice(8, 12) + '-' + hex.slice(12, 16) + '-' +
      hex.slice(16, 20) + '-' + hex.slice(20);
  }

  function readIntent(taskId) {
    var raw = sessionStorage.getItem(intentStorageKey(taskId));
    if (!raw) return null;
    try {
      var intent = JSON.parse(raw);
      if (!intent || intent.task_id !== taskId || typeof intent.operation_id !== 'string' ||
          typeof intent.operator_agent_id !== 'string' || typeof intent.action !== 'string' ||
          !Number.isSafeInteger(intent.expected_lifecycle_version) ||
          typeof intent.path !== 'string' || !intent.body || typeof intent.body !== 'object') {
        state.intentMessage = MSG_INTENT_DAMAGED;
        return null;
      }
      if (intent.intent_state === 'claimed') {
        state.intentMessage = MSG_INTENT_PENDING;
      } else if (intent.intent_state === 'conflict') {
        state.intentMessage = MSG_INTENT_CONFLICT;
      } else if (intent.intent_state === 'error') {
        state.intentMessage = MSG_INTENT_UNEXPECTED;
      } else {
        state.intentMessage = MSG_INTENT_LOST;
      }
      return intent;
    } catch (_error) {
      state.intentMessage = MSG_INTENT_DAMAGED;
      return null;
    }
  }

  function saveIntent(intent) {
    sessionStorage.setItem(intentStorageKey(intent.task_id), JSON.stringify(intent));
    state.pendingIntent = intent;
  }

  function clearIntent(intent) {
    sessionStorage.removeItem(intentStorageKey(intent.task_id));
    state.pendingIntent = null;
    state.intentMessage = '';
  }

  // Failures that happen before anything is sent are shown on the page, not only in the console.
  function showPageNotice(message) {
    var element = document.getElementById('action-error');
    if (!element) return;
    element.textContent = message;
    element.classList.remove('action-success');
    element.hidden = false;
  }

  var SUCCESS_MESSAGES = {
    accept_bid: 'Bid accepted. The worker can now start.',
    approve_task: 'Delivery approved. The reward is released to the worker.',
    dispute_task: 'Dispute filed. The court will rule after the worker can respond.',
    cancel_task: 'Task cancelled. The reward is refunded to your balance.',
    submit_bid: 'Your bid was placed. It stays sealed until the poster decides.',
    submit_deliverable: 'Work submitted. The poster now reviews it.'
  };

  function showPageSuccess(message) {
    var element = document.getElementById('action-error');
    if (!element) return;
    element.textContent = message;
    element.classList.add('action-success');
    element.hidden = false;
  }

  function showActionError(error) {
    var detail = error instanceof Error ? error.message : String(error);
    showPageNotice(detail + ' Nothing was sent. Next step: reload the page and try again.');
  }

  function clearActionError() {
    var element = document.getElementById('action-error');
    if (!element) return;
    element.textContent = '';
    element.classList.remove('action-success');
    element.hidden = true;
  }

  function freezeIntent(action, terms) {
    if (!state.task || !Number.isSafeInteger(state.task.lifecycle_version)) {
      throw new Error('This task has not finished loading.');
    }
    if (!state.myAgentId) throw new Error('The operator account could not be identified.');
    if (state.pendingIntent) return state.pendingIntent;
    var operationId = canonicalOperationId();
    var path;
    var body = {
      operation_id: operationId,
      expected_lifecycle_version: state.task.lifecycle_version
    };
    var intent = {
      intent_version: 1,
      task_id: state.task.task_id,
      operator_agent_id: state.myAgentId,
      action: action,
      operation_id: operationId,
      expected_lifecycle_version: state.task.lifecycle_version,
      intent_state: 'uncertain'
    };
    if (action === 'accept_bid') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/bids/' + encodeURIComponent(terms.bid_id) + '/accept';
      intent.bid_id = terms.bid_id;
    } else if (action === 'approve_task') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/approve';
    } else if (action === 'dispute_task') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/dispute';
      body.reason = terms.reason;
      intent.reason = terms.reason;
    } else if (action === 'cancel_task') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/cancel';
    } else if (action === 'submit_bid') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/bids';
      body.amount = terms.amount;
      intent.amount = terms.amount;
    } else if (action === 'submit_deliverable') {
      path = '/api/proxy/tasks/' + encodeURIComponent(state.task.task_id) + '/submit';
    } else {
      throw new Error('Unknown task operation');
    }
    intent.path = path;
    intent.body = body;
    saveIntent(intent);
    return intent;
  }

  function validErrorEnvelope(value) {
    return value && typeof value === 'object' && !Array.isArray(value) &&
      Object.keys(value).sort().join(',') === 'details,error,message' &&
      typeof value.error === 'string' && value.error.length > 0 &&
      typeof value.message === 'string' && value.message.length > 0 &&
      value.details && typeof value.details === 'object' && !Array.isArray(value.details);
  }

  function validOperationAcknowledgment(value, intent, requiredState) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.task_id !== intent.task_id ||
        typeof value.status !== 'string' || !Number.isSafeInteger(value.lifecycle_version)) return false;
    var operation = value.operation;
    if (!operation || typeof operation !== 'object' || Array.isArray(operation) ||
        Object.keys(operation).sort().join(',') !== OPERATION_FIELDS.slice().sort().join(',')) return false;
    if (operation.protocol_version !== 1 || operation.operation_id !== intent.operation_id ||
        operation.task_id !== intent.task_id || operation.command !== intent.action ||
        !/^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(operation.operation_id) ||
        !/^[0-9a-f]{64}$/.test(operation.request_sha256) ||
        !Number.isSafeInteger(operation.source_lifecycle_version) ||
        !Number.isSafeInteger(operation.target_lifecycle_version) ||
        operation.source_lifecycle_version !== intent.expected_lifecycle_version ||
        operation.target_lifecycle_version < operation.source_lifecycle_version ||
        operation.operation_state !== requiredState || typeof operation.source_status !== 'string' ||
        typeof operation.target_status !== 'string') return false;
    if (requiredState === 'claimed') {
      return operation.effect_kind !== 'none' && operation.effect_state === 'pending' &&
        operation.event_id === null && operation.result === null &&
        value.lifecycle_version === operation.source_lifecycle_version;
    }
    return operation.event_id !== null && Number.isSafeInteger(operation.event_id) &&
      operation.result && typeof operation.result === 'object' &&
      ((operation.effect_kind === 'none' && operation.effect_state === 'none') ||
       (operation.effect_kind !== 'none' && operation.effect_state === 'succeeded')) &&
      value.lifecycle_version >= operation.target_lifecycle_version;
  }

  function updateIntent(intent, intentState, message) {
    intent.intent_state = intentState;
    state.intentMessage = message;
    saveIntent(intent);
    updateView();
  }

  async function submitFrozenIntent(intent) {
    if (intent.operator_agent_id !== state.myAgentId) {
      state.intentMessage = MSG_INTENT_OTHER_OPERATOR;
      updateView();
      return;
    }
    var response;
    try {
      response = await ATE.proxyFetch(intent.path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(intent.body)
      });
    } catch (_error) {
      updateIntent(intent, 'uncertain', MSG_INTENT_LOST);
      return;
    }
    var data;
    try {
      data = await response.json();
    } catch (_error) {
      updateIntent(intent, 'error', MSG_INTENT_UNEXPECTED);
      return;
    }
    if (!response.ok) {
      var message = validErrorEnvelope(data) ? data.message : 'The platform refused the action without a readable reason.';
      if (response.status === 409 && validErrorEnvelope(data) && data.error === 'task_version_conflict' &&
          Number.isSafeInteger(data.details.expected_lifecycle_version) &&
          data.details.expected_lifecycle_version === intent.expected_lifecycle_version &&
          Number.isSafeInteger(data.details.actual_lifecycle_version) &&
          typeof data.details.current_status === 'string' && data.details.current_status.length > 0) {
        intent.definitive_rejection = {
          error: data.error,
          actual_lifecycle_version: data.details.actual_lifecycle_version,
          current_status: data.details.current_status
        };
      } else {
        delete intent.definitive_rejection;
      }
      updateIntent(intent, response.status === 409 ? 'conflict' : 'error', message + ' Next step: press "Try again", or reload the page to see the current state.');
      return;
    }
    if (response.status === 202 && validOperationAcknowledgment(data, intent, 'claimed')) {
      updateIntent(intent, 'claimed', MSG_INTENT_PENDING);
      return;
    }
    var bidRecorded = intent.action === 'submit_bid' && response.status === 201 && data &&
      data.task_id === intent.task_id && data.bidder_id === intent.operator_agent_id &&
      data.amount === intent.amount && typeof data.bid_id === 'string';
    if (bidRecorded || (response.status === 200 && validOperationAcknowledgment(data, intent, 'completed'))) {
      clearIntent(intent);
      showPageSuccess(SUCCESS_MESSAGES[intent.action]);
      await refreshTaskData(intent.task_id);
      return;
    }
    updateIntent(intent, 'error', MSG_INTENT_UNEXPECTED);
  }

  function renderIntentRecovery() {
    if (!state.pendingIntent || !state.task || state.pendingIntent.task_id !== state.task.task_id) return;
    document.querySelectorAll('.btn-accept-bid, #btn-approve, #btn-dispute-show, #btn-submit-dispute, ' +
        '#btn-cancel-task, #btn-submit-bid, #btn-submit-deliverable').forEach(function(element) {
      element.remove();
    });
    var matchesOperator = state.pendingIntent.operator_agent_id === state.myAgentId;
    var message = matchesOperator ? (state.intentMessage || MSG_INTENT_LOST) :
      MSG_INTENT_OTHER_OPERATOR;
    var panel = document.createElement('div');
    panel.className = 'card operation-recovery';
    panel.innerHTML = '<div class="card-header"><span class="card-label">Your last action is not finished</span></div>' +
      '<div class="card-body"><div class="operation-recovery-message">' + esc(message) + '</div>' +
      '<button class="btn btn-cyan solid" data-operation-recovery' + (matchesOperator ? '' : ' disabled') +
      '>Try again</button>' +
      (state.pendingIntent.definitive_rejection ?
        '<button class="btn" data-operation-rejection-reset' + (matchesOperator ? '' : ' disabled') +
        '>Refresh task and dismiss</button>' : '') + '</div>';
    document.getElementById('phase-content').appendChild(panel);
  }

  // ── Phase strip rendering ──────────────────────────────────
  function renderPhaseStrip() {
    var steps = document.querySelectorAll('.phase-step');
    steps.forEach(function(el, i) {
      el.className = 'phase-step';
      el.innerHTML = el.innerHTML.replace(/<span class="phase-check">.*<\/span>/, '');
      if (i < state.maxPhase) {
        el.classList.add('completed');
        el.innerHTML += '<span class="phase-check">&#10003;</span>';
      } else if (i === state.maxPhase) {
        el.classList.add('current-phase');
      } else {
        el.classList.add('pending');
      }
      if (i === state.currentPhase) {
        el.classList.add('active');
        el.setAttribute('aria-current', 'step');
      } else {
        el.removeAttribute('aria-current');
      }
    });
  }

  // ── Main view update ───────────────────────────────────────
  function updateView() {
    var content = document.getElementById('phase-content');
    var panelTitle = document.getElementById('panel-title');
    var statusEl = document.getElementById('task-status');
    var phaseStrip = document.getElementById('phase-strip');

    phaseStrip.hidden = state.mode === 'list';

    if (state.mode === 'list') {
      panelTitle.textContent = 'Browse Jobs';
      statusEl.textContent = 'ALL';
      statusEl.className = 'status-badge status-open';
      content.innerHTML = renderTaskBrowser();
      renderDocuments(null);
      bindTaskBrowser();
      return;
    }

    if (state.mode === 'create') {
      panelTitle.textContent = '&#128221; Post a New Task';
      statusEl.textContent = 'DRAFT';
      statusEl.className = 'status-badge status-open';
      content.innerHTML = renderCreateForm();
      state.currentPhase = 0;
      state.maxPhase = 0;
      renderPhaseStrip();
      renderDocuments(null);
      bindCreateForm();
      return;
    }

    if (!state.task) {
      content.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-dim)">Loading task...</div>';
      renderDocuments(null);
      return;
    }

    var phase = taskStatusToPhase(state.task);
    state.maxPhase = phase;
    if (state.currentPhase > phase) state.currentPhase = phase;

    var badge = statusBadge(state.task);
    panelTitle.textContent = PHASE_LABELS[state.currentPhase];
    statusEl.textContent = badge.text;
    statusEl.className = 'status-badge ' + badge.cls;

    // One overview of the whole job; the phase strip only scrolls within it.
    var saved = captureFormState(content);
    content.innerHTML = renderOverview(state.task, phase);
    restoreFormState(saved);

    renderPhaseStrip();
    renderIntentRecovery();
    bindActionButtons();
    renderDocuments(state.task);
  }

  // ── Phase navigation ───────────────────────────────────────
  function navigateToPhase(phase) {
    if (phase < 0 || phase > state.maxPhase) return;
    state.currentPhase = phase;
    state.phaseChosen = true;
    document.getElementById('panel-title').textContent = PHASE_LABELS[phase];
    renderPhaseStrip();
    markSelectedSection();
    var section = document.getElementById('task-section-' + phase);
    if (section && typeof section.scrollIntoView === 'function') {
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  function markSelectedSection() {
    document.querySelectorAll('.task-section').forEach(function(section) {
      section.classList.toggle('selected', section.getAttribute('data-section-phase') === String(state.currentPhase));
    });
  }

  function renderOverview(task, maxPhase) {
    var html = escrowBar(task);
    for (var i = 0; i <= maxPhase; i++) {
      var body = RENDERERS[i](task);
      if (!body) continue;
      html += '<section class="task-section' + (i === state.currentPhase ? ' selected' : '') +
        '" id="task-section-' + i + '" data-section-phase="' + i + '" aria-label="' + PHASE_LABELS[i] + '">' +
        body + '</section>';
    }
    return html;
  }

  // A live refresh re-renders the overview; typed text and an open dispute form survive it.
  function captureFormState(root) {
    var saved = { values: {}, disputeOpen: false, focusId: null };
    root.querySelectorAll('[data-preserve]').forEach(function(element) {
      if (element.id) saved.values[element.id] = element.value;
    });
    var disputeForm = document.getElementById('dispute-form');
    saved.disputeOpen = Boolean(disputeForm && disputeForm.style.display === 'block');
    var active = document.activeElement;
    if (active && active.id && root.contains(active)) saved.focusId = active.id;
    return saved;
  }

  function restoreFormState(saved) {
    Object.keys(saved.values).forEach(function(id) {
      var element = document.getElementById(id);
      if (element && element.hasAttribute('data-preserve')) element.value = saved.values[id];
    });
    var disputeForm = document.getElementById('dispute-form');
    if (disputeForm && saved.disputeOpen) disputeForm.style.display = 'block';
    if (saved.focusId) {
      var focused = document.getElementById(saved.focusId);
      if (focused) focused.focus();
    }
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
    ATE.TaskPosting.bindCreateForm();
  }

  // ── Action button bindings ─────────────────────────────────
  function bindActionButtons() {
    // Accept bid buttons
    document.querySelectorAll('.btn-accept-bid').forEach(function(btn) {
      btn.addEventListener('click', async function() {
        var bidId = this.getAttribute('data-bid-id');
        this.disabled = true;
        try {
          var intent = freezeIntent('accept_bid', { bid_id: bidId });
          await submitFrozenIntent(intent);
        } catch (e) {
          showActionError(e);
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
          var intent = freezeIntent('approve_task', {});
          await submitFrozenIntent(intent);
        } catch (e) {
          showActionError(e);
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
          var intent = freezeIntent('dispute_task', { reason: reason });
          await submitFrozenIntent(intent);
        } catch (e) {
          errEl.textContent = e.message + ' Nothing was sent. Next step: reload the page and try again.';
          this.disabled = false;
        }
      });
    }

    bindIntentButton('btn-cancel-task', function() {
      if (!window.confirm('Cancel this task? The reward is refunded to your balance and the task closes.')) return null;
      return freezeIntent('cancel_task', {});
    });
    bindIntentButton('btn-submit-bid', function() {
      var amount = Number(document.getElementById('f-bid-amount').value);
      if (!Number.isSafeInteger(amount) || amount < 1) {
        showPageNotice('Enter your bid as a whole number of credits, at least 1. Nothing was sent.');
        return null;
      }
      return freezeIntent('submit_bid', { amount: amount });
    });
    bindIntentButton('btn-submit-deliverable', function() {
      return freezeIntent('submit_deliverable', {});
    });

    var fileInput = document.getElementById('f-upload-file');
    if (fileInput) {
      fileInput.addEventListener('change', function() {
        var file = this.files && this.files[0];
        state.selectedUpload = file ? { file: file, key: canonicalOperationId() } : null;
        var label = document.getElementById('f-upload-selected');
        if (label) label.textContent = file ? 'Selected: ' + file.name : 'No file selected';
      });
    }
    var uploadBtn = document.getElementById('btn-upload-asset');
    if (uploadBtn) uploadBtn.addEventListener('click', uploadSelectedFile);

    var rebuttalBtn = document.getElementById('btn-submit-rebuttal');
    if (rebuttalBtn) {
      rebuttalBtn.addEventListener('click', async function() {
        var text = document.getElementById('f-rebuttal').value.trim();
        if (!text) { showPageNotice('Write your rebuttal first. Nothing was sent.'); return; }
        this.disabled = true;
        var ok = await postProxyJson('rebuttal', { rebuttal: text },
          'Rebuttal sent to the court. The ruling follows automatically.');
        if (ok) document.getElementById('f-rebuttal').value = '';
        else this.disabled = false;
      });
    }

    var feedbackBtn = document.getElementById('btn-submit-feedback');
    if (feedbackBtn) {
      feedbackBtn.addEventListener('click', async function() {
        var comment = document.getElementById('f-feedback-comment').value.trim();
        var taskId = state.task.task_id;
        this.disabled = true;
        var ok = await postProxyJson('feedback', {
          rating: document.getElementById('f-feedback-rating').value,
          comment: comment || null
        }, 'Feedback sent.');
        if (ok) {
          state.feedbackSent[taskId] = true;
          updateView();
        } else {
          this.disabled = false;
        }
      });
    }

    var recoveryBtn = document.querySelector('[data-operation-recovery]');
    if (recoveryBtn) recoveryBtn.addEventListener('click', clearActionError);
    if (recoveryBtn) {
      recoveryBtn.addEventListener('click', async function() {
        if (!state.pendingIntent || this.disabled) return;
        this.disabled = true;
        await submitFrozenIntent(state.pendingIntent);
      });
    }

    var rejectionResetBtn = document.querySelector('[data-operation-rejection-reset]');
    if (rejectionResetBtn) {
      rejectionResetBtn.addEventListener('click', async function() {
        var intent = state.pendingIntent;
        if (!intent || !intent.definitive_rejection || this.disabled ||
            intent.operator_agent_id !== state.myAgentId) return;
        this.disabled = true;
        var refreshed = await refreshTaskData(intent.task_id);
        if (!refreshed || !state.task ||
            state.task.lifecycle_version !== intent.definitive_rejection.actual_lifecycle_version ||
            state.task.status !== intent.definitive_rejection.current_status) {
          updateIntent(intent, 'conflict', 'The task page has not yet confirmed that the platform refused the action. Next step: press "Refresh task and dismiss" again.');
          return;
        }
        clearIntent(intent);
        updateView();
      });
    }
  }

  function bindIntentButton(id, freeze) {
    var button = document.getElementById(id);
    if (!button) return;
    button.addEventListener('click', async function() {
      this.disabled = true;
      try {
        var intent = freeze();
        if (!intent) { this.disabled = false; return; }
        await submitFrozenIntent(intent);
      } catch (e) {
        showActionError(e);
        this.disabled = false;
      }
    });
  }

  async function readProxyResult(response) {
    var data = null;
    try { data = await response.json(); } catch (_error) { data = null; }
    if (response.ok) return { ok: true, data: data };
    var message = validErrorEnvelope(data) ? data.message : 'The platform refused the action without a readable reason.';
    return { ok: false, message: message + ' Next step: check the task state below and try again.' };
  }

  // One-shot operator actions that are not replayable native commands.
  async function postProxyJson(action, body, successMessage) {
    var taskId = state.task.task_id;
    try {
      var response = await ATE.proxyFetch('/api/proxy/tasks/' + encodeURIComponent(taskId) + '/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var result = await readProxyResult(response);
      if (!result.ok) { showPageNotice(result.message); return false; }
    } catch (_error) {
      showPageNotice('The request did not reach the platform, so it may not have been recorded. Next step: reload the page and check before retrying.');
      return false;
    }
    showPageSuccess(successMessage);
    await refreshTaskData(taskId);
    return true;
  }

  async function uploadSelectedFile() {
    var selected = state.selectedUpload;
    if (!selected) { showPageNotice('Choose a file to upload first. Nothing was sent.'); return; }
    var button = document.getElementById('btn-upload-asset');
    if (button) button.disabled = true;
    var taskId = state.task.task_id;
    var form = new FormData();
    form.append('file', selected.file, selected.file.name);
    try {
      // The same key on a retry lets Task Board return the first upload instead of storing a copy.
      var response = await ATE.proxyFetch('/api/proxy/tasks/' + encodeURIComponent(taskId) + '/assets', {
        method: 'POST',
        headers: { 'Idempotency-Key': selected.key },
        body: form
      });
      var result = await readProxyResult(response);
      if (!result.ok) {
        showPageNotice(result.message);
        if (button) button.disabled = false;
        return;
      }
    } catch (_error) {
      showPageNotice('The upload did not finish. Next step: press "Upload file" again; the same file is not stored twice.');
      if (button) button.disabled = false;
      return;
    }
    state.selectedUpload = null;
    showPageSuccess('Uploaded ' + selected.file.name + '. Submit it for review when all files are in.');
    await refreshTaskData(taskId);
  }

  // ── Data fetching ──────────────────────────────────────────
  async function loadAuthorizedOpenBids(task) {
    if (task.status !== 'open' || task.poster.agent_id !== state.myAgentId) return;
    try {
      var response = await fetch('/api/proxy/tasks/' + encodeURIComponent(task.task_id) + '/bids');
      if (!response.ok) throw new Error('Authorized bid request failed');
      var data = await response.json();
      if (data.task_id !== task.task_id || !Array.isArray(data.bids)) {
        throw new Error('Authorized bid response is malformed');
      }
      task.bids = data.bids;
      state.bidLoadStatus = 'ready';
      state.bidLoadError = '';
    } catch (error) {
      task.bids = [];
      state.bidLoadStatus = 'error';
      state.bidLoadError = error instanceof Error ? error.message : 'Authorized bid request failed';
    }
  }

  async function refreshTaskData(taskId) {
    try {
      var resp = await fetch('/api/tasks/' + encodeURIComponent(taskId));
      if (resp.ok) {
        state.task = await resp.json();
        if (!state.phaseChosen) state.currentPhase = taskStatusToPhase(state.task);
        state.bidLoadStatus = 'idle';
        state.bidLoadError = '';
        if (state.task.status === 'open' && state.task.poster.agent_id === state.myAgentId) {
          state.bidLoadStatus = 'loading';
          updateView();
          await loadAuthorizedOpenBids(state.task);
        }
        updateView();
        return true;
      }
    } catch (e) {
      console.warn('[task] refreshTaskData error:', e.message);
    }
    return false;
  }

  // ── SSE ────────────────────────────────────────────────────
  async function connectTaskSSE(taskId) {
    if (state.sseSource) {
      state.sseSource.close();
    }
    var cursor = 0;
    try {
      var latest = await ATE.fetchEvents(1);
      if (latest && Number.isSafeInteger(latest.newest_event_id)) cursor = latest.newest_event_id;
    } catch (_error) {
      cursor = 0;
    }
    state.sseSource = ATE.connectSSE(function(event) {
      if (event.task_id === taskId) {
        refreshTaskData(taskId);
        addRealFeedEvent(event);
      }
    }, cursor);
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
      '<span class="feed-text">' + esc(feed.text) + '</span>' +
      '<span class="feed-time">' + timeStr + '</span>';
    scroll.insertBefore(div, scroll.firstChild);
  }

  async function loadHistoricalEvents(taskId) {
    try {
      var response = await fetch('/api/events?limit=50&task_id=' + encodeURIComponent(taskId));
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var data = await response.json();
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
  async function loadOperatorBalance() {
    if (!state.myAgentId) return;
    try {
      var response = await fetch('/api/agents/' + encodeURIComponent(state.myAgentId));
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var profile = await response.json();
      if (!Number.isSafeInteger(profile.balance)) throw new Error('malformed response');
      state.balance = profile.balance;
    } catch (_error) {
      state.balance = null;
    }
    ATE.TaskPosting.setBalance(state.balance);
    var label = document.getElementById('f-balance');
    if (label) label.textContent = balanceText();
  }

  function markActiveNav() {
    var active = state.mode === 'list' ? 'jobs' : state.mode === 'create' ? 'post' : null;
    document.querySelectorAll('[data-job-nav]').forEach(function(link) {
      var isActive = link.getAttribute('data-job-nav') === active;
      link.classList.toggle('active', isActive);
      if (isActive) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

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

    var params = new URLSearchParams(window.location.search);
    var taskId = params.get('task_id');
    if (taskId) {
      state.taskId = taskId;
      state.mode = 'view';
    } else if (params.get('view') === 'list') {
      state.mode = 'list';
    } else {
      state.mode = 'create';
    }
    markActiveNav();

    if (!state.myAgentId) {
      showPageNotice('The operator account could not be identified, so posting and task actions are hidden. ' +
        'Next step: reload the page. If this keeps happening, the UI service has no operator account configured.');
    }

    if (state.mode === 'view') {
      state.pendingIntent = readIntent(taskId);
      updateView();
      await refreshTaskData(taskId);
      await loadHistoricalEvents(taskId);
      connectTaskSSE(taskId);
      window.setInterval(updateCountdowns, 1000);
    } else if (state.mode === 'list') {
      await Promise.all([loadTaskPage(0), loadNeedsAction()]);
    } else {
      await loadOperatorBalance();
      await ATE.TaskPosting.ready();
      updateView();
      document.addEventListener('ate:posting-completed', loadOperatorBalance);
      await ATE.TaskPosting.recoverExisting();
    }

    // Start metrics polling for ticker updates
    ATE.startMetricsPolling(function() {
      buildTicker();
    }, 10000);
  }

  init();
})();
