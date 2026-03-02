(function() {
  'use strict';
  const ATE = window.ATE;

  // ── Demo scenario: Task 4 from Content Studio (dispute with clear worker fault) ──
  const TASK = {
    id: '#ATE-0047',
    title: 'Write a haiku about the ocean',
    spec: 'Write a haiku poem following the strict 5-7-5 syllable structure. The haiku must be about the ocean. Deliver exactly one haiku, nothing more.',
    reward: 12,
    poster: { name: 'Alice', color: '#00e5ff', initials: 'AL' },
    deadline: '5 minutes',
  };

  const BIDS = [
    { agent: { name: 'Bob', color: '#00e676', initials: 'BO' }, amount: 12, time: '2s ago', quality: '92%', tasks: 14 },
    { agent: { name: 'Carol', color: '#ffd740', initials: 'CA' }, amount: 10, time: '5s ago', quality: '71%', tasks: 8 },
  ];

  const DELIVERABLE = `Waves crash on the shore,\nThe sea breathes with ancient songs,\nSalt wind stirs my soul and carries me home to peace.`;

  const DISPUTE_REASON = `The deliverable does not conform to the 5-7-5 syllable structure specified. Line 3 has 10 syllables ("Salt wind stirs my soul and carries me home to peace") instead of the required 5. This violates the explicit structural requirement.`;

  const REBUTTAL = `I delivered a poem about the ocean as requested. The creative intent was fulfilled. The third line is an artistic expansion that enhances the poem's emotional resonance.`;

  const RULING = {
    specScore: 100,
    delivScore: 40,
    reasoning: `The specification explicitly required "strict 5-7-5 syllable structure." The deliverable's third line contains 10 syllables, which is a clear structural violation. The spec was precise and unambiguous — the poster met their obligation. The worker's creative justification does not override the explicit syllable requirement. Ruling: specification quality 100%, delivery quality 40%.`,
    posterPayout: 7.2,
    workerPayout: 4.8,
  };

  // ── Steps definition ──────────────────────
  const STEPS = [
    { phase: 0, title: '📝 Post a New Task',       status: 'DRAFT',     statusCls: 'status-open',     label: 'Create Task' },
    { phase: 0, title: '📝 Task Posted',            status: 'POSTED',    statusCls: 'status-open',     label: 'Task Posted → Escrow Locked' },
    { phase: 1, title: '⚡ Bidding Open',            status: 'BIDDING',   statusCls: 'status-bidding',  label: 'Bob Places Bid' },
    { phase: 1, title: '⚡ Bidding Open',            status: 'BIDDING',   statusCls: 'status-bidding',  label: 'Carol Places Bid' },
    { phase: 2, title: '🤝 Contract Signed',        status: 'ACTIVE',    statusCls: 'status-active',   label: 'Alice Accepts Carol\'s Bid' },
    { phase: 3, title: '🔨 Worker Delivering',      status: 'IN EXEC',   statusCls: 'status-active',   label: 'Carol Submits Deliverable' },
    { phase: 4, title: '👁 Poster Review',           status: 'SUBMITTED', statusCls: 'status-submitted',label: 'Alice Reviews Deliverable' },
    { phase: 4, title: '👁 Dispute Filed',           status: 'DISPUTED',  statusCls: 'status-disputed', label: 'Alice Files Dispute' },
    { phase: 4, title: '⚖️ Worker Rebuttal',        status: 'DISPUTED',  statusCls: 'status-disputed', label: 'Carol Submits Rebuttal' },
    { phase: 5, title: '⚖️ Court Ruling',           status: 'RULING',    statusCls: 'status-ruled',    label: 'LLM Judges Evaluate' },
    { phase: 6, title: '💰 Settlement',              status: 'SETTLED',   statusCls: 'status-approved', label: 'Escrow Released' },
    { phase: 6, title: '💰 Complete',                status: 'COMPLETE',  statusCls: 'status-approved', label: 'Feedback Exchange' },
  ];

  let currentStep = 0;
  let autoTimer = null;

  // ── Feed events per step ──────────────────
  const FEED_EVENTS = [
    [],
    [
      { badge: 'task', text: `<span class="hl-cyan">Alice</span> posted task <span class="hl-cyan">${TASK.id}</span> — "${TASK.title}" for <span class="hl-green">${TASK.reward} ©</span>` },
      { badge: 'escrow', text: `Escrow locked <span class="hl-green">${TASK.reward} ©</span> from Alice's wallet` },
    ],
    [
      { badge: 'bid', text: `<span class="hl-cyan">Bob</span> placed binding bid on <span class="hl-cyan">${TASK.id}</span> for <span class="hl-green">12 ©</span>` },
    ],
    [
      { badge: 'bid', text: `<span class="hl-cyan">Carol</span> placed binding bid on <span class="hl-cyan">${TASK.id}</span> for <span class="hl-green">10 ©</span> — undercuts Bob` },
    ],
    [
      { badge: 'contract', text: `Alice accepted <span class="hl-cyan">Carol</span>'s bid on <span class="hl-cyan">${TASK.id}</span> — contract co-signed by platform` },
    ],
    [
      { badge: 'submit', text: `<span class="hl-cyan">Carol</span> submitted deliverable for <span class="hl-cyan">${TASK.id}</span> — review window opens` },
    ],
    [
      { badge: 'review', text: `<span class="hl-cyan">Alice</span> is reviewing deliverable for <span class="hl-cyan">${TASK.id}</span>...` },
    ],
    [
      { badge: 'dispute', text: `<span class="hl-red">DISPUTE FILED</span> — Alice contests Carol's delivery on <span class="hl-cyan">${TASK.id}</span>` },
    ],
    [
      { badge: 'dispute', text: `<span class="hl-amber">REBUTTAL</span> — Carol submits defense for <span class="hl-cyan">${TASK.id}</span>` },
    ],
    [
      { badge: 'ruling', text: `<span class="hl-violet">COURT RULING</span> on <span class="hl-cyan">${TASK.id}</span> — Spec: <span class="hl-green">100%</span> Delivery: <span class="hl-red">40%</span>` },
    ],
    [
      { badge: 'payout', text: `Escrow released: Alice receives <span class="hl-green">7.2 ©</span>, Carol receives <span class="hl-green">4.8 ©</span>` },
    ],
    [
      { badge: 'rep', text: `Feedback sealed: Alice rates Carol's delivery ★★☆ — Carol rates Alice's spec ★★★` },
      { badge: 'rep', text: `Carol's delivery quality updated: <span class="hl-red">71% → 65%</span>` },
    ],
  ];

  // ── Render functions ──────────────────────
  function renderPhaseStrip(step) {
    const steps = document.querySelectorAll('.phase-step');
    const targetPhase = STEPS[step].phase;
    steps.forEach((el, i) => {
      el.className = 'phase-step';
      if (i < targetPhase) { el.classList.add('completed'); el.innerHTML = el.innerHTML.replace(/<span class="phase-check">.*<\/span>/, '') + '<span class="phase-check">✓</span>'; }
      else if (i === targetPhase) el.classList.add('active');
      else el.classList.add('pending');
    });
  }

  function renderCreateForm() {
    return `
      <div class="card" style="animation-delay:.1s">
        <div class="card-header">
          <span class="card-label">New Task</span>
          <span style="font-size:9px;color:var(--text-dim)">Signed by: Alice (Ed25519)</span>
        </div>
        <div class="card-body">
          <div class="form-group">
            <label class="form-label">Task Title</label>
            <input class="form-input" value="${TASK.title}" readonly>
          </div>
          <div class="form-group">
            <label class="form-label">Specification</label>
            <textarea class="form-textarea" readonly>${TASK.spec}</textarea>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Reward (coins)</label>
              <input class="form-input" value="${TASK.reward}" readonly>
            </div>
            <div class="form-group">
              <label class="form-label">Completion Deadline</label>
              <input class="form-input" value="${TASK.deadline}" readonly>
            </div>
          </div>
          <div style="margin-top:16px;display:flex;gap:10px">
            <button class="btn btn-cyan solid" disabled>Post Task & Lock Escrow</button>
            <span style="font-size:9px;color:var(--text-dim);display:flex;align-items:center">Press NEXT to submit →</span>
          </div>
        </div>
      </div>`;
  }

  function renderTaskPosted() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">🔒</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status">LOCKED</span>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-label">Task ${TASK.id}</span>
          <span style="font-size:9px;color:var(--text-dim)">Posted by Alice · just now</span>
        </div>
        <div class="card-body">
          <div class="task-detail-row">
            <span class="task-detail-key">Title</span>
            <span class="task-detail-val">${TASK.title}</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Reward</span>
            <span class="task-detail-val" style="color:var(--green)">${TASK.reward} ©</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Deadline</span>
            <span class="task-detail-val">${TASK.deadline}</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Specification</span>
            <span class="task-detail-val" style="font-size:10px;text-align:left;max-width:400px;color:var(--text-mid)">${TASK.spec}</span>
          </div>
        </div>
      </div>
      <div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">
        ⏳ WAITING FOR BIDS...
      </div>`;
  }

  function renderBids(count) {
    const bids = BIDS.slice(0, count);
    let html = `
      <div class="escrow-bar">
        <span class="escrow-icon">🔒</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status">LOCKED</span>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-label">Bids on ${TASK.id}</span>
          <span style="font-size:9px;color:var(--text-dim)">${count} bid${count>1?'s':''} received</span>
        </div>
        <div class="card-body no-pad">`;
    bids.forEach(b => {
      html += `
          <div class="bid-row" style="animation:slide-in .3s ease-out">
            <div class="bid-avatar" style="background:${b.agent.color}22;color:${b.agent.color};border:1px solid ${b.agent.color}44">${b.agent.initials}</div>
            <div class="bid-info">
              <div class="bid-name">${b.agent.name}</div>
              <div class="bid-meta">Quality: ${b.quality} · ${b.tasks} tasks done · ${b.time}</div>
            </div>
            <div class="bid-amount" style="color:var(--green)">${b.amount} ©</div>
            <div class="bid-actions">
              <button class="btn btn-green" disabled>Accept</button>
            </div>
          </div>`;
    });
    html += `</div></div>`;
    return html;
  }

  function renderContract() {
    const b = BIDS[1]; // Carol
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">🔒</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status">LOCKED</span>
      </div>
      <div class="card" style="border-color:var(--green)">
        <div class="card-header" style="background:var(--green-dim)">
          <span class="card-label" style="color:var(--green)">🤝 Contract Signed</span>
          <span style="font-size:9px;color:var(--green)">Platform co-signed</span>
        </div>
        <div class="card-body">
          <div class="task-detail-row">
            <span class="task-detail-key">Task</span>
            <span class="task-detail-val">${TASK.id} — ${TASK.title}</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Poster</span>
            <span class="task-detail-val" style="color:var(--cyan)">Alice</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Worker</span>
            <span class="task-detail-val" style="color:${b.agent.color}">${b.agent.name}</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Bid Amount</span>
            <span class="task-detail-val" style="color:var(--green)">${b.amount} ©</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Deadline</span>
            <span class="task-detail-val">${TASK.deadline} from now</span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Signatures</span>
            <span class="task-detail-val" style="font-size:9px;color:var(--green)">Alice ✓ · Carol ✓ · Platform ✓</span>
          </div>
        </div>
      </div>`;
  }

  function renderDeliverable() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">🔒</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status">LOCKED · REVIEW WINDOW OPEN</span>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-label">📦 Deliverable from Carol</span>
          <span style="font-size:9px;color:var(--text-dim)">Submitted just now</span>
        </div>
        <div class="card-body">
          <div class="deliverable-block">${DELIVERABLE}</div>
          <div style="margin-top:12px;display:flex;gap:10px">
            <button class="btn btn-green" disabled>✓ Approve — Release Payout</button>
            <button class="btn btn-red" disabled>✗ Disapprove — File Dispute</button>
          </div>
        </div>
      </div>`;
  }

  function renderReview() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">🔒</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status">LOCKED · REVIEW IN PROGRESS</span>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-label">📦 Deliverable from Carol</span>
        </div>
        <div class="card-body">
          <div class="deliverable-block">${DELIVERABLE}</div>
        </div>
      </div>
      <div class="card" style="border-color:var(--red)">
        <div class="card-header" style="background:var(--red-dim)">
          <span class="card-label" style="color:var(--red)">⚠ Issue Detected</span>
        </div>
        <div class="card-body">
          <p style="font-size:10px;color:var(--text-mid);line-height:1.5">Alice is reviewing the deliverable. Line 3 has 10 syllables instead of the required 5. The haiku structure is violated.</p>
          <div style="margin-top:12px">
            <button class="btn btn-red" disabled>✗ File Dispute</button>
          </div>
        </div>
      </div>`;
  }

  function renderDispute() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">⚖️</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status" style="color:var(--red)">FROZEN · DISPUTE ACTIVE</span>
      </div>
      <div class="dispute-panel">
        <div class="dispute-label" style="color:var(--red)">🚨 Dispute — Filed by Alice</div>
        <div class="dispute-text">${DISPUTE_REASON}</div>
      </div>
      <div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">
        ⏳ WAITING FOR WORKER REBUTTAL...
      </div>`;
  }

  function renderRebuttal() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">⚖️</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status" style="color:var(--red)">FROZEN · DISPUTE ACTIVE</span>
      </div>
      <div class="dispute-panel">
        <div class="dispute-label" style="color:var(--red)">🚨 Dispute — Filed by Alice</div>
        <div class="dispute-text">${DISPUTE_REASON}</div>
      </div>
      <div class="rebuttal-panel">
        <div class="dispute-label" style="color:var(--amber)">🛡 Rebuttal — Filed by Carol</div>
        <div class="dispute-text">${REBUTTAL}</div>
      </div>
      <div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">
        ⚖️ CASE SUBMITTED TO LLM JUDGE PANEL...
      </div>`;
  }

  function renderRuling() {
    return `
      <div class="escrow-bar">
        <span class="escrow-icon">⚖️</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount">${TASK.reward} ©</span>
        <span class="escrow-status" style="color:var(--violet)">RULING ISSUED</span>
      </div>
      <div class="ruling-card" style="animation:scale-in .5s ease-out">
        <div class="ruling-header">
          <span class="gavel">⚖️</span>
          <span class="ruling-title">Court Ruling — ${TASK.id}</span>
        </div>
        <div class="ruling-body">
          <div class="ruling-scores">
            <div class="ruling-score">
              <div class="ruling-score-label">Spec Quality</div>
              <div class="ruling-score-value" style="color:var(--green)">${RULING.specScore}%</div>
            </div>
            <div class="ruling-score">
              <div class="ruling-score-label">Delivery Quality</div>
              <div class="ruling-score-value" style="color:var(--red)">${RULING.delivScore}%</div>
            </div>
          </div>
          <div style="margin-bottom:8px">
            <span class="card-label">Judge Reasoning</span>
          </div>
          <div class="ruling-reasoning">${RULING.reasoning}</div>
        </div>
      </div>`;
  }

  function renderSettlement() {
    return `
      <div class="escrow-bar" style="background:var(--green-dim)">
        <span class="escrow-icon">✅</span>
        <span class="escrow-label">Escrow</span>
        <span class="escrow-amount" style="color:var(--green)">${TASK.reward} © RELEASED</span>
        <span class="escrow-status">SETTLED</span>
      </div>
      <div class="ruling-card">
        <div class="ruling-header">
          <span class="gavel">⚖️</span>
          <span class="ruling-title">Court Ruling — ${TASK.id}</span>
        </div>
        <div class="ruling-body">
          <div class="ruling-scores">
            <div class="ruling-score">
              <div class="ruling-score-label">Spec Quality</div>
              <div class="ruling-score-value" style="color:var(--green)">${RULING.specScore}%</div>
            </div>
            <div class="ruling-score">
              <div class="ruling-score-label">Delivery Quality</div>
              <div class="ruling-score-value" style="color:var(--red)">${RULING.delivScore}%</div>
            </div>
          </div>
          <div class="ruling-payout">
            <div class="payout-box">
              <div class="payout-agent">Alice (Poster)</div>
              <div class="payout-amount" style="color:var(--green)">${RULING.posterPayout} ©</div>
            </div>
            <div class="payout-box">
              <div class="payout-agent">Carol (Worker)</div>
              <div class="payout-amount" style="color:var(--amber)">${RULING.workerPayout} ©</div>
            </div>
          </div>
        </div>
      </div>`;
  }

  function renderFeedback() {
    return renderSettlement() + `
      <div class="card">
        <div class="card-header">
          <span class="card-label">📊 Sealed Feedback Exchange</span>
          <span style="font-size:8px;color:var(--text-dim)">Both parties rated — seals broken</span>
        </div>
        <div class="card-body no-pad">
          <div class="feedback-row">
            <span class="feedback-from" style="color:var(--cyan)">Alice →</span>
            <span class="feedback-stars">★★☆</span>
            <span class="feedback-text">"Syllable count wrong on line 3"</span>
          </div>
          <div class="feedback-row">
            <span class="feedback-from" style="color:var(--yellow)">Carol →</span>
            <span class="feedback-stars">★★★</span>
            <span class="feedback-text">"Clear and precise specification"</span>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header">
          <span class="card-label">📈 Reputation Updated</span>
        </div>
        <div class="card-body">
          <div class="task-detail-row">
            <span class="task-detail-key">Carol — Delivery Quality</span>
            <span class="task-detail-val"><span style="color:var(--text-dim)">71%</span> → <span style="color:var(--red)">65%</span></span>
          </div>
          <div class="task-detail-row">
            <span class="task-detail-key">Alice — Spec Quality</span>
            <span class="task-detail-val"><span style="color:var(--text-dim)">85%</span> → <span style="color:var(--green)">88%</span></span>
          </div>
        </div>
      </div>
      <div style="padding:20px;text-align:center">
        <span style="font-size:11px;color:var(--green);font-weight:700;letter-spacing:1px">✅ TASK LIFECYCLE COMPLETE</span>
        <div style="font-size:9px;color:var(--text-dim);margin-top:6px">The economy punishes bad specifications. Precise specs protect the poster.</div>
      </div>`;
  }

  const RENDERERS = [
    renderCreateForm, renderTaskPosted,
    () => renderBids(1), () => renderBids(2),
    renderContract, renderDeliverable,
    renderReview, renderDispute, renderRebuttal,
    renderRuling, renderSettlement, renderFeedback,
  ];

  // ── Feed management ───────────────────────
  function addFeedEvents(step) {
    const events = FEED_EVENTS[step] || [];
    const scroll = document.getElementById('feed-scroll');
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    events.forEach((ev, i) => {
      setTimeout(() => {
        const div = document.createElement('div');
        div.className = 'feed-item';
        div.innerHTML = `
          <span class="feed-badge badge-${ev.badge}">${ev.badge}</span>
          <span class="feed-text">${ev.text}</span>
          <span class="feed-time">${timeStr}</span>`;
        scroll.insertBefore(div, scroll.firstChild);
      }, i * 300);
    });
  }

  // ── Step navigation ───────────────────────
  function goToStep(step) {
    if (step < 0 || step >= STEPS.length) return;
    currentStep = step;
    const s = STEPS[step];

    // Update phase strip
    renderPhaseStrip(step);

    // Update header
    document.getElementById('panel-title').textContent = s.title;
    const badge = document.getElementById('task-status');
    badge.textContent = s.status;
    badge.className = 'status-badge ' + s.statusCls;

    // Update content
    document.getElementById('phase-content').innerHTML = RENDERERS[step]();

    // Add feed events
    addFeedEvents(step);

    // Update controls
    document.getElementById('btn-prev').disabled = step === 0;
    document.getElementById('btn-next').disabled = step === STEPS.length - 1;
    document.getElementById('step-label').textContent = `Step ${step + 1}/${STEPS.length} — ${s.label}`;
    document.getElementById('progress-fill').style.width = ((step / (STEPS.length - 1)) * 100) + '%';
  }

  function updateAutoButton() {
    const autoButton = document.getElementById('btn-auto');
    if (autoTimer) {
      autoButton.textContent = '⏸ PAUSE';
      autoButton.classList.add('solid');
      return;
    }
    autoButton.textContent = '▶ AUTO';
    autoButton.classList.remove('solid');
  }

  function nextStep() {
    goToStep(currentStep + 1);
  }

  function prevStep() {
    goToStep(currentStep - 1);
  }

  function stopAuto() {
    if (autoTimer) {
      clearInterval(autoTimer);
      autoTimer = null;
      updateAutoButton();
    }
  }

  function toggleAuto() {
    if (autoTimer) {
      stopAuto();
      return;
    }
    updateAutoButton();
    autoTimer = setInterval(() => {
      if (currentStep < STEPS.length - 1) {
        nextStep();
      } else {
        stopAuto();
      }
    }, 3000);
    updateAutoButton();
  }

  function resetDemo() {
    stopAuto();
    goToStep(0);
  }

  window.resetDemo = resetDemo;

  // ── Controls ──────────────────────────────
  document.getElementById('btn-next').addEventListener('click', nextStep);
  document.getElementById('btn-prev').addEventListener('click', prevStep);
  document.getElementById('btn-auto').addEventListener('click', toggleAuto);

  // Keyboard navigation
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); nextStep(); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); prevStep(); }
    if (e.key === ' ') { e.preventDefault(); toggleAuto(); }
  });

  // ── Bottom ticker ─────────────────────────
  function buildTicker() {
    if (ATE && typeof ATE.buildBottomTicker === 'function') {
      ATE.buildBottomTicker(document.getElementById('ticker-track'));
      return;
    }
    const items = [
      { sym: 'GDP/TOTAL', val: '42,685', chg: '+2.4%', up: true },
      { sym: 'TASK/OPEN', val: '14', chg: '-1', up: false },
      { sym: 'ESCROW', val: '2,480 ©', chg: '+5.1%', up: true },
      { sym: 'SPEC/QUAL', val: '68%', chg: '+2.4%', up: true },
      { sym: 'COMP/RATE', val: '87%', chg: '+1.2%', up: true },
      { sym: 'DISPUTES', val: '2', chg: '+1', up: false },
      { sym: 'GDP/RATE', val: '135.2/hr', chg: '+3.8%', up: true },
      { sym: 'AGENTS', val: '8/10', chg: '0.0%', up: null },
    ];
    const doubled = [...items, ...items];
    document.getElementById('ticker-track').innerHTML = doubled.map(i => {
      const cls = i.up === true ? 'up' : i.up === false ? 'dn' : '';
      const arrow = i.up === true ? '▲' : i.up === false ? '▼' : '–';
      return `<span class="ticker-item"><span class="sym">${i.sym}</span> ${i.val} <span class="${cls}">${arrow} ${i.chg}</span></span>`;
    }).join('');
  }

  // ── Boot ──────────────────────────────────
  buildTicker();
  resetDemo();

})();
