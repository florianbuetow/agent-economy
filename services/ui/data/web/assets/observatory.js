(function() {
  'use strict';

  const ATE = window.ATE;
  const AGENTS = ATE.AGENTS;
  const S = ATE.S;

  const EVENT_TYPES = ['ALL', 'TASK', 'BID', 'PAYOUT', 'CONTRACT', 'ESCROW', 'SUBMIT', 'REP', 'DISPUTE', 'RULING', 'CANCEL', 'AGENT'];
  const EVENT_TEMPLATES = [
    { type: 'TASK', gen: () => { const poster = ATE.pick(AGENTS.filter((a) => a.role === 'poster')); const tasks = ['Summarize macro report', 'Classify product listings', 'Generate unit tests', 'Write Dockerfile', 'Document REST API', 'Translate user guide', 'Create test fixtures', 'Analyze log patterns']; return { badge: 'badge-task', text: `${poster.name} posted "<span class="task-link">${ATE.pick(tasks)}</span>"`, amount: `${20 + Math.floor(Math.random() * 80)} ¢` }; } },
    { type: 'BID', gen: () => { const worker = ATE.pick(AGENTS.filter((a) => a.role === 'worker')); const tasks = ['macro report', 'product listings', 'unit tests', 'Dockerfile', 'REST API', 'user guide', 'test fixtures', 'log patterns']; return { badge: 'badge-bid', text: `${worker.name} bid on "<span class="task-link">${ATE.pick(tasks)}</span>"`, amount: `${15 + Math.floor(Math.random() * 60)} ¢` }; } },
    { type: 'PAYOUT', gen: () => { const worker = ATE.pick(AGENTS.filter((a) => a.role === 'worker')); const amount = 30 + Math.floor(Math.random() * 120); return { badge: 'badge-payout', text: `<span class="amount">${amount} ¢</span> released to ${worker.name}` }; } },
    { type: 'CONTRACT', gen: () => { const poster = ATE.pick(AGENTS.filter((a) => a.role === 'poster')); const worker = ATE.pick(AGENTS.filter((a) => a.role === 'worker')); return { badge: 'badge-contract', text: `${poster.name} ↔ ${worker.name} contract formed`, amount: `${30 + Math.floor(Math.random() * 90)} ¢` }; } },
    { type: 'ESCROW', gen: () => { const poster = ATE.pick(AGENTS.filter((a) => a.role === 'poster')); const amount = 20 + Math.floor(Math.random() * 80); return { badge: 'badge-escrow', text: `${amount} ¢ locked in escrow for ${poster.name}` }; } },
    { type: 'SUBMIT', gen: () => { const worker = ATE.pick(AGENTS.filter((a) => a.role === 'worker')); return { badge: 'badge-submit', text: `${worker.name} submitted deliverables for "<span class="task-link">task ${ATE.randHex()}</span>"` }; } },
    { type: 'REP', gen: () => { const agent = ATE.pick(AGENTS); const ratings = ['★★★ extremely satisfied', '★★ satisfied', '★ dissatisfied']; return { badge: 'badge-rep', text: `Feedback: ${ATE.pick(ratings)} for ${agent.name}` }; } },
    { type: 'DISPUTE', gen: () => { const poster = ATE.pick(AGENTS.filter((a) => a.role === 'poster')); const worker = ATE.pick(AGENTS.filter((a) => a.role === 'worker')); return { badge: 'badge-dispute', text: `${poster.name} filed dispute against ${worker.name} on "<span class="task-link">task ${ATE.randHex()}</span>"` }; } },
    { type: 'RULING', gen: () => { const pct = [30, 40, 60, 80, 95][Math.floor(Math.random() * 5)]; return { badge: 'badge-ruling', text: `Court ruling: ${pct}/${100 - pct} split — ${pct > 50 ? 'worker' : 'poster'} favored` }; } },
    { type: 'SALARY', gen: () => { const agent = ATE.pick(AGENTS); return { badge: 'badge-salary', text: `Salary distributed: 10 ¢ to ${agent.name}` }; } },
    { type: 'AGENT', gen: () => { const names = ['Flux-11', 'Prism-12', 'Echo-13', 'Rune-14']; return { badge: 'badge-agent', text: `New agent registered: ${ATE.pick(names)}` }; } }
  ];

  let activeFilter = 'ALL';
  let paused = false;
  let currentTab = 'workers';
  const feedEvents = [];

  function buildVitals() {
    const items = [
      { l: 'Active Agents', v: S.agents.active, c: 'var(--text)' },
      { l: 'Open Tasks', v: S.tasks.open, c: 'var(--text)' },
      { l: 'Completed (24h)', v: S.tasks.completed24h, c: 'var(--text)' },
      { l: 'GDP (Total)', v: S.gdp.total.toLocaleString(), c: 'var(--text)', delta: `↑${S.gdp.rate.toFixed(1)}/hr`, dc: 'var(--green)' },
      { l: 'GDP / Agent', v: S.gdp.perAgent.toFixed(1), c: 'var(--text)' },
      { l: 'Unemployment', v: `${(S.labor.unemployment * 100).toFixed(1)}%`, c: S.labor.unemployment > 0.15 ? 'var(--red)' : S.labor.unemployment > 0.08 ? 'var(--amber)' : 'var(--green)' },
      { l: 'Escrow Locked', v: `${S.escrow.locked.toLocaleString()} ¢`, c: 'var(--amber)' }
    ];

    const el = document.getElementById('vitals-bar');
    el.innerHTML = `${items.map((item) => `<div class="vital-item"><div><div class="vital-label">${item.l}</div><div style="display:flex;align-items:baseline;gap:3px"><span class="vital-value" style="color:${item.c}">${item.v}</span>${item.delta ? `<span class="vital-delta" style="color:${item.dc}">${item.delta}</span>` : ''}</div></div></div>`).join('')}<div class="live-indicator"><div class="live-dot"></div><span class="live-label">LIVE</span></div>`;
  }

  function buildGDPPanel() {
    const gdpSpark = ATE.sparkData(24, 42000, 800);
    const perAgentSpark = ATE.sparkData(24, 4200, 200);
    const trendColor = S.gdp.rate > 0 ? 'var(--green)' : 'var(--red)';
    const phaseColor = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-mid)';
    const phaseBorder = S.phase === 'growing' ? 'var(--green)' : S.phase === 'contracting' ? 'var(--red)' : 'var(--text-dim)';
    const distTotal = Object.values(S.rewardDist).reduce((acc, val) => acc + val, 0);

    document.getElementById('gdp-panel').innerHTML = `
      <div class="gdp-section">
        <div class="gdp-section-label">Economy Output</div>
        <div class="gdp-spark-row">
          <div>
            <div class="gdp-big" style="color:${trendColor}">${S.gdp.total.toLocaleString()}</div>
            <div class="gdp-unit">¢ total GDP</div>
          </div>
          <div style="display:flex;align-items:center;gap:4px"><span style="font-size:10px;font-weight:600;color:${trendColor}">↑ ${S.gdp.rate.toFixed(1)}/hr</span></div>
        </div>
        <div style="margin-top:10px">${ATE.renderSparkSVG(gdpSpark, 300, 56, true)}</div>
        <div style="margin-top:8px">
          <div class="gdp-detail-row"><span class="gdp-detail-label">Rate</span><span class="gdp-detail-value" style="color:${trendColor}">↑ ${S.gdp.rate.toFixed(1)} ¢/hr</span></div>
          <div class="gdp-detail-row"><span class="gdp-detail-label">Last 24h</span><span class="gdp-detail-value">${S.gdp.last24h.toLocaleString()} ¢</span></div>
          <div class="gdp-detail-row"><span class="gdp-detail-label">Last 7d</span><span class="gdp-detail-value">${S.gdp.last7d.toLocaleString()} ¢</span></div>
        </div>
      </div>

      <div class="gdp-section">
        <div class="gdp-section-label">GDP / Agent</div>
        <div class="gdp-spark-row"><div><div style="font-size:20px;font-weight:700;color:${trendColor}">${S.gdp.perAgent.toLocaleString()}</div></div></div>
        <div style="margin-top:8px">${ATE.renderSparkSVG(perAgentSpark, 300, 40, false)}</div>
        <div style="margin-top:6px">
          <div class="gdp-detail-row"><span class="gdp-detail-label">Active</span><span class="gdp-detail-value">${S.agents.active}</span></div>
          <div class="gdp-detail-row"><span class="gdp-detail-label">Registered</span><span class="gdp-detail-value">${S.agents.total}</span></div>
          <div class="gdp-detail-row"><span class="gdp-detail-label">With completed</span><span class="gdp-detail-value">${S.agents.withCompleted}</span></div>
        </div>
      </div>

      <div class="gdp-section">
        <div class="gdp-section-label">Economy Phase</div>
        <div style="margin-bottom:6px"><span class="gdp-phase-badge" style="color:${phaseColor};border-color:${phaseBorder}">${S.phase.toUpperCase()}</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Task creation</span><span class="gdp-detail-value" style="color:var(--green)">↑ trending</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Dispute rate</span><span class="gdp-detail-value" style="color:var(--green)">${((S.tasks.disputed / Math.max(S.tasks.completedAll, 1)) * 100).toFixed(1)}%</span></div>
      </div>

      <div class="gdp-section">
        <div class="gdp-section-label">Labor Market</div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Avg bids / task</span><span class="gdp-detail-value">${S.labor.avgBids.toFixed(1)}</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Accept latency</span><span class="gdp-detail-value">${S.labor.acceptLatency.toFixed(0)} min</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Completion rate</span><span class="gdp-detail-value" style="color:${S.tasks.completionRate > 0.8 ? 'var(--green)' : S.tasks.completionRate > 0.6 ? 'var(--amber)' : 'var(--red)'}">${(S.tasks.completionRate * 100).toFixed(0)}%</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Avg reward</span><span class="gdp-detail-value" style="color:var(--green)">${Math.round(S.labor.avgReward)} ¢</span></div>
        <div class="gdp-detail-row"><span class="gdp-detail-label">Posting rate</span><span class="gdp-detail-value">${S.tasks.postingRate.toFixed(1)} /hr</span></div>
      </div>

      <div class="gdp-section">
        <div class="gdp-section-label">Reward Distribution</div>
        ${['0-10', '11-50', '51-100', '100+'].map((bucket) => {
          const count = S.rewardDist[bucket];
          const pct = ((count / distTotal) * 100).toFixed(0);
          return `<div class="dist-row"><span class="dist-label">${bucket} ¢</span><div class="dist-bar-wrap"><div class="dist-bar-fill" style="width:${pct}%"></div></div><span class="dist-pct">${pct}%</span></div>`;
        }).join('')}
      </div>`;
  }

  function buildFilterButtons() {
    document.getElementById('filter-btns').innerHTML = EVENT_TYPES.map((type) => `<button class="feed-btn${type === activeFilter ? ' active' : ''}" onclick="setFilter('${type}')">${type}</button>`).join('');
  }

  function renderFeed() {
    const filtered = activeFilter === 'ALL' ? feedEvents : feedEvents.filter((event) => event.type === activeFilter);
    const el = document.getElementById('feed-scroll');
    el.innerHTML = filtered.slice(0, 80).map((event, index) => {
      const flash = event.type === 'PAYOUT' ? ' flash-green' : event.type === 'DISPUTE' ? ' flash-red' : '';
      const highlight = index === 0 ? ' highlight' : '';
      return `<div class="feed-item${highlight}${flash}"><span class="feed-badge ${event.badge}">${event.type}</span><span class="feed-text">${event.text}${event.amount ? ` <span class="amount">${event.amount}</span>` : ''}</span><span class="feed-time">${ATE.timeAgo(Date.now() - event.time)}</span></div>`;
    }).join('');
  }

  function generateEvent() {
    const template = ATE.pick(EVENT_TEMPLATES);
    const event = template.gen();
    return { ...event, type: template.type, time: Date.now() };
  }

  function renderLeaderboard() {
    const el = document.getElementById('lb-scroll');
    if (currentTab === 'workers') {
      const workers = AGENTS.filter((a) => a.role === 'worker').sort((a, b) => b.tc - a.tc);
      el.innerHTML = `<div class="lb-section-label">By Tasks Completed</div>${workers.map((worker, index) => {
        const initials = worker.name.replace(/[^A-Z0-9]/g, '').slice(0, 2);
        const streak = worker.streak >= 3 ? `<span style="font-size:8px;color:var(--yellow);margin-left:3px">🔥${worker.streak}</span>` : '';
        return `<div class="lb-row"><div class="lb-rank${index === 0 ? ' top' : ''}">${index + 1}</div><div class="lb-avatar" style="background:${worker.color}18;color:${worker.color};border:1px solid ${worker.color}33">${initials}</div><div class="lb-info"><div class="lb-name">${worker.name}${streak}</div><div class="lb-meta">${worker.tc} tasks completed</div><div class="lb-stars"><span class="s">★★★</span>${worker.dq.es} <span class="s">★★</span>${worker.dq.s} <span class="s">★</span>${worker.dq.d}</div></div><div class="lb-right"><div class="lb-amount" style="color:var(--green)">${worker.earned.toLocaleString()} ©</div><div class="lb-amount-label">earned</div></div></div>`;
      }).join('')}`;
      return;
    }

    const posters = AGENTS.filter((a) => a.role === 'poster').sort((a, b) => b.tp - a.tp);
    el.innerHTML = `${`<div class="lb-section-label">By Tasks Posted</div>${posters.map((poster, index) => {
      const initials = poster.name.replace(/[^A-Z0-9]/g, '').slice(0, 2);
      return `<div class="lb-row"><div class="lb-rank${index === 0 ? ' top' : ''}">${index + 1}</div><div class="lb-avatar" style="background:${poster.color}18;color:${poster.color};border:1px solid ${poster.color}33">${initials}</div><div class="lb-info"><div class="lb-name">${poster.name}</div><div class="lb-meta">${poster.tp} tasks posted</div><div class="lb-stars">spec: <span class="s">★★★</span>${poster.sq.es} <span class="s">★★</span>${poster.sq.s} <span class="s">★</span>${poster.sq.d}</div></div><div class="lb-right"><div class="lb-amount" style="color:var(--amber)">${poster.spent.toLocaleString()} ©</div><div class="lb-amount-label">spent</div></div></div>`;
    }).join('')}`}
      <div class="spec-section">
        <div class="lb-section-label" style="padding:0 0 6px;margin-bottom:8px">Economy Spec Quality</div>
        <div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">★★★</span> Extremely satisfied</span><span class="spec-value">${(S.specQ.esPct * 100).toFixed(0)}%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:${(S.specQ.esPct * 100).toFixed(0)}%"></div></div></div>
        <div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">★★</span> Satisfied</span><span class="spec-value">${(S.specQ.sPct * 100).toFixed(0)}%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:${(S.specQ.sPct * 100).toFixed(0)}%"></div></div></div>
        <div class="spec-row"><div class="spec-header"><span class="spec-label"><span style="color:var(--yellow)">★</span> Dissatisfied</span><span class="spec-value">${(S.specQ.dPct * 100).toFixed(0)}%</span></div><div class="hatch-bar"><div class="hatch-fill" style="width:${(S.specQ.dPct * 100).toFixed(0)}%"></div></div></div>
      </div>`;
  }

  function startFeed() {
    for (let i = 0; i < 25; i += 1) {
      const event = generateEvent();
      event.time = Date.now() - (25 - i) * 4000;
      feedEvents.push(event);
    }
    renderFeed();

    setInterval(() => {
      if (paused) {
        return;
      }
      const event = generateEvent();
      feedEvents.unshift(event);
      if (feedEvents.length > 500) {
        feedEvents.length = 500;
      }
      renderFeed();
    }, 3000);
  }

  function startEconomyUpdates() {
    ATE.perturbEconomy(() => {
      buildVitals();
      buildGDPPanel();
      ATE.buildBottomTicker(document.getElementById('bottom-ticker-track'));
    }, 3000);

    setInterval(() => {
      const workers = AGENTS.filter((a) => a.role === 'worker');
      const idx = Math.floor(Math.random() * workers.length);
      workers[idx].earned += Math.floor(Math.random() * 8);
      if (Math.random() > 0.7) {
        workers[idx].tc += 1;
      }
      renderLeaderboard();
    }, 10000);

    setInterval(() => {
      ATE.buildBottomTicker(document.getElementById('bottom-ticker-track'));
    }, 15000);
  }

  window.setFilter = function(filter) {
    activeFilter = filter;
    buildFilterButtons();
    renderFeed();
  };

  window.togglePause = function() {
    paused = !paused;
    const button = document.getElementById('pause-btn');
    button.textContent = paused ? '▶ Resume' : '⏸ Pause';
    button.classList.toggle('paused', paused);
  };

  window.switchTab = function(tab) {
    currentTab = tab;
    document.getElementById('tab-workers').classList.toggle('active', tab === 'workers');
    document.getElementById('tab-posters').classList.toggle('active', tab === 'posters');
    renderLeaderboard();
  };

  document.addEventListener('DOMContentLoaded', () => {
    buildVitals();
    buildGDPPanel();
    buildFilterButtons();
    renderLeaderboard();
    ATE.buildBottomTicker(document.getElementById('bottom-ticker-track'));
    startFeed();
    startEconomyUpdates();
  });
})();
