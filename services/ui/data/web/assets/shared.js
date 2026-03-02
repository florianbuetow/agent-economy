(function() {
  'use strict';

  const AGENTS = [
    { id: 'axiom-1', name: 'Axiom-1', role: 'worker', color: '#00e5ff', earned: 680, spent: 40, tc: 22, tp: 2, dq: { es: 18, s: 3, d: 1 }, sq: { es: 1, s: 1, d: 0 }, streak: 8 },
    { id: 'nexus-3', name: 'Nexus-3', role: 'worker', color: '#00e676', earned: 410, spent: 20, tc: 14, tp: 1, dq: { es: 10, s: 3, d: 1 }, sq: { es: 0, s: 1, d: 0 }, streak: 3 },
    { id: 'sigma-2', name: 'Sigma-2', role: 'worker', color: '#ffd740', earned: 320, spent: 10, tc: 11, tp: 0, dq: { es: 8, s: 2, d: 1 }, sq: { es: 0, s: 0, d: 0 }, streak: 2 },
    { id: 'delta-4', name: 'Delta-4', role: 'worker', color: '#e040fb', earned: 245, spent: 15, tc: 9, tp: 1, dq: { es: 6, s: 2, d: 1 }, sq: { es: 1, s: 0, d: 0 }, streak: 0 },
    { id: 'orbit-8', name: 'Orbit-8', role: 'worker', color: '#40c4ff', earned: 188, spent: 5, tc: 7, tp: 0, dq: { es: 5, s: 1, d: 1 }, sq: { es: 0, s: 0, d: 0 }, streak: 1 },
    { id: 'zen-0', name: 'Zen-0', role: 'worker', color: '#b388ff', earned: 92, spent: 0, tc: 3, tp: 0, dq: { es: 2, s: 1, d: 0 }, sq: { es: 0, s: 0, d: 0 }, streak: 1 },
    { id: 'helix-7', name: 'Helix-7', role: 'poster', color: '#ff9100', earned: 60, spent: 520, tc: 1, tp: 18, dq: { es: 0, s: 1, d: 0 }, sq: { es: 6, s: 7, d: 5 }, streak: 0 },
    { id: 'vector-9', name: 'Vector-9', role: 'poster', color: '#ff5252', earned: 30, spent: 380, tc: 0, tp: 14, dq: { es: 0, s: 0, d: 0 }, sq: { es: 8, s: 4, d: 2 }, streak: 0 },
    { id: 'nova-5', name: 'Nova-5', role: 'poster', color: '#69f0ae', earned: 45, spent: 290, tc: 2, tp: 11, dq: { es: 1, s: 1, d: 0 }, sq: { es: 7, s: 3, d: 1 }, streak: 0 },
    { id: 'pulse-6', name: 'Pulse-6', role: 'poster', color: '#ffab40', earned: 20, spent: 180, tc: 0, tp: 8, dq: { es: 0, s: 0, d: 0 }, sq: { es: 4, s: 3, d: 1 }, streak: 0 }
  ];

  const S = {
    gdp: { total: 42680, last24h: 3240, last7d: 18920, rate: 135.2, perAgent: 4268 },
    agents: { total: 10, active: 8, withCompleted: 7 },
    tasks: { completed24h: 12, completedAll: 1243, open: 14, inExec: 6, disputed: 2, completionRate: 0.87, postingRate: 4.2 },
    escrow: { locked: 2480 },
    specQ: { avg: 68, esPct: 0.42, sPct: 0.38, dPct: 0.2, trend: 'up', delta: 2.4 },
    labor: { avgBids: 3.2, avgReward: 52, unemployment: 0.12, acceptLatency: 8.4 },
    phase: 'growing',
    rewardDist: { '0-10': 5, '11-50': 38, '51-100': 42, '100+': 15 }
  };

  function pick(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
  }

  function randHex() {
    return Math.random().toString(16).slice(2, 10);
  }

  function timeAgo(ms) {
    const seconds = Math.floor(ms / 1000);
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    return `${Math.floor(seconds / 3600)}h ago`;
  }

  function sparkData(n, base, variance) {
    const out = [];
    let value = base;
    for (let i = 0; i < n; i += 1) {
      value += (Math.random() - 0.42) * variance;
      value = Math.max(base * 0.3, value);
      out.push(value);
    }
    return out;
  }

  function renderSparkSVG(data, w, h, fill) {
    const max = Math.max(...data);
    const min = Math.min(...data);
    const range = max - min || 1;
    const points = data.map((v, i) => {
      const x = (i / (data.length - 1)) * w;
      const y = h - ((v - min) / range) * (h - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const polyline = points.join(' ');
    const fillPoly = fill ? `<polygon points="0,${h} ${polyline} ${w},${h}" fill="var(--green-fill)" />` : '';
    return `<svg class="sparkline" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${fillPoly}<polyline points="${polyline}" fill="none" stroke="var(--green)" stroke-width="1.2" /></svg>`;
  }

  function genSparkline(n, base, variance) {
    return sparkData(n, base, variance);
  }

  function animateCounter(el, from, to, duration, suffix) {
    const start = performance.now();
    el.classList.add('counting');

    function tick(now) {
      const progress = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = Math.round(from + (to - from) * eased);
      el.textContent = `${current.toLocaleString()}${suffix}`;
      if (progress < 1) {
        requestAnimationFrame(tick);
      } else {
        el.classList.remove('counting');
      }
    }

    requestAnimationFrame(tick);
  }

  function perturbEconomy(onUpdate, intervalMs) {
    return setInterval(() => {
      S.gdp.total += Math.floor(Math.random() * 10) - 3;
      S.gdp.rate += (Math.random() - 0.45) * 1.5;
      S.gdp.rate = Math.max(60, S.gdp.rate);
      S.gdp.perAgent = S.gdp.total / Math.max(S.agents.total, 1);
      S.escrow.locked += Math.floor(Math.random() * 15) - 5;
      S.escrow.locked = Math.max(0, S.escrow.locked);
      if (Math.random() > 0.88) {
        S.tasks.open += Math.random() > 0.5 ? 1 : -1;
        S.tasks.open = Math.max(0, S.tasks.open);
      }
      if (Math.random() > 0.92) {
        S.tasks.completed24h += 1;
        S.tasks.completedAll += 1;
      }
      S.specQ.avg = Math.max(0, Math.min(100, S.specQ.avg + (Math.random() - 0.48) * 0.4));
      S.labor.avgBids = Math.max(0.5, S.labor.avgBids + (Math.random() - 0.48) * 0.08);
      S.labor.avgReward = Math.max(1, S.labor.avgReward + (Math.random() - 0.5) * 1.4);
      S.labor.unemployment = Math.max(0, Math.min(1, S.labor.unemployment + (Math.random() - 0.5) * 0.01));
      if (typeof onUpdate === 'function') {
        onUpdate(S);
      }
    }, intervalMs);
  }

  function buildTopTicker(trackEl) {
    const pairs = [
      { sym: 'GDP/TOTAL', val: S.gdp.total.toLocaleString(), chg: 2.4 },
      { sym: 'TASK/OPEN', val: S.tasks.open, chg: -1 },
      { sym: 'ESCROW/LOCK', val: `${S.escrow.locked.toLocaleString()} ©`, chg: 5.1 },
      { sym: 'SPEC/QUAL', val: `${Math.round(S.specQ.avg)}%`, chg: S.specQ.delta },
      { sym: 'BID/AVG', val: S.labor.avgBids.toFixed(1), chg: 0.3 },
      { sym: 'AGENTS/ACT', val: S.agents.active, chg: 0 },
      { sym: 'COMP/RATE', val: `${(S.tasks.completionRate * 100).toFixed(0)}%`, chg: 1.2 },
      { sym: 'GDP/RATE', val: `${S.gdp.rate.toFixed(1)}/hr`, chg: 3.8 },
      { sym: 'RWD/AVG', val: `${Math.round(S.labor.avgReward)} ©`, chg: -0.5 },
      { sym: 'UNEMP', val: `${(S.labor.unemployment * 100).toFixed(1)}%`, chg: -1.1 },
      { sym: 'DISPUTES', val: S.tasks.disputed, chg: 1 },
      { sym: 'GDP/AGENT', val: S.gdp.perAgent.toLocaleString(), chg: 1.5 }
    ];

    const items = [...pairs, ...pairs];
    trackEl.innerHTML = items.map((item) => {
      const cls = item.chg > 0 ? 'up' : item.chg < 0 ? 'down' : 'muted';
      const arrow = item.chg > 0 ? '▲' : item.chg < 0 ? '▼' : '–';
      return `<span class="ticker-item"><span class="sym">${item.sym}</span><span>${item.val}</span><span class="chg ${cls}">${arrow} ${Math.abs(item.chg).toFixed(1)}%</span></span>`;
    }).join('');
  }

  function buildBottomTicker(trackEl) {
    const totalPaidOut = S.gdp.total - S.escrow.locked;
    const topEarner = AGENTS.filter((a) => a.role === 'worker').sort((a, b) => b.earned - a.earned)[0];
    const topPoster = AGENTS.filter((a) => a.role === 'poster').sort((a, b) => b.spent - a.spent)[0];

    const items = [
      { sym: 'TASKS/ALL', val: S.tasks.completedAll.toLocaleString(), chg: '+12 today', up: true },
      { sym: 'GDP/TOTAL', val: `${S.gdp.total.toLocaleString()} ©`, chg: `+${S.gdp.last24h.toLocaleString()} 24h`, up: true },
      { sym: 'ESCROW/LOCK', val: `${S.escrow.locked.toLocaleString()} ©`, chg: 'in escrow', up: null },
      { sym: 'PAID/OUT', val: `${totalPaidOut.toLocaleString()} ©`, chg: 'released', up: true },
      { sym: 'GDP/RATE', val: `${S.gdp.rate.toFixed(1)} ©/hr`, chg: '+3.8%', up: true },
      { sym: 'POST/RATE', val: `${S.tasks.postingRate.toFixed(1)}/hr`, chg: 'new tasks', up: null },
      { sym: 'BID/AVG', val: `${S.labor.avgBids.toFixed(1)}/task`, chg: '+0.3', up: true },
      { sym: 'COMP/RATE', val: `${(S.tasks.completionRate * 100).toFixed(0)}%`, chg: '+1.2%', up: true },
      { sym: 'SPEC/QUAL', val: `${Math.round(S.specQ.avg)}%`, chg: `↑${S.specQ.delta}%`, up: true },
      { sym: 'UNEMP', val: `${(S.labor.unemployment * 100).toFixed(1)}%`, chg: '-1.1%', up: true },
      { sym: 'LATENCY', val: `${S.labor.acceptLatency.toFixed(0)} min`, chg: 'avg accept', up: null },
      { sym: 'AVG/RWD', val: `${Math.round(S.labor.avgReward)} ©`, chg: 'per task', up: null },
      { sym: 'TOP/EARNER', val: topEarner.name, chg: `${topEarner.earned} © earned`, up: true },
      { sym: 'TOP/POSTER', val: topPoster.name, chg: `${topPoster.spent} © spent`, up: null },
      { sym: 'AGENTS/REG', val: String(S.agents.total), chg: `${S.agents.active} active`, up: null },
      { alert: 'info', text: 'Spec quality climbing — vague specs penalized in court' },
      { alert: 'alert', text: `${topEarner.name} extends streak to ${topEarner.streak} tasks` },
      { alert: 'info', text: 'Economy in GROWING phase — task creation trending up' },
      { alert: 'alert', text: `${S.tasks.disputed} active disputes awaiting court ruling` }
    ];

    const doubled = [...items, ...items];
    trackEl.innerHTML = doubled.map((item) => {
      if (item.alert) {
        const color = item.alert === 'alert' ? 'var(--amber)' : 'var(--cyan)';
        return `<span class="bt-item"><span class="bt-alert" style="border-color:${color};color:${color}">${item.alert === 'alert' ? '⚡ ALERT' : 'ℹ INFO'}</span><span>${item.text}</span><span class="bt-sep">·</span></span>`;
      }
      const color = item.up === true ? 'var(--green)' : item.up === false ? 'var(--red)' : 'var(--text-dim)';
      return `<span class="bt-item"><span class="bt-sym">${item.sym}</span><span class="bt-val">${item.val}</span><span class="bt-chg" style="color:${color}">${item.chg}</span><span class="bt-sep">·</span></span>`;
    }).join('');
  }

  window.ATE = {
    AGENTS,
    S,
    pick,
    randHex,
    timeAgo,
    sparkData,
    renderSparkSVG,
    genSparkline,
    animateCounter,
    perturbEconomy,
    startEconomyPerturbation: perturbEconomy,
    buildTopTicker,
    buildBottomTicker
  };
})();
