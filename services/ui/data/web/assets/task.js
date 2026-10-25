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
    maxPhase: 0,
    sseSource: null,
    documentsAnchorApplied: false,
    documentsSignature: null,
    taskList: [],
    taskListTotal: 0,
    taskListStatus: 'idle',
    taskListError: ''
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

  function escrowBar(task) {
    if (!task) return '';
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
      '<div class="form-group">' +
        '<label class="form-label">Bid Acceptance Window (s)</label>' +
        '<input class="form-input" id="f-accept-window" type="number" min="1" placeholder="Required">' +
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
        '<span class="card-label">Task ' + esc(task.task_id.slice(0, 12)) + '</span>' +
        '<span style="font-size:9px;color:var(--text-dim)">Posted by ' + esc(task.poster.name) + '</span>' +
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
    var bidCount = Number.isInteger(task.bid_count) ? task.bid_count : bids.length;
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = escrowBar(task) +
      '<div class="card"><div class="card-header">' +
      '<span class="card-label">Bids on ' + esc(task.task_id.slice(0, 12)) + '</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + bidCount + ' bid' + (bidCount !== 1 ? 's' : '') + ' received</span>' +
      '</div><div class="card-body no-pad">';
    if (bids.length === 0 && isMyTask && state.bidLoadStatus === 'loading') {
      html += '<div class="bid-load-status" style="padding:14px;text-align:center;color:var(--text-dim)">Loading authorized bid details...</div>';
    } else if (bids.length === 0 && isMyTask && state.bidLoadStatus === 'error') {
      html += '<div class="bid-load-error" style="padding:14px;text-align:center;color:var(--red)">Bid details are unavailable. Retry by refreshing this task.</div>';
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
        '<div class="bid-info"><div class="bid-name">' + esc(bidder.name) + '</div>' +
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
    return escrowBar(task) +
      '<div class="card" style="border-color:var(--green)">' +
      '<div class="card-header" style="background:var(--green-dim)">' +
        '<span class="card-label" style="color:var(--green)">&#129309; Contract Signed</span>' +
        '<span style="font-size:9px;color:var(--green)">Platform co-signed</span>' +
      '</div><div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Task</span><span class="task-detail-val">' + esc(task.task_id.slice(0, 12)) + ' &mdash; ' + esc(task.title) + '</span></div>' +
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
    var html = escrowBar(task);
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
    return str ? ATE.escapeHTML(str) : '';
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
    return JSON.stringify({spec: task.spec, assets: task.assets, dispute: task.dispute});
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

    var html = documentText('Task specification', task.spec);
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

  function renderTaskBrowser() {
    var html = '<div class="card"><div class="card-header"><span class="card-label">All Jobs</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + state.taskList.length + ' of ' +
      state.taskListTotal + '</span></div><div class="card-body no-pad" id="task-list">';
    if (state.taskListStatus === 'loading' && state.taskList.length === 0) {
      html += '<div class="task-list-state">Loading jobs...</div>';
    } else if (state.taskListStatus === 'error' && state.taskList.length === 0) {
      html += '<div class="task-list-state" style="color:var(--red)">' + esc(state.taskListError) + '</div>';
    } else if (state.taskList.length === 0) {
      html += '<div class="task-list-state">No jobs are available.</div>';
    } else {
      state.taskList.forEach(function(task) {
        var badge = statusBadge(task);
        var title = typeof task.title === 'string' && task.title.length > 0 ? task.title : 'Untitled task';
        var reward = Number.isSafeInteger(task.reward) ? task.reward + ' ©' : 'Reward unavailable';
        html += '<a class="task-list-row" href="/task.html?task_id=' + encodeURIComponent(task.task_id) + '">' +
          '<span class="status-badge ' + esc(badge.cls) + '">' + esc(badge.text) + '</span>' +
          '<span class="task-list-title">' + esc(title) + '</span>' +
          '<span class="task-list-meta">' + esc(reward) + '</span></a>';
      });
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
    if (!button) return;
    button.addEventListener('click', function() {
      loadTaskPage(state.taskList.length);
    });
  }

  async function loadTaskPage(offset) {
    state.taskListStatus = 'loading';
    state.taskListError = '';
    updateView();
    try {
      var response = await fetch('/api/tasks?limit=' + TASK_LIST_PAGE_SIZE + '&offset=' + offset);
      if (!response.ok) throw new Error('Job list request failed (' + response.status + ').');
      var data = await response.json();
      if (!Array.isArray(data.tasks) || !Number.isSafeInteger(data.total_count)) {
        throw new Error('Job list response is malformed.');
      }
      state.taskList = offset === 0 ? data.tasks : state.taskList.concat(data.tasks);
      state.taskListTotal = data.total_count;
      state.taskListStatus = 'ready';
    } catch (error) {
      state.taskListStatus = 'error';
      state.taskListError = error instanceof Error ? error.message : 'Job list request failed.';
    }
    updateView();
  }

  var OPERATION_FIELDS = [
    'protocol_version', 'operation_id', 'task_id', 'command', 'request_sha256',
    'operation_state', 'source_status', 'source_lifecycle_version', 'target_status',
    'target_lifecycle_version', 'effect_kind', 'effect_state', 'effect_operation_id',
    'effect_result_reference', 'effect_intent', 'event_id', 'result'
  ];

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
        state.intentMessage = 'Saved operation intent is malformed and cannot be retried.';
        return null;
      }
      if (intent.intent_state === 'claimed') {
        state.intentMessage = 'Operation pending on the platform. Retry the saved operation explicitly.';
      } else if (intent.intent_state === 'conflict') {
        state.intentMessage = 'Saved operation has a platform conflict. Retry only for authoritative confirmation.';
      } else if (intent.intent_state === 'error') {
        state.intentMessage = 'Saved operation received an invalid response. Retry explicitly.';
      } else {
        state.intentMessage = 'Operation response is uncertain. Retry the saved operation explicitly.';
      }
      return intent;
    } catch (_error) {
      state.intentMessage = 'Saved operation intent is malformed and cannot be retried.';
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

  function freezeIntent(action, terms) {
    if (!state.task || !Number.isSafeInteger(state.task.lifecycle_version)) {
      throw new Error('Authoritative task lifecycle version is unavailable');
    }
    if (!state.myAgentId) throw new Error('Operator identity is unavailable');
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
      state.intentMessage = 'Saved operation belongs to a different operator identity.';
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
      updateIntent(intent, 'uncertain', 'Operation response is uncertain. Retry the saved operation explicitly.');
      return;
    }
    var data;
    try {
      data = await response.json();
    } catch (_error) {
      updateIntent(intent, 'error', 'Invalid Task Board acknowledgment response.');
      return;
    }
    if (!response.ok) {
      var message = validErrorEnvelope(data) ? data.message : 'Task Board returned an invalid error response.';
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
      updateIntent(intent, response.status === 409 ? 'conflict' : 'error', message + ' Retry the saved operation explicitly.');
      return;
    }
    if (response.status === 202 && validOperationAcknowledgment(data, intent, 'claimed')) {
      updateIntent(intent, 'claimed', 'Operation pending on the platform. Retry the saved operation explicitly.');
      return;
    }
    if (response.status === 200 && validOperationAcknowledgment(data, intent, 'completed')) {
      clearIntent(intent);
      await refreshTaskData(intent.task_id);
      return;
    }
    updateIntent(intent, 'error', 'Invalid Task Board acknowledgment response. Retry the saved operation explicitly.');
  }

  function renderIntentRecovery() {
    if (!state.pendingIntent || !state.task || state.pendingIntent.task_id !== state.task.task_id) return;
    document.querySelectorAll('.btn-accept-bid, #btn-approve, #btn-dispute-show, #btn-submit-dispute').forEach(function(element) {
      element.remove();
    });
    var matchesOperator = state.pendingIntent.operator_agent_id === state.myAgentId;
    var message = matchesOperator ? (state.intentMessage || 'Saved operation awaiting explicit confirmation retry.') :
      'Saved operation belongs to a different operator identity.';
    var panel = document.createElement('div');
    panel.className = 'card operation-recovery';
    panel.innerHTML = '<div class="card-header"><span class="card-label">Saved Task Operation</span></div>' +
      '<div class="card-body"><div class="operation-recovery-message">' + esc(message) + '</div>' +
      '<button class="btn btn-cyan solid" data-operation-recovery' + (matchesOperator ? '' : ' disabled') +
      '>Retry Saved Operation</button>' +
      (state.pendingIntent.definitive_rejection ?
        '<button class="btn" data-operation-rejection-reset' + (matchesOperator ? '' : ' disabled') +
        '>Refresh and Dismiss Rejected Operation</button>' : '') + '</div>';
    document.getElementById('phase-content').appendChild(panel);
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

    if (state.currentPhase === 0 && phase === 0 && state.task.status === 'open') {
      content.innerHTML = renderPostPhase(state.task);
    } else {
      var renderer = RENDERERS[state.currentPhase];
      content.innerHTML = renderer ? renderer(state.task) : '';
    }

    renderPhaseStrip();
    renderIntentRecovery();
    bindActionButtons();
    renderDocuments(state.task);
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
          var intent = freezeIntent('approve_task', {});
          await submitFrozenIntent(intent);
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
          var intent = freezeIntent('dispute_task', { reason: reason });
          await submitFrozenIntent(intent);
        } catch (e) {
          errEl.textContent = e.message;
          this.disabled = false;
        }
      });
    }

    var recoveryBtn = document.querySelector('[data-operation-recovery]');
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
          updateIntent(intent, 'conflict', 'Authoritative task state did not confirm the rejection. Retry the refresh explicitly.');
          return;
        }
        clearIntent(intent);
        updateView();
      });
    }
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
        var newPhase = taskStatusToPhase(state.task);
        state.currentPhase = newPhase;
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
      '<span class="feed-text">' + esc(feed.text) + '</span>' +
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
      state.pendingIntent = readIntent(taskId);
      updateView();
      await refreshTaskData(taskId);
      await loadHistoricalEvents(taskId);
      connectTaskSSE(taskId);
    } else if (params.get('view') === 'list') {
      state.mode = 'list';
      await loadTaskPage(0);
    } else {
      state.mode = 'create';
      await ATE.TaskPosting.ready();
      updateView();
      await ATE.TaskPosting.recoverExisting();
    }

    // Start metrics polling for ticker updates
    ATE.startMetricsPolling(function() {
      buildTicker();
    }, 10000);
  }

  init();
})();
