// Home dashboard: reads data.json written by scripts/export.py and renders it.
// Odds differ by horizon (a year's odds run on a different base rate than a
// week's), so each chip asks for odds above that horizon's median by an offset,
// plus a spread cap. Lotto ignores odds and looks for names that are moving.
const RISK = {
  conservative: { oddsAbove: 0.02, maxSpread: 0.15 },
  balanced:     { oddsAbove: 0.0,  maxSpread: 0.5 },
  aggressive:   { oddsAbove: -1,   maxSpread: 2 },
  lotto:        { oddsAbove: -1,   maxSpread: 99 },
};
let data, risk = "balanced";

const $ = (s) => document.querySelector(s);
const fmt = (n, d = 2) => (n == null ? "—" : n.toFixed(d));
const sign = (n, d = 2) => (n > 0 ? "+" : "") + fmt(n, d);
const cls = (n) => (n > 0 ? "up" : n < 0 ? "down" : "dim");
const median = (a) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : 0.5; };

function passes(s, pool) {
  const r = RISK[risk];
  if (risk === "lotto") return s.volume_ratio >= 1.5 || Math.abs(s.change_pct) >= 4;
  const base = median((pool || data.most_volume).map((x) => x.p_up));
  return s.p_up >= base + r.oddsAbove && (s.spread_pct ?? 0) <= r.maxSpread && s.edge > 0;
}

function odds(s) {
  const pct = Math.round(s.p_up * 100);
  const dir = s.p_up >= 0.5 ? "↑" : "↓";
  const w = Math.round(Math.abs(s.p_up - 0.5) * 400);
  return `<span class="bar"><i style="width:${w}%"></i></span>${dir} ${pct}%`;
}

function renderStatus() {}

// ---- Freshness indicator: how old the newest data on screen is, and whether the market is open.
let lastTick = null;        // time of the newest live message from the relay
let lastDataTime = null;    // time stamped inside that message (exchange time)
let relayUp = false;

function marketOpen() {
  const et = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  const m = et.getHours() * 60 + et.getMinutes(), d = et.getDay();
  return d >= 1 && d <= 5 && m >= 570 && m < 960;   // 9:30-16:00 ET, Mon-Fri
}

function renderFresh() {
  const el = $("#fresh"), txt = $("#fresh-text");
  const open = marketOpen();
  if (!relayUp) {
    const age = (Date.now() - new Date(data.generated_at)) / 60000;
    const published = location.hostname.endsWith("github.io");
    // On the published site data refreshes every 5 min in market hours; that's "fresh", not "stale".
    el.className = "fresh " + (published && age <= 12 ? "live" : published && !open ? "closed" : "stale");
    // The second half is detail: phones show only how fresh the data is.
    txt.innerHTML = `updated ${age < 1 ? "just now" : age < 60 ? Math.round(age) + " min ago" : Math.round(age / 60) + " h ago"}` +
      `<span class="fx">${published ? (open ? " · refreshes every 5 min" : " · market closed") : " · relay offline"}</span>`;
    return;
  }
  const ageMs = lastTick ? Date.now() - lastTick : Infinity;
  const ageS = ageMs / 1000;
  if (!open) {
    el.className = "fresh closed";
    const et = lastDataTime ? new Date(lastDataTime).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET" : "";
    txt.textContent = `market closed · last print ${et} · board ${ageS < 5 ? "live" : Math.round(ageS) + "s ago"}`;
  } else if (ageS < 3) {
    el.className = "fresh live";
    txt.textContent = `live · ${ageMs < 1000 ? "<1s" : ageS.toFixed(1) + "s"} ago`;
  } else {
    el.className = "fresh stale";
    txt.textContent = `stale · ${Math.round(ageS)}s since last update`;
  }
}
setInterval(() => data && renderFresh(), 250);

function renderVolume(kind = "stocks") {
  const body = $("#vol-table tbody");
  $("#options-empty").hidden = kind !== "options";
  $("#vol-table").hidden = kind === "options";
  if (kind === "options") return;
  body.innerHTML = data.most_volume.map((s) => `
    <tr>
      <td class="sym">${s.symbol}<small>${s.sector}</small></td>
      <td>${fmt(s.price)}</td>
      <td class="${cls(s.change_pct)}">${sign(s.change_pct)}%</td>
      <td>${fmt(s.volume_ratio, 1)}×</td>
      <td>${odds(s)}</td>
      <td class="${cls(s.edge)}">${sign(s.edge)}%${s.spread_pct > 1 ? '<span class="thin">thin</span>' : ""}</td>
    </tr>`).join("");
}

// ---- Stocks page: every horizon, filtered by the risk chip.
const HORIZON_LABEL = { weekly: "1 week", monthly: "1 month", "6month": "6 months", long: "over 6 months" };
let horizon = "weekly";

function renderEdges() {
  const h = data.horizons[horizon];
  const onlyEdge = $("#only-edge").checked;
  $("#weights").textContent = "weights · " + Object.entries(h.weights).map(([k, v]) => `${k.replace("_surge", "")} ${sign(v, 2)}`).join(" · ");
  let rows = h.picks.filter((s) => passes(s, h.picks) || (!onlyEdge && risk !== "lotto"));
  if (onlyEdge) rows = rows.filter((s) => s.edge > 0 || risk === "lotto");
  $("#edge-empty").hidden = rows.length > 0;
  $("#edge-table tbody").innerHTML = rows.map((s, i) => `
    <tr class="${passes(s, h.picks) ? "hit" : "off"}">
      <td class="n">${String(i + 1).padStart(2, "0")}</td>
      <td class="sym">${s.symbol}<small>${s.sector}</small></td>
      <td>${fmt(s.price)} <span class="${cls(s.change_pct)}">${sign(s.change_pct, 1)}%</span></td>
      <td>${odds(s)}</td>
      <td class="${cls(s.expected_move)}">${sign(s.expected_move)}%</td>
      <td class="${s.spread_pct > 1 ? "down" : "dim"}">${fmt(s.spread_pct, 2)}%</td>
      <td class="${cls(s.edge)}">${sign(s.edge)}%</td>
      <td class="dim">${Object.entries(s.signals).map(([k, v]) => `${k.replace("_surge", "")} ${sign(v, 1)}`).join(" · ")}</td>
    </tr>`).join("");
  const n = h.picks.filter((s) => s.edge > 0).length;
  $("#stocks-foot").textContent = `Horizon ${HORIZON_LABEL[horizon]} · ${n} of ${h.picks.length} names with positive edge after spread · typical expected move ±${h.avg_abs_move}%` +
    (horizon === "long" ? " · long term currently uses price signals only; quality signals (growth, margins, valuation) are next" : "") +
    " · odds come from momentum and volume, which backtests show carry little edge alone; stronger signals are in progress";
}

// ---- Live flow: a tape of strings. Live events from the local relay go on top;
// the snapshot from data.json fills it until the relay connects.
const LABEL = { news: "NEWS", edge: "EDGE", volume: "VOL", trade: "TRADE", block: "BLOCK", hello: "LINK" };
const MAX_ROWS = 300;
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString([], { hour12: false }) : "");
const usd = (n) => (n >= 1e6 ? "$" + (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? "$" + (n / 1e3).toFixed(0) + "k" : "$" + n);

function flowRow(f) {
  let text = f.text, score = "";
  if (f.type === "trade" || f.type === "block") {
    text = `${f.price.toFixed(2)}  ×${f.size.toLocaleString()}`;
    score = usd(f.usd);
  } else if (f.score != null) {
    score = sign(f.score, 2);
  }
  const li = document.createElement("li");
  li.className = f.type;
  li.innerHTML = `<span class="t">${clock(f.time)}</span><span class="tag ${f.type}">${LABEL[f.type] || f.type.toUpperCase()}</span>
    <span class="sym">${esc(f.symbol)}</span><span class="txt" title="${esc(text)}">${esc(text)}</span>
    <span class="score ${f.score == null ? "" : cls(f.score)}">${score}</span>`;
  return li;
}

function renderFlow() {
  const list = $("#flow");
  list.innerHTML = "";
  // Only real events belong on the tape; the snapshot's engine picks live on the Stocks page.
  data.flow.filter((f) => f.type === "news").forEach((f) => list.appendChild(flowRow(f)));
}

// ---- Live board: every symbol ranked by % move, re-ranked each second.
const BOARD_N = 10;
let board = [], selected = null, lastRank = {};

function boardRow(r, i) {
  const li = document.createElement("li");
  li.dataset.symbol = r.symbol;
  li.className = (selected === r.symbol ? "sel " : "") + (lastRank[r.symbol] != null && lastRank[r.symbol] !== i ? "moved" : "");
  li.innerHTML = `<span class="rank">${String(i + 1).padStart(2, "0")}</span>
    <span class="sym">${r.symbol}<small>${r.sector}</small></span>
    <span class="chg ${cls(r.chg)}">${sign(r.chg)}%</span>
    <span class="rng"><i style="left:${(r.range_pos * 100).toFixed(0)}%"></i></span>`;
  li.onclick = () => { selected = selected === r.symbol ? null : r.symbol; renderBoard(); renderZone(); };
  return li;
}

function renderBoard() {
  const bull = board.filter((r) => r.chg > 0).slice(0, BOARD_N);
  const bear = board.filter((r) => r.chg < 0).slice(-BOARD_N).reverse();
  const fill = (el, rows) => { el.innerHTML = ""; rows.forEach((r, i) => el.appendChild(boardRow(r, i))); };
  fill($("#bull"), bull); fill($("#bear"), bear);
  lastRank = {}; bull.forEach((r, i) => (lastRank[r.symbol] = i)); bear.forEach((r, i) => (lastRank[r.symbol] = i));
}

function renderZone() {
  const z = $("#zone");
  const r = board.find((x) => x.symbol === selected);
  if (!r) { z.innerHTML = `<span class="dim">Click a ticker for its zone</span>`; return; }
  const ob = r.vwap ? `${(r.vwap * 0.9975).toFixed(2)}–${(r.vwap * 1.0025).toFixed(2)}` : "—";
  z.innerHTML = `<b>${r.symbol}</b><span class="${cls(r.chg)}">${fmt(r.price)} ${sign(r.chg)}%</span>
    <span><span class="k">Resistance</span>${fmt(r.high)}</span>
    <span><span class="k">Order block</span>${ob}</span>
    <span><span class="k">Support</span>${fmt(r.low)}</span>
    <span><span class="k">Vol</span>${r.vol_ratio == null ? "—" : fmt(r.vol_ratio, 1) + "× yday"}</span>`;
}

// With no relay, the board and the shape run from the export's snapshot rows.
function useSnapshotBoard() {
  if (relayUp || !data.board) return;
  board = data.board; renderBoard(); renderZone(); xraySetRows(board);
  const newest = board.reduce((m, r) => (r.time > m ? r.time : m), "");
  if (newest) lastDataTime = newest;
}

function connectLive() {
  const list = $("#flow"), badge = $("#live"), rate = $("#flow-rate");
  let count = 0, retry = 2000;
  const tick = () => { rate.textContent = count ? `${count} events/min` : rate.textContent; count = 0; };
  let timer;
  // The live relay only exists on the machine running scripts/run.py. The published site
  // runs on the 5-minute snapshot instead, so it doesn't try to connect at all.
  const PUBLISHED = location.hostname.endsWith("github.io");
  if (PUBLISHED) {
    badge.className = "live"; badge.lastElementChild.textContent = "5-min";
    rate.textContent = "ranked from the latest snapshot · refreshes every 5 min";
    useSnapshotBoard();
    return;
  }
  function open() {
    const ws = new WebSocket("ws://localhost:8766");
    ws.onopen = () => { badge.className = "live on"; badge.lastElementChild.textContent = "live"; retry = 2000; relayUp = true; timer = setInterval(tick, 60000); };
    ws.onmessage = (e) => {
      const f = JSON.parse(e.data);
      lastTick = Date.now();
      if (f.type === "board") {
        const newest = f.rows.reduce((m, r) => (r.time > m ? r.time : m), "");
        if (newest) lastDataTime = newest;
        board = f.rows; renderBoard(); renderZone(); xraySetRows(f.rows);
        if (paper.open.some((p) => p.type === "stock")) renderPaper();
        rate.textContent = `${board.length} names · ranked ${clock(f.time)}`;
        return;
      }
      const li = flowRow(f); li.classList.add("new");
      list.prepend(li); count++;
      if (list.children.length > MAX_ROWS) list.lastElementChild.remove();
    };
    ws.onclose = () => {
      badge.className = "live"; badge.lastElementChild.textContent = "snapshot"; clearInterval(timer); relayUp = false;
      rate.textContent = "relay offline · showing the latest snapshot · run scripts/run.py for live";
      useSnapshotBoard();
      setTimeout(open, retry); retry = Math.min(retry * 2, 30000);
    };
    ws.onerror = () => ws.close();
  }
  open();
}

function renderSector(sec) {
  const el = $("#sector-detail");
  if (!sec) { el.innerHTML = `<span class="dim">Hover a peak · click to pin</span>`; return; }
  el.innerHTML = `<span class="name">${sec.name}</span>
    <span class="pick dim">score ${sec.score} · momentum ${sign(sec.momentum, 1)} · vol ${fmt(sec.volume, 1)}×</span>` +
    sec.top.map((s) => `<span class="pick"><b>${s.symbol}</b>${Math.round(s.p_up * 100)}% <span class="${cls(s.edge)}">${sign(s.edge)}%</span></span>`).join("");
}

// ---- Shape of the market: every name as a bubble placed by today's % move, in its
// sector's lane, sized by volume. Live off the relay board; the snapshot until then.
// The distribution IS the shape: a tight bell when calm, a lean when risk is on or
// off, a lump breaking away when a sector rotates.
const xray = { nodes: new Map(), lanes: [], hover: null, raf: 0 };

function xraySetRows(rows) {
  const lanes = [...new Set(rows.map((r) => r.sector))].sort((a, b) => a.localeCompare(b));
  xray.lanes = lanes;
  rows.forEach((r) => {
    const n = xray.nodes.get(r.symbol) || { x: 0, y: 0, vx: 0, vy: 0 };
    Object.assign(n, { symbol: r.symbol, sector: r.sector, chg: r.chg ?? r.change_pct ?? 0, price: r.price,
                       vol: r.vol_ratio ?? r.volume_ratio ?? 1, high: r.high, low: r.low });
    xray.nodes.set(r.symbol, n);
  });
  for (const s of [...xray.nodes.keys()]) if (!rows.some((r) => r.symbol === s)) xray.nodes.delete(s);
  renderShapeStats(rows);
}

function renderShapeStats(rows) {
  const chg = rows.map((r) => r.chg ?? r.change_pct ?? 0);
  const up = chg.filter((c) => c > 0).length, dn = chg.filter((c) => c < 0).length;
  const sorted = [...chg].sort((a, b) => a - b), med = sorted[Math.floor(sorted.length / 2)] || 0;
  const spread = sorted.length ? sorted[Math.floor(sorted.length * 0.9)] - sorted[Math.floor(sorted.length * 0.1)] : 0;
  const bySector = {};
  rows.forEach((r) => (bySector[r.sector] ||= []).push(r.chg ?? r.change_pct ?? 0));
  const lead = Object.entries(bySector).map(([s, a]) => [s, a.reduce((x, y) => x + y, 0) / a.length]).sort((a, b) => b[1] - a[1]);
  const shape = spread < 2 ? "tight" : spread < 5 ? "normal" : "wide";
  const lean = med > 0.3 ? "leaning up" : med < -0.3 ? "leaning down" : "centred";
  $("#sector-detail").innerHTML = `
    <div class="stat"><b class="${cls(up - dn)}">${up} / ${dn}</b><span>up / down</span></div>
    <div class="stat"><b class="${cls(med)}">${sign(med, 2)}%</b><span>median move</span></div>
    <div class="stat"><b>${spread.toFixed(1)}%</b><span>spread · ${shape}, ${lean}</span></div>
    ${lead.length ? `<div class="stat"><b class="up">${lead[0][0]}</b><span>leading ${sign(lead[0][1], 1)}%</span></div>
    <div class="stat"><b class="down">${lead[lead.length - 1][0]}</b><span>lagging ${sign(lead[lead.length - 1][1], 1)}%</span></div>` : ""}`;
}

function buildShape() {
  const host = $("#shape"), canvas = $("#xray"), tip = $("#xray-tip"), ctx = canvas.getContext("2d");
  if (!xray.nodes.size) {
    const rows = (data.horizons?.weekly?.picks || data.most_volume).map((s) => ({ ...s, chg: s.change_pct, vol_ratio: s.volume_ratio }));
    xraySetRows(rows);
  }
  let W = 0, H = 0, dpr = Math.min(devicePixelRatio, 2), mouse = null;
  const resize = () => { W = host.clientWidth; H = host.clientHeight; canvas.width = W * dpr; canvas.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0); };
  new ResizeObserver(resize).observe(host); resize();
  host.onmousemove = (e) => { const r = canvas.getBoundingClientRect(); mouse = { x: e.clientX - r.left, y: e.clientY - r.top }; tip.style.left = mouse.x + "px"; tip.style.top = mouse.y + "px"; };
  host.onmouseleave = () => { mouse = null; tip.hidden = true; };

  const PAD = 56, LANE_LABEL = 92;
  const xOf = (chg, range) => LANE_LABEL + ((Math.max(-range, Math.min(range, chg)) + range) / (2 * range)) * (W - LANE_LABEL - PAD / 2);

  cancelAnimationFrame(xray.raf);
  (function frame() {
    xray.raf = requestAnimationFrame(frame);
    if (!W || !H) return;
    const nodes = [...xray.nodes.values()], lanes = xray.lanes;
    // Scale to the bulk of the market (95th percentile), so one outlier doesn't flatten the bell.
    const abs = nodes.map((n) => Math.abs(n.chg)).sort((a, b) => a - b);
    const p95 = abs[Math.floor(abs.length * 0.95)] || 1;
    const range = Math.max(2, Math.min(10, Math.ceil(p95 * 1.3)));
    const laneH = (H - 24) / Math.max(lanes.length, 1);
    const rOf = (n) => 4 + Math.min(10, Math.sqrt(Math.max(n.vol, 0.2)) * 4);

    // Targets, then gentle physics so bubbles glide and don't overlap.
    nodes.forEach((n) => {
      n.tx = xOf(n.chg, range); n.ty = 12 + (lanes.indexOf(n.sector) + 0.5) * laneH; n.r = rOf(n);
      if (!n.init) { n.x = n.tx; n.y = n.ty; n.init = true; }
      n.vx += (n.tx - n.x) * 0.08; n.vy += (n.ty - n.y) * 0.08;
    });
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j], dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 0.01, min = a.r + b.r + 1.5;
      if (d < min) { const push = (min - d) / d * 0.25; a.vx -= dx * push; a.vy -= dy * push; b.vx += dx * push; b.vy += dy * push; }
    }
    nodes.forEach((n) => { n.vx *= 0.6; n.vy *= 0.6; n.x += n.vx; n.y += n.vy; });

    // Draw.
    ctx.clearRect(0, 0, W, H);
    ctx.font = "11px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle";
    lanes.forEach((s, i) => {
      const y = 12 + (i + 0.5) * laneH;
      ctx.fillStyle = i % 2 ? "rgba(255,255,255,0.025)" : "transparent"; ctx.fillRect(LANE_LABEL, y - laneH / 2, W - LANE_LABEL, laneH);
      ctx.fillStyle = "#8e8e93"; ctx.textAlign = "left"; ctx.fillText(s, 14, y);
    });
    const zero = xOf(0, range);
    ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(zero, 6); ctx.lineTo(zero, H - 18); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#8e8e93"; ctx.textAlign = "center";
    [-range, -range / 2, 0, range / 2, range].forEach((v) => ctx.fillText((v > 0 ? "+" : "") + v + "%", xOf(v, range), H - 9));
    let hit = null;
    nodes.forEach((n) => {
      const up = n.chg >= 0, alpha = 0.55 + Math.min(0.45, Math.abs(n.chg) / range);
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = up ? `rgba(0,200,5,${alpha})` : `rgba(255,80,0,${alpha})`; ctx.fill();
      if (Math.abs(n.chg) > range) {   // pinned at the edge: ring it and label the real move
        ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = "#fff"; ctx.textAlign = up ? "right" : "left"; ctx.font = "10px Inter, system-ui, sans-serif";
        ctx.fillText(`${n.symbol} ${sign(n.chg, 1)}%`, n.x + (up ? -n.r - 4 : n.r + 4), n.y); ctx.font = "11px Inter, system-ui, sans-serif";
      }
      if (mouse && Math.hypot(mouse.x - n.x, mouse.y - n.y) <= n.r + 2) hit = n;
    });
    if (hit) {
      ctx.beginPath(); ctx.arc(hit.x, hit.y, hit.r + 2, 0, Math.PI * 2); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      tip.hidden = false;
      tip.innerHTML = `<b>${hit.symbol} <span class="${cls(hit.chg)}">${sign(hit.chg, 2)}%</span></b><span>${hit.sector} · ${money(hit.price)} · ${fmt(hit.vol, 1)}× volume</span>`;
    } else tip.hidden = true;
    host.style.cursor = hit ? "pointer" : "default";
  })();
}

// ---- Three tiers (Stocks and Options pages): will / maybe / lotto, each by horizon.
const tierTab = { "stocks-will": "month", "stocks-maybe": "month", "options-will": "month", "options-maybe": "month", "options-lotto": "weekly",
                  "spx-will": "weekly", "spx-maybe": "weekly", "spx-lotto": "weekly" };
const LOTTO_DESC = { weekly: "expiring within two weeks, 8–25% out of the money, under $60 a contract",
                     month: "expiring in 3–7 weeks, 12–35% out, under $150 a contract",
                     years: "LEAPs a year or more out, 30–80% out, under $300 a contract — a long-dated bet on a re-rating" };
const table = (head, rows, empty) => rows.length
  ? `<table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`
  : `<p class="empty">${empty}</p>`;
const ctr = (c) => c ? `<td class="ctr"><b>${c.underlying} ${c.expiry.slice(5)} ${c.strike}${c.type[0].toUpperCase()}</b> · ask ${fmt(c.ask)} · ${c.dte}d</td>` : `<td class="dim">no quote</td>`;

// Robinhood-style rows. A contract reads "MA $730 Call · Exp Oct 30", price per contract on the right.
const money = (n) => "$" + (Math.abs(n) >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 0 }) : n.toFixed(2));
const expLabel = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const list = (items, empty) => items.length ? `<ul class="rh">${items.join("")}</ul>` : `<p class="empty">${empty}</p>`;
const rhRow = ({ title, tag, sub, value, delta, deltaCls, hit, action }) => `
  <li class="${hit ? "hit" : ""}"><div class="l"><div class="t">${title}${tag || ""}</div><div class="s">${sub}</div></div>
  <div class="r"><div class="v">${value}</div><div class="d ${deltaCls || "dim"}">${delta || ""}</div></div>${action ? `<div class="act">${action}</div>` : ""}</li>`;

// ---- Paper trading: positions picked from the tiers, kept in this browser, marked to the latest prices.
const PAPER_KEY = "raidical.paper";
let paper = { open: [], closed: [] };
try { paper = JSON.parse(localStorage.getItem(PAPER_KEY)) || paper; } catch {}
const savePaper = () => { try { localStorage.setItem(PAPER_KEY, JSON.stringify(paper)); } catch {} };
const paperId = (p) => p.type === "stock" ? `S:${p.symbol}` : `O:${p.contract}`;
const isHeld = (id) => paper.open.some((p) => paperId(p) === id);

function tradeButton(id, payload) {
  return isHeld(id) ? `<button class="held" disabled>Holding</button>` : `<button data-trade='${esc(JSON.stringify(payload))}'>Paper trade</button>`;
}
function stockTrade(s, tier) {
  return tradeButton(`S:${s.symbol}`, { type: "stock", symbol: s.symbol, entry: s.price, qty: 100, tier, note: (s.for || []).slice(0, 2).join(", ") });
}
function contractTrade(c, tier) {
  if (!c) return "";
  return tradeButton(`O:${c.contract}`, { type: "option", symbol: c.underlying, contract: c.contract, label: `${c.underlying} $${c.strike} ${c.type === "call" ? "Call" : "Put"} ${expLabel(c.expiry)}`,
    entry: c.ask, qty: 1, tier, expiry: c.expiry, kind: c.type, strike: c.strike });
}
function openPaper(payload) {
  if (isHeld(paperId(payload))) return;
  paper.open.push({ ...payload, opened: new Date().toISOString() });
  savePaper(); renderTiers(); renderPaper();
}
function closePaper(idx) {
  const p = paper.open.splice(idx, 1)[0];
  const m = markOf(p);
  paper.closed.unshift({ ...p, exit: m.price, closed: new Date().toISOString(), pnl: m.pnl, pnl_pct: m.pnl_pct });
  savePaper(); renderTiers(); renderPaper();
}
function markOf(p) {
  let price = null, stale = false;
  if (p.type === "stock") {
    const live = board.find((r) => r.symbol === p.symbol);
    price = live ? live.price : data.marks?.[p.symbol];
    stale = !live;
  } else if (p.type === "spread") {
    // Multi-leg SPX structure: mark every leg at mid; entry is the net (credit +, debit −).
    const qs = p.legs.map((l) => data.contract_marks?.[l.contract]);
    price = qs.every((q) => q && q.mid != null) ? qs.reduce((n, q, i) => n + p.legs[i].side * q.mid, 0) : null;
    stale = true;
  } else {
    const q = data.contract_marks?.[p.contract];
    price = q ? (q.bid || q.mid) : null;   // close at the bid: what you'd actually get
    stale = true;
  }
  if (price == null) return { price: p.entry, pnl: 0, pnl_pct: 0, unknown: true };
  const mult = p.type === "stock" ? 1 : 100;
  const pnl = (price - p.entry) * p.qty * mult;
  const basis = p.type === "spread" ? Math.abs(p.risk || p.entry) : p.entry;
  return { price, pnl, pnl_pct: basis ? pnl / (basis * p.qty * mult) * 100 : 0, stale };
}

function renderPaper() {
  const rows = paper.open.map((p, i) => ({ p, i, m: markOf(p) }));
  const openPnl = rows.reduce((s, r) => s + r.m.pnl, 0);
  const cost = rows.reduce((s, r) => s + (r.p.type === "spread" ? Math.abs(r.p.risk || r.p.entry) : r.p.entry) * r.p.qty * (r.p.type === "stock" ? 1 : 100), 0);
  const realized = paper.closed.reduce((s, c) => s + (c.pnl || 0), 0);
  const wins = paper.closed.filter((c) => c.pnl > 0).length;
  $("#paper-stats").innerHTML = `
    <div><span class="k">open positions</span><b>${rows.length}</b></div>
    <div><span class="k">open P&amp;L</span><b class="${cls(openPnl)}">${sign(openPnl, 0).replace(/^([+-])/, "$1$")}</b></div>
    <div><span class="k">realised</span><b class="${cls(realized)}">${sign(realized, 0).replace(/^([+-])/, "$1$")}</b></div>
    <div><span class="k">closed · hit rate</span><b>${paper.closed.length}${paper.closed.length ? ` · ${Math.round(wins / paper.closed.length * 100)}%` : ""}</b></div>
    <div><span class="k">capital in play</span><b>${money(cost)}</b></div>`;
  $("#paper-open").innerHTML = rows.length ? `<ul class="rh">${rows.map(({ p, i, m }) => rhRow({
    title: p.type === "stock" ? p.symbol : p.label,
    tag: `<span class="pill${p.kind ? " " + p.kind : ""}">${p.type === "stock" ? `${p.qty} sh` : `${p.qty} ctr`}</span><small>${p.tier || ""}</small>`,
    sub: `in at ${money(p.entry)} on ${new Date(p.opened).toLocaleDateString()}${p.note ? " · " + p.note : ""}${m.unknown ? " · no mark yet" : m.stale ? " · last export mark" : " · live"}`,
    value: money(m.price), delta: `${sign(m.pnl, 0).replace(/^([+-])/, "$1$")} · ${sign(m.pnl_pct, 1)}%`, deltaCls: cls(m.pnl),
    action: `<button class="close" data-close="${i}">Close</button>`,
  })).join("")}</ul>` : `<p class="empty">No paper positions. Go to Stocks or Options and press <b>Paper trade</b> on any signal.</p>`;
  $("#paper-closed").innerHTML = paper.closed.length ? `<ul class="rh">${paper.closed.map((c) => rhRow({
    title: c.type === "stock" ? c.symbol : c.label, tag: `<small>${c.tier || ""}</small>`,
    sub: `${money(c.entry)} → ${money(c.exit)} · ${new Date(c.opened).toLocaleDateString()} → ${new Date(c.closed).toLocaleDateString()}`,
    value: `${sign(c.pnl, 0).replace(/^([+-])/, "$1$")}`, delta: `${sign(c.pnl_pct, 1)}%`, deltaCls: cls(c.pnl),
  })).join("")}</ul>` : `<p class="empty dim">Nothing closed yet.</p>`;
}

function wirePaper() {
  document.body.addEventListener("click", (e) => {
    const t = e.target.closest("[data-trade]");
    if (t) { openPaper(JSON.parse(t.dataset.trade)); return; }
    const c = e.target.closest("[data-close]");
    if (c) closePaper(+c.dataset.close);
  });
  $("#paper-export").onclick = () => {
    const blob = new Blob([JSON.stringify(paper, null, 1)], { type: "application/json" });
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "paper-trades.json" }); a.click();
  };
  $("#paper-reset").onclick = () => { if (confirm("Clear all paper positions and history?")) { paper = { open: [], closed: [] }; savePaper(); renderTiers(); renderPaper(); } };
}

function contractRow(p, c, extra = "", hit = false, tier = "") {
  if (!c) return rhRow({ title: p.symbol, sub: "no contract with a live quote", value: "—" });
  const kind = c.type === "call" ? "Call" : "Put";
  const be = c.strike + (c.type === "call" ? c.ask : -c.ask);
  return rhRow({
    title: `${c.underlying} $${c.strike} ${kind}`,
    tag: `<span class="pill ${c.type}">${kind}</span>`,
    sub: `Exp ${expLabel(c.expiry)} · ${c.dte}d · break-even ${money(be)} (${sign(c.breakeven_move_pct, 1)}%)${extra ? " · " + extra : ""}`,
    value: money(c.ask * 100), delta: `${fmt(c.ask)} × 100 · spot ${money(c.spot)}`, hit,
    action: contractTrade(c, tier),
  });
}

// Layered-engine rows: what argues for the name, what argues against, and the 40-day expectation.
const layerSub = (v) => [
  v.for?.length ? `<span class="up">+</span> ${v.for.join(" · ")}` : "",
  v.against?.length ? `<span class="down">−</span> ${v.against.join(" · ")}` : "",
].filter(Boolean).join(" &nbsp; ");

function peadRow(p, E) {
  const pending = p.status === "pending";
  return rhRow({
    title: p.symbol, tag: `<span class="pill">Earnings drift</span>${pending ? `<span class="pill pend">enters at close</span>` : ""}`,
    sub: `${layerSub(p)} · ${pending ? `reported ${expLabel(p.announced)} · buys the next close, holds ${E.hold_days} days` : `in at ${money(p.entry)} ${expLabel(p.entry_date)} · ${p.days_left} of ${E.hold_days} days left`}`,
    value: money(p.price), delta: pending ? `expects ${sign(p.expected_40d, 1)}% in 40d` : `${sign(p.excess)}% vs SPY · expects ${sign(p.expected_40d, 1)}% in 40d`, deltaCls: pending ? "up" : cls(p.excess), hit: true,
    action: stockTrade(p, "will"),
  });
}
function maybeRow(s) {
  if (s.expected_40d != null) {   // layered verdict
    return rhRow({
      title: s.symbol, tag: `<small>${s.sector}</small>${s.announced ? `<span class="pill">Earnings drift</span>` : ""}`,
      sub: layerSub(s),
      value: money(s.price), delta: `expects ${sign(s.expected_40d, 1)}% in 40d`, deltaCls: cls(s.expected_40d),
      action: stockTrade(s, "maybe"),
    });
  }
  const pct = Math.round(Math.max(s.p_up, 1 - s.p_up) * 100);
  return rhRow({
    title: s.symbol, tag: `<small>${s.sector}</small>`,
    sub: `${s.p_up >= 0.5 ? "↑" : "↓"} ${pct}% odds · expected ${sign(s.expected_move)}% · edge ${sign(s.edge)}% after spread · price engine only`,
    value: money(s.price), delta: `${sign(s.change_pct, 1)}% today`, deltaCls: cls(s.change_pct),
    action: stockTrade(s, "maybe"),
  });
}

// Index funds: the only thing that earned "Will". Odds are its own history, in today's market state.
function indexRow(r) {
  const per = { 5: "one-week", 21: "one-month", 252: "one-year" }[r.horizon_days] || "";
  return rhRow({
    title: r.symbol, tag: `<span class="pill">${r.name}</span><span class="pill ${r.uptrend ? "call" : "put"}">${r.uptrend ? "above" : "below"} its 200-day</span>`,
    sub: `gained in ${fmt(r.odds, 0)}% of ${per} holds begun in this state (${r.n.toLocaleString()} start days since ${r.since.slice(0, 4)}) · typical ${sign(r.avg, 1)}% · worst tenth ${sign(r.p10, 1)}%, best tenth ${sign(r.p90, 1)}% · any state: ${fmt(r.odds_all, 0)}%`,
    value: money(r.price), delta: `${fmt(r.odds, 0)}% odds · ${sign(r.avg, 1)}% typical`, deltaCls: "up", hit: true,
    action: stockTrade({ ...r, for: [`${fmt(r.odds, 0)}% odds`] }, "will"),
  });
}
// A model pick: its drivers in plain words, then what names scored like it actually did out of sample.
function brainRow(s, tier) {
  return rhRow({
    title: s.symbol, tag: `<small>${s.sector || ""}</small>${s.days_to_next != null ? `<span class="pill pend">reports in ${s.days_to_next}d</span>` : ""}`,
    sub: `${layerSub(s)}<br><span class="dim">names scored like this: gained ${fmt(s.odds, 0)}% of the time · ${sign(s.exp, 1)}% vs SPY · usual range ${sign(s.p10, 0)}% to ${sign(s.p90, 0)}%</span>`,
    value: money(s.price), delta: `${fmt(s.odds, 0)}% odds · ${sign(s.abs, 1)}% typical`, deltaCls: cls(s.abs), action: stockTrade(s, tier),
  });
}

function renderTiers() {
  const T = data.tiers, B = data.brain; if (!T) return;
  const span = { weekly: "week", month: "month", years: "year" };

  // Stocks · will: the index, with its measured odds
  const sw = tierTab["stocks-will"];
  $("#stocks-will").innerHTML = list((T.stocks.will[sw] || []).map(indexRow), "Index history not loaded in this snapshot.");
  if (B) {
    const m = B.month, d = B.earnings_drift;
    $("#pead-foot").textContent = `Why only index funds: on years the model had never seen (${B.test_years[0]}–${B.test_years[1]}), its 20 favourite stocks gained in ${m.picks_gained}% of months; SPY gained in ${m.spy_gained}%. ` +
      `No stock-picking rule we tested beat simply holding the index on odds of a gain, across ${B.rows.weekly.toLocaleString()} stock-periods and ${B.symbols.toLocaleString()} companies, listed and delisted. ` +
      (d ? `That includes the earnings-drift rule this tier used to show: ${d.trades.toLocaleString()} trades across the whole market made ${sign(d.net_vs_spy, 2)}% against SPY, not the +4.7% first measured on 234 hand-picked survivors.` : "");
  }

  // Stocks · maybe: the model's picks
  const sm = tierTab["stocks-maybe"];
  $("#stocks-maybe").innerHTML = sm === "years"
    ? `<p class="empty">No model beat the index at six months or longer, so nothing is shown. Company fundamentals from SEC filings are the missing input for this horizon.</p>`
    : list((T.stocks.maybe[sm] || []).map((s) => brainRow(s, "maybe")), "The brain has not scored this snapshot yet.");
  if (B) {
    const k = sm === "weekly" ? B.week : B.month;
    $("#maybe-foot").textContent = sm === "years" ? "" :
      `The model's top 20 liquid names for the next ${span[sm]}. Out of sample they made ${sign(k.top20_net_vs_spy, 2)}% a ${span[sm]} against SPY after costs (t=${k.top20_t}, ahead in ${k.years_positive} of ${k.years} years): ` +
      (Math.abs(k.top20_t) >= 3 ? "a real edge." : k.top20_t >= 1 ? "a lean, not proof." : "no edge; shown as a ranking only.") +
      ` Odds and ranges on each row are what names with that score actually did.`;
  }

  // Stocks · lotto
  $("#stocks-lotto").innerHTML = list(T.stocks.lotto.map((s) => rhRow({
    title: s.symbol, tag: `<span class="pill">${s.reasons[0]}</span>`,
    sub: `${s.reasons.slice(1).join(" · ") || "catalyst this week"} · ${fmt(s.volume_ratio, 1)}× normal volume${s.max_move ? ` · biggest earnings move ${fmt(s.max_move, 0)}%` : ""}`,
    value: money(s.price), delta: `${sign(s.change_pct, 1)}% today`, deltaCls: cls(s.change_pct),
    action: stockTrade(s, "lotto"),
  })), "No catalysts this week.");

  // Options · will: defined-risk index premium, the one measured option edge
  const ow = tierTab["options-will"];
  $("#options-will").innerHTML = ow === "years"
    ? `<p class="empty">The premium edge is short-dated: implied vol beats realised over days and weeks, not years. Nothing here on purpose.</p>`
    : list((T.options.will[ow] || []).map((s) => spxRow(s, "will")), "No index condor with a full quote at the 1σ strikes right now.");
  const E = data.spx && data.spx.evidence;
  $("#options-will-foot").textContent = E
    ? `Measured: since ${E.since.slice(0, 4)}, the market's implied volatility was above what the index then realised on ${E.share}% of days, by ${E.gap} points on average. Selling both 1σ strikes collects that gap; the wings cap the loss. Same structure on SPX, sized 10×, is on the SPX tab. Prices are indicative (free feed).`
    : "Defined-risk premium selling on the index. Prices are indicative (free feed).";

  // Options · maybe: best contract by expected value on each model pick
  const om = tierTab["options-maybe"], skipped = (T.options.maybe_skipped || {})[om] || 0;
  $("#options-maybe").innerHTML = om === "years"
    ? `<p class="empty">No stock model for six months or longer yet, so no contracts either.</p>`
    : list((T.options.maybe[om] || []).map((p) => contractRow(p, p.contract,
        `expected value ${sign(p.contract.ev_pct, 0)}% on premium · ${p.contract.p_profit}% chance of profit · priced at ${p.contract.iv}% vol, stock has realised ${p.contract.realised_vol}% · plan to hold ~${p.contract.hold_days}d`, false, "maybe")),
        `Priced the best contract on each of the model's picks (${skipped} checked): none has positive expected value today. The options cost more than the edge is worth, so shares are the better way to take these.`);

  // Options · lotto
  const ol = tierTab["options-lotto"];
  const lottoRows = Array.isArray(T.options.lotto) ? T.options.lotto : (T.options.lotto[ol] || []);
  $("#lotto-foot").textContent = `${LOTTO_DESC[ol]}, on names with a catalyst. Most expire worthless; the point is the payoff if the move lands. "Needs" is how far the stock must move by expiry. Judge lottos as a group, never one at a time.`;
  $("#options-lotto").innerHTML = list(lottoRows.map((p) => contractRow(p, p.contract, p.reasons.join(" · "), false, "lotto")), "No cheap contracts on this week's catalysts.");
  renderBrain();
}

function renderBrain() {
  const B = data.brain; if (!B || !$("#brain-scored")) return;
  const m = B.month;
  $("#brain-asof").textContent = `scored through ${B.asof}`;
  $("#brain-scored").textContent = B.tradable.toLocaleString();
  $("#brain-rows").textContent = (B.rows.weekly / 1e6).toFixed(1) + "M";
  $("#brain-edge").textContent = `${sign(m.top20_net_vs_spy, 2)}%`;
  $("#brain-edge").className = cls(m.top20_net_vs_spy);
  const verdict = Math.abs(m.top20_t) >= 3 ? "edge" : m.top20_t >= 1 ? "lean" : "no edge";
  $("#brain-verdict").textContent = `${verdict} · t=${m.top20_t}`;
  $("#brain-verdict").className = verdict === "edge" ? "up" : "dim";
  const av = (data.tiers.stocks.avoid || []).slice(0, 10).map((r) => r.symbol).join(" · ");
  $("#brain-foot").textContent = `A gradient-boosted model on ${B.features} inputs (price, volume, earnings history, industry, short interest), trained on every US company ${B.span[0].slice(0, 4)}–${B.span[1].slice(0, 4)} and judged only on years it had not seen. ` +
    `Where it is strongest is saying no: the tenth it ranks lowest gained in just ${m.avoid_gained}% of months and trailed SPY by ${Math.abs(m.avoid_vs_spy)}% a month.` + (av ? ` On that list now: ${av}.` : "") +
    ` Retrains itself every weekend on the full history and only replaces the model in service if the new one holds up; last retrain ${new Date(B.trained_at).toLocaleDateString()}${(B.history || []).length > 1 ? `, ${B.history.length} on record` : ""}.`;
}
const renderPead = renderTiers;

// ---- SPX desk: the index option market's own numbers, and three tiers of SPX structures.
const legStr = (l) => `${l.side > 0 ? "+" : "−"}${l.strike}${l.type === "call" ? "C" : "P"}`;
function spxTrade(s, tier) {
  // Entry is the net at mid as a signed position value (debit paid −, credit received +); risk is the max loss.
  const net = s.legs.reduce((n, l) => n + l.side * l.mid, 0);
  const id = `X:${s.legs.map((l) => l.contract).join("|")}`;
  return tradeButton(id, { type: "spread", symbol: s.underlying || "SPX", label: s.name, legs: s.legs.map((l) => ({ contract: l.contract, side: l.side })),
    entry: net, risk: s.max_loss, qty: 1, tier, expiry: s.expiry, kind: s.kind });
}
function spxRow(s, tier) {
  const credit = s.credit != null;
  const sub = [
    `Exp ${expLabel(s.expiry)} · ${s.dte}d`,
    s.pop != null ? `${s.pop}% chance of max profit` : "",
    s.ror != null ? (credit ? `${s.ror}% return on risk` : `pays +${s.ror}% if it lands`) : "",
    s.lo != null ? `profit zone ${money(s.lo)} – ${money(s.hi)} (${sign(s.lo_pct, 1)}% / ${sign(s.hi_pct, 1)}%)` : "",
    s.breakeven != null ? `break-even ${money(s.breakeven)} (needs ${sign(s.needs_pct, 1)}%)` : "",
    s.note || "",
  ].filter(Boolean).join(" · ");
  return rhRow({
    title: s.name, tag: `<span class="pill ${/put/i.test(s.kind) ? "put" : /call/i.test(s.kind) ? "call" : ""}">${s.kind}</span><small>${s.legs.map(legStr).join(" ")}</small>`,
    sub,
    value: credit ? `+${money(s.credit * 100)}` : money(s.debit * 100), deltaCls: credit ? "up" : "",
    delta: credit ? `credit · risk ${money(s.max_loss * 100)}` : s.max_gain != null ? `debit · max ${money(s.max_gain * 100)}` : `debit${s.delta != null ? " · " + Math.abs(s.delta * 100).toFixed(0) + "Δ" : ""}`,
    hit: tier === "will", action: spxTrade(s, tier),
  });
}
function renderSpx() {
  const X = data.spx; if (!X) { $("#spx-stats").innerHTML = `<p class="empty">SPX desk not in this snapshot yet.</p>`; return; }
  $("#spx-asof").textContent = `Cboe · 15-min delayed · ${X.as_of ? expLabel(X.as_of.slice(0, 10)) + " " + X.as_of.slice(11, 16) + " ET" : ""}`;
  const L = X.lean, E = X.evidence, W = X.walls || {};
  $("#spx-stats").innerHTML = `
    <div><span class="k">SPX</span><b>${money(X.spot)}</b></div>
    <div><span class="k">today</span><b class="${cls(X.change_pct)}">${sign(X.change_pct, 2)}%</b></div>
    <div><span class="k">range</span><b>${money(X.low)} – ${money(X.high)}</b></div>
    <div><span class="k">VIX</span><b>${fmt(X.vix, 1)}</b></div>
    <div><span class="k">30-day implied</span><b>${fmt(X.iv30, 1)}%</b></div>
    <div><span class="k">lean</span><b class="${L.dir === "up" ? "up" : L.dir === "down" ? "down" : "dim"}">${L.dir === "up" ? "↑ up" : L.dir === "down" ? "↓ down" : "flat"}</b></div>`;
  $("#spx-moves").innerHTML = `<ul class="rh">${(X.expected || []).map((m) => rhRow({
    title: `${m.label[0].toUpperCase() + m.label.slice(1)} · ${expLabel(m.expiry)}`, tag: `<small>${m.dte}d · IV ${fmt(m.iv, 1)}%</small>`,
    sub: `straddle ${money(m.straddle)} at the ${money(m.atm)} strike · 1σ strikes ${money(m.d16_lo)} / ${money(m.d16_hi)}`,
    value: `±${money(m.move)}`, delta: `±${fmt(m.move_pct, 2)}% · ${money(m.lo)} – ${money(m.hi)}`,
  })).join("")}</ul>`;

  const bar = (oi, max) => `<i class="bar" style="width:${Math.round(oi / max * 100)}%"></i>`;
  const max = Math.max(...(W.calls || []).map((x) => x.oi), ...(W.puts || []).map((x) => x.oi), 1);
  $("#walls-hint").textContent = W.through ? `open interest through ${expLabel(W.through)} · ${(W.total_oi / 1e6).toFixed(1)}M contracts` : "";
  $("#spx-walls").innerHTML = W.calls ? `
    <div class="learn-stats">
      <div><span class="k">put / call OI</span><b>${fmt(W.pc_ratio, 2)}</b></div>
      <div><span class="k">max pain ${expLabel(W.next_expiry)}</span><b>${money(W.max_pain)}</b></div>
      <div><span class="k">most gamma</span><b>${money(W.gamma_strike)}</b></div>
    </div>
    <ul class="rh">
      ${W.calls.map((x) => rhRow({ title: money(x.strike), tag: `<span class="pill call">Call wall</span>`, sub: `${sign((x.strike / X.spot - 1) * 100, 1)}% above${bar(x.oi, max)}`, value: `${(x.oi / 1000).toFixed(0)}k`, delta: "open interest" })).join("")}
      ${W.puts.map((x) => rhRow({ title: money(x.strike), tag: `<span class="pill put">Put wall</span>`, sub: `${sign((x.strike / X.spot - 1) * 100, 1)}% below${bar(x.oi, max)}`, value: `${(x.oi / 1000).toFixed(0)}k`, delta: "open interest" })).join("")}
    </ul>` : `<p class="empty">No open-interest data in this snapshot.</p>`;

  const T = X.tiers;
  const w = tierTab["spx-will"];
  $("#spx-will").innerHTML = w === "years"
    ? `<p class="empty">The premium edge is short-dated: implied vol beats realised most over days and weeks, not years. Nothing here on purpose.</p>`
    : list((T.will[w] || []).map((s) => spxRow(s, "will")), "No condor with a full quote at the 1σ strikes right now.");
  $("#spx-will-foot").textContent = `Measured: since ${E.since}, VIX was above the vol SPX then realised over the next month on ${E.share}% of days (${E.share_1y}% in the last year), by ${E.gap} points on average (${E.gap_1y} last year). That gap is what selling both 1σ strikes collects. The wings cap the loss; the worst stretch had realised vol ${Math.abs(E.worst)} points over implied.`;
  const m = tierTab["spx-maybe"];
  $("#spx-maybe").innerHTML = L.dir === "flat" ? `<p class="empty">No lean: the tape is split (${L.reasons.join(", ")}).</p>`
    : list((T.maybe[m] || []).map((s) => spxRow(s, "maybe")), "No spread with a full quote on this horizon.");
  $("#spx-maybe-foot").textContent = `Lean ${L.dir}: ${L.reasons.join(", ")} (20-day ${money(L.sma20)}, 50-day ${money(L.sma50)}). Trend on the index is not a proven edge here; this is the structure to use if you agree with the lean, sized as a maybe.`;
  const l = tierTab["spx-lotto"];
  $("#spx-lotto").innerHTML = list((T.lotto[l] || []).map((s) => spxRow(s, "lotto")), "Nothing cheap enough on this horizon.");
}


// ---- Earnings: a Mon-Fri week of logo tiles, split before open / after close, plus a feed.
let weekOffset = 0;
const expandedDays = new Set();
const logo = (sym) => `<img src="https://financialmodelingprep.com/image-stock/${sym}.png" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'fallback',textContent:'${sym.slice(0, 4)}'}))">`;
const TILE_LIMIT = 9;

function tile(r) {
  const state = r.reported ? " rep" + (r.day0 > 0 ? " up" : r.day0 < 0 ? " down" : "") : "";
  const tip = r.reported
    ? `${r.symbol} · EPS ${r.eps ?? "—"} vs est ${r.estimate ?? "—"} · surprise ${r.surprise == null ? "—" : sign(r.surprise, 0) + "%"} · day ${r.day0 == null ? "—" : sign(r.day0, 1) + "%"}`
    : `${r.symbol} · est EPS ${r.estimate ?? "—"}`;
  return `<div class="tile${state}" title="${esc(tip)}" data-sym="${r.symbol}">${logo(r.symbol)}<span>${r.symbol}</span></div>`;
}

function sessionBlock(label, rows, key) {
  if (!rows.length) return "";
  const open = expandedDays.has(key);
  const shown = open ? rows : rows.slice(0, TILE_LIMIT);
  const more = rows.length - shown.length;
  return `<div class="sess">${label}</div><div class="tiles">${shown.map(tile).join("")}${more > 0 ? `<div class="tile more" data-expand="${key}">+${more}</div>` : ""}</div>`;
}

function renderCalendar() {
  const W = data.earnings_weeks, C = data.earnings_calendar; if (!W || !C) return;
  const wk = W[String(weekOffset)] || W["0"];
  const d = (s) => new Date(s + "T12:00:00");
  const fmtD = (s, o) => d(s).toLocaleDateString("en-US", o);
  $("#wk-range").textContent = `${fmtD(wk.start, { month: "short", day: "numeric" })} – ${fmtD(wk.end, { month: "short", day: "numeric" })}`;
  $("#wk-count").textContent = `${wk.count} reports · ${data.earnings_drift.scanned.toLocaleString()} names scanned`;
  $("#wk-prev").disabled = !W[String(weekOffset - 1)]; $("#wk-next").disabled = !W[String(weekOffset + 1)];
  $("#wk-days").innerHTML = wk.days.map((day) => `
    <div class="dcol${day.is_today ? " today" : ""}">
      <h4>${fmtD(day.date, { weekday: "short" })}<small>${fmtD(day.date, { month: "short", day: "numeric" })}</small></h4>
      ${sessionBlock("☀ Before open", day.before, day.date + "b")}
      ${sessionBlock("☾ After close", day.after, day.date + "a")}
      ${sessionBlock("· Time TBA", day.during, day.date + "d")}
      ${!day.before.length && !day.after.length && !day.during.length ? `<p class="empty dim" style="text-align:center;font-size:12px">—</p>` : ""}
    </div>`).join("");

  $("#cal-bar").textContent = C.bar == null ? "" : `a top-fifth beat this year: +${fmt(C.bar, 0)}% or more`;
  $("#cal-feed").innerHTML = C.recent.length ? C.recent.slice(0, feedShown).map((r) => `
    <div class="card${r.fired ? " hit" : ""}">
      <div class="head">${logo(r.symbol)}<div><b>${r.symbol}</b><small>${fmtD(r.date, { month: "short", day: "numeric" })}${r.fired ? " · big beat, market liked it" : ""}</small></div>
        <span class="move ${cls(r.day0)}">${r.day0 == null ? "" : sign(r.day0, 1) + "%"}</span></div>
      <table><thead><tr><th></th><th>Est.</th><th>Actual</th><th>Surprise</th></tr></thead>
      <tbody><tr><td class="dim">EPS</td><td>${r.estimate == null ? "—" : fmt(r.estimate)}</td><td>${r.eps == null ? "—" : fmt(r.eps)}</td>
      <td class="${cls(r.surprise)}">${r.surprise == null ? "—" : sign(r.surprise, 0) + "%"}</td></tr></tbody></table>
    </div>`).join("") + (C.recent.length > feedShown ? `<button class="pillbtn more-btn" id="feed-more">Show ${Math.min(FEED_STEP, C.recent.length - feedShown)} more · ${(C.recent.length - feedShown).toLocaleString()} left</button>` : "")
    : `<p class="empty">No reports in the last 10 days.</p>`;
}
// The feed covers the whole market (hundreds of reports in a busy week): show it in pages.
const FEED_STEP = 30;
let feedShown = FEED_STEP;
document.addEventListener("click", (e) => { if (e.target.closest("#feed-more")) { feedShown += FEED_STEP; renderCalendar(); } });

function wireCalendar() {
  $("#wk-prev").onclick = () => { weekOffset--; renderCalendar(); };
  $("#wk-next").onclick = () => { weekOffset++; renderCalendar(); };
  $("#wk-today").onclick = () => { weekOffset = 0; renderCalendar(); };
  $("#wk-days").addEventListener("click", (e) => {
    const more = e.target.closest("[data-expand]");
    if (more) { expandedDays.add(more.dataset.expand); renderCalendar(); return; }
    const t = e.target.closest(".tile[data-sym]");
    if (t) window.open(`https://finance.yahoo.com/quote/${t.dataset.sym}/`, "_blank", "noopener");
  });
}

function renderPortfolio() {
  fetch("engine_portfolio.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((P) => {
    if (!P || !P.summary) { $("#ep-stats").innerHTML = `<p class="empty">The engine hasn't started trading yet. It opens its account on the first export.</p>`; return; }
    const S = P.summary, dollars = (n) => sign(n, 0).replace(/^([+-])/, "$1$");
    $("#ep-since").textContent = `${S.live ? "live since" : "goes live"} ${P.started} · $100,000 · buys the brain's top monthly picks, 5% each, up to 3 new names a day · sells after 21 trading days, or sooner if the brain turns on a name · no human in the loop`;
    $("#ep-stats").innerHTML = `
      <div><span class="k">total P&amp;L</span><b class="${cls(S.total_pnl)}">${dollars(S.total_pnl)}</b></div>
      <div><span class="k">today</span><b class="${cls(S.day_pnl)}">${dollars(S.day_pnl)}</b></div>
      <div><span class="k">equity</span><b>${money(S.equity)}</b></div>
      <div><span class="k">return</span><b class="${cls(S.return_pct)}">${sign(S.return_pct)}%</b></div>
      <div><span class="k">SPY same period</span><b class="${cls(S.spy_pct)}">${sign(S.spy_pct)}%</b></div>
      <div><span class="k">open P&amp;L</span><b class="${cls(S.open_pnl)}">${dollars(S.open_pnl)}</b></div>
      <div><span class="k">realised</span><b class="${cls(S.realized)}">${dollars(S.realized)}</b></div>
      <div><span class="k">closed · hit rate</span><b>${S.n_closed}${S.hit_rate == null ? "" : ` · ${Math.round(S.hit_rate * 100)}%`}</b></div>
      <div><span class="k">cash</span><b>${money(S.cash)}</b></div>`;
    $("#ep-open").innerHTML = P.open.length ? `<ul class="rh">${P.open.map((p) => rhRow({
      title: p.symbol, tag: `<span class="pill">${p.shares} sh</span>`,
      sub: `in at ${money(p.entry)} on ${p.entry_date} · ${(p.why || []).join(" · ")} · ${p.days_left}d left${p.rank != null ? ` · brain rank ${p.rank}/100` : ""}${(p.source || "").includes("brain") ? "" : " · legacy"}`,
      value: money(p.value), delta: `${dollars(p.pnl)} · ${sign(p.ret_pct, 1)}%`, deltaCls: cls(p.pnl), hit: true,
    })).join("")}</ul>` : `<p class="empty">${S.live ? "No open positions. The engine buys the brain's next monthly picks." : `Not live yet. Trading starts at the ${P.started} session; the first buys are the brain's picks from that day on.`}</p>`;
    // History: every trade the engine has ever made, newest first, closed and open alike.
    const hist = [
      ...P.closed.map((c) => ({ ...c, state: "closed" })),
      ...P.open.map((p) => ({ ...p, state: "open" })),
    ].sort((a, b) => (b.exit_date || b.entry_date).localeCompare(a.exit_date || a.entry_date));
    $("#ep-closed").innerHTML = hist.length ? `<ul class="rh">${hist.map((c) => rhRow({
      title: c.symbol, tag: `<span class="pill">${c.shares} sh</span><span class="pill ${c.state === "open" ? "call" : ""}">${c.state}</span>`,
      sub: c.state === "closed"
        ? `bought ${money(c.entry)} ${c.entry_date} → sold ${money(c.exit)} ${c.exit_date} · ${sign(c.excess_pct, 1)}% vs SPY${c.reason ? " · " + c.reason : ""}`
        : `bought ${money(c.entry)} ${c.entry_date} · still held · ${c.days_left}d left`,
      value: dollars(c.pnl), delta: `${sign(c.ret_pct, 1)}%`, deltaCls: cls(c.pnl),
    })).join("")}</ul>` : `<p class="empty dim">No trades yet.</p>`;
  }).catch(() => {});
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("#ep-tabs button"); if (!b) return;
  document.querySelectorAll("#ep-tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $("#ep-open").hidden = b.dataset.ep !== "current"; $("#ep-closed").hidden = b.dataset.ep !== "history";
});

// ---- Engine suggestions: the self-learning engine's live calls and its own scorecard.
function sparkline(canvas, points) {
  const ctx = canvas.getContext("2d"), w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (points.length < 2) return;
  ctx.strokeStyle = "#3a3a3f"; ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke(); ctx.setLineDash([]);
  const last = points[points.length - 1];
  ctx.strokeStyle = last >= 0.5 ? "#00c805" : "#ff5000"; ctx.lineWidth = 1.5; ctx.beginPath();
  points.forEach((p, i) => {
    const x = (i / (points.length - 1)) * w, y = h - ((p - 0.3) / 0.4) * h;
    i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  });
  ctx.stroke();
}

function renderLearning(L) {
  if (!$("#acc-recent")) return;   // the intraday learner strip was retired with the old engine
  const pct = (v) => (v == null ? "—" : (v * 100).toFixed(1) + "%");
  const fresh = Date.now() - new Date(L.updated_at).getTime() < 180000;
  const badge = $("#learn-live");
  badge.className = "live" + (fresh ? " on" : "");
  badge.lastElementChild.textContent = fresh ? (L.mode === "live" ? "learning" : "replay") : "idle";
  $("#acc-recent").textContent = pct(L.accuracy_recent);
  $("#acc-recent").className = L.accuracy_recent > 0.52 ? "up" : L.accuracy_recent < 0.48 ? "down" : "";
  $("#n-resolved").textContent = L.n_resolved.toLocaleString();
  const edge = L.accuracy_recent > 0.53 ? "edge" : L.accuracy_recent < 0.47 ? "no edge" : "coin flip";
  $("#learn-verdict").textContent = edge;
  $("#learn-verdict").className = edge === "edge" ? "up" : edge === "no edge" ? "down" : "dim";
  sparkline($("#spark"), L.history.map((p) => p.acc).filter((a) => a != null));
  const top = L.suggestions.slice(0, 3).map((s) => `${s.symbol} ${s.p_up >= 0.5 ? "↑" : "↓"}${Math.round(Math.max(s.p_up, 1 - s.p_up) * 100)}%`).join(" · ");
  $("#learn-foot").textContent = `Predicts 15 minutes ahead every minute and re-weights itself on each result. ` +
    (top ? `Current calls: ${top}. ` : "") + `Price patterns alone; shown for honesty, not as calls.`;
}

function pollLearning() {
  fetch("learning.json", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)).then((L) => L && renderLearning(L)).catch(() => {});
}

function wire() {
  $("#vol-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    document.querySelectorAll("#vol-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    renderVolume(b.dataset.kind);
  });
  document.querySelectorAll("[data-h]").forEach((b) => b.addEventListener("click", () => {
    const group = b.closest("[data-tier]");
    if (group) {
      tierTab[group.dataset.tier] = b.dataset.h;
      group.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      renderTiers(); renderSpx(); return;
    }
    horizon = b.dataset.h;
    document.querySelectorAll("#horizon-tabs [data-h], .in-horizon").forEach((x) => x.classList.toggle("on", x === b));
    renderEdges();
  }));
  $("#only-edge").addEventListener("change", renderEdges);
  document.querySelectorAll(".nav a").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    document.querySelectorAll(".nav a").forEach((x) => x.classList.toggle("active", x === a));
    document.querySelectorAll("main").forEach((m) => (m.hidden = m.id !== a.dataset.tab));
    window.scrollTo(0, 0);
    a.scrollIntoView({ inline: "center", block: "nearest" });
  }));
}

// ---- Home: market outlook. Up or down, as odds: how often the S&P 500 rose from days like
// today (same side of its 200-day average), plus the range the options market is pricing.
function renderOutlook() {
  const el = $("#outlook"); if (!el) return;
  const W = data.tiers && data.tiers.stocks && data.tiers.stocks.will;
  const pick = (h) => W && !Array.isArray(W) && (W[h] || []).find((r) => r.symbol === "SPY");
  const wk = pick("weekly"), mo = pick("month"), yr = pick("years");
  if (!mo) { el.innerHTML = ""; return; }
  const call = mo.odds >= 60 ? ["More likely up", "up"] : mo.odds >= 53 ? ["Leans up", "up"] : mo.odds > 47 ? ["Coin flip", "dim"] : mo.odds > 40 ? ["Leans down", "down"] : ["More likely down", "down"];
  const row = (label, r) => !r ? "" : `
    <div class="ol-row">
      <span class="ol-l">${label}</span>
      <span class="ol-bar"><i style="width:${Math.round(r.odds)}%"></i></span>
      <span class="ol-o ${r.odds >= 50 ? "up" : "down"}">${fmt(r.odds, 0)}% up</span>
      <span class="ol-t">typical ${sign(r.avg, 1)}%</span>
    </div>`;
  const X = data.spx, wkMove = X && (X.expected || []).find((m) => m.label === "this week");
  el.innerHTML = `
    <div class="ol-head"><span class="k">Market outlook · S&amp;P 500</span><span class="pill ${mo.uptrend ? "call" : "put"}">${mo.uptrend ? "above" : "below"} its 200-day average</span></div>
    <div class="ol-call ${call[1]}">${call[0]}</div>
    <div class="ol-sub">From days like today, the S&amp;P 500 was higher a month later ${fmt(mo.odds, 0)}% of the time.</div>
    ${row("Next week", wk)}${row("Next month", mo)}${row("Next year", yr)}
    ${wkMove ? `<div class="ol-range"><span class="k">options are pricing</span> ±${fmt(wkMove.move_pct, 1)}% this week · ${money(wkMove.lo)} – ${money(wkMove.hi)}${X.vix != null ? ` · VIX ${fmt(X.vix, 1)}` : ""}</div>` : ""}
    <div class="ol-foot">Odds are the index's own record since ${mo.since.slice(0, 4)} on days it sat ${mo.uptrend ? "above" : "below"} its 200-day average (${mo.n.toLocaleString()} of them). A base rate, not a promise about this week.</div>`;
}

function renderAll() {
  renderStatus(); renderVolume(document.querySelector("#vol-tabs .on")?.dataset.kind); renderEdges(); renderPead(); renderCalendar(); renderPaper(); renderPortfolio(); renderFlow(); renderSpx(); renderOutlook();
}

// The page keeps itself current: every minute it re-reads data.json and, if the export
// has produced a newer snapshot, re-renders everything from it (the 3D shape included).
function pollData() {
  fetch("data.json", { cache: "no-store" }).then((r) => r.json()).then((d) => {
    if (d.generated_at === data.generated_at) return;
    data = d;
    renderAll();
    useSnapshotBoard();
  }).catch(() => {});
}

fetch("data.json", { cache: "no-store" }).then((r) => r.json()).then((d) => {
  data = d;
  renderAll(); buildShape(); wire(); wireCalendar(); wirePaper(); connectLive();
  pollLearning(); setInterval(pollLearning, 10000);
  setInterval(pollData, 60000);
}).catch((e) => {
  // Never a silent blank page: say what broke and keep retrying until a good snapshot lands.
  const t = document.getElementById("fresh-text");
  if (t) t.textContent = "data file unreadable, retrying… (" + (e && e.message ? e.message.slice(0, 60) : "error") + ")";
  setTimeout(() => location.reload(), 60000);
});
