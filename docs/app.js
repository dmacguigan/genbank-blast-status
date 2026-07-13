"use strict";

const STATES = {
  OPERATIONAL: { cls: "operational", label: "Operational", color: "var(--green)" },
  SLOW:        { cls: "slow",        label: "Slow",        color: "var(--yellow)" },
  DEGRADED:    { cls: "degraded",    label: "Degraded",    color: "var(--orange)" },
  OUTAGE:      { cls: "outage",      label: "Possible outage", color: "var(--red)" },
  INCONCLUSIVE:{ cls: "inconclusive",label: "Inconclusive", color: "var(--grey)" },
};

function relTime(iso) {
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "unknown";
  const s = Math.round((Date.now() - then) / 1000);
  if (s < 90) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 90) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 36) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function fmtLatency(l) {
  if (l == null || isNaN(l)) return "—";
  return l >= 1 ? `${Math.round(l)}s` : `${l}s`;
}

async function loadJSON(path) {
  const resp = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!resp.ok) throw new Error(`${path}: HTTP ${resp.status}`);
  return resp.json();
}

function renderBanner(s) {
  const banner = document.getElementById("banner");
  const meta = STATES[s.status] || STATES.INCONCLUSIVE;
  banner.className = `banner ${meta.cls}`;
  banner.querySelector(".banner-status").textContent = meta.label;
  document.getElementById("message").textContent = s.message || "";
  document.getElementById("checked").textContent = relTime(s.checked_utc);
  document.getElementById("latency").textContent =
    s.blast_ok ? fmtLatency(s.blast_latency_s) : "—";
  const submitEl = document.getElementById("submit");
  if (s.submit_total) {
    submitEl.textContent = `${s.submit_ok}/${s.submit_total}`;
    submitEl.title = s.submit_failed
      ? `${s.submit_failed} rejected — ${s.submit_sample_error || ""}`
      : "all test submissions queued";
  } else {
    submitEl.textContent = "—";
  }
}

function renderStats(history) {
  const now = Date.now();
  const dayAgo = now - 24 * 3600 * 1000;
  const recent = history.filter(h => new Date(h.t).getTime() >= dayAgo);
  const judged = recent.filter(h => h.s !== "INCONCLUSIVE");
  if (judged.length) {
    const up = judged.filter(h => h.s === "OPERATIONAL" || h.s === "SLOW").length;
    document.getElementById("uptime").textContent =
      `${Math.round((up / judged.length) * 100)}%`;
  }
  const lats = recent.filter(h => h.ok && h.l > 0).map(h => h.l);
  if (lats.length) {
    const avg = lats.reduce((a, b) => a + b, 0) / lats.length;
    document.getElementById("avglat").textContent = fmtLatency(avg);
  }
}

// Fixed 3-day window split into 30-min slots, so the x-axis always spans the
// same time regardless of how many checks ran. Each slot is one bar; slots with
// no check render as a "no data" gap, and a slot with several checks shows the
// worst status so real problems stay visible.
const WINDOW_MS = 3 * 24 * 3600 * 1000; // 3 days
const SLOT_MS = 30 * 60 * 1000;         // 30 min cadence
const SEVERITY = { OUTAGE: 4, DEGRADED: 3, SLOW: 2, OPERATIONAL: 1, INCONCLUSIVE: 0 };

let tooltipEl = null;
function tooltip() {
  if (!tooltipEl) {
    tooltipEl = document.createElement("div");
    tooltipEl.className = "tl-tooltip";
    tooltipEl.hidden = true;
    document.body.appendChild(tooltipEl);
  }
  return tooltipEl;
}
function showTooltip(e) {
  const bar = e.target.closest(".bar");
  if (!bar || !bar.dataset.tip) return;
  const t = tooltip();
  t.textContent = bar.dataset.tip;
  t.hidden = false;
  const pad = 12;
  const r = t.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
  t.style.left = `${Math.max(4, x)}px`;
  t.style.top = `${Math.max(4, y)}px`;
}
function hideTooltip() { if (tooltipEl) tooltipEl.hidden = true; }

function renderTimeline(history) {
  const el = document.getElementById("timeline");
  el.innerHTML = "";
  const end = Date.now();
  const start = end - WINDOW_MS;
  const slotCount = Math.round(WINDOW_MS / SLOT_MS);
  const slots = new Array(slotCount).fill(null);

  for (const h of history) {
    const t = new Date(h.t).getTime();
    if (isNaN(t) || t < start || t >= end) continue;
    const i = Math.floor((t - start) / SLOT_MS);
    const cur = slots[i];
    if (!cur || (SEVERITY[h.s] ?? -1) >= (SEVERITY[cur.s] ?? -1)) slots[i] = h;
  }

  slots.forEach((h, i) => {
    const bar = document.createElement("div");
    if (!h) {
      bar.className = "bar nodata";
      const slotTime = new Date(start + i * SLOT_MS);
      bar.dataset.tip = `${slotTime.toLocaleString()}\nNo check in this window`;
      el.appendChild(bar);
      return;
    }
    const meta = STATES[h.s] || STATES.INCONCLUSIVE;
    bar.className = `bar ${meta.cls}`;
    let tip = `${new Date(h.t).toLocaleString()}\n${meta.label}`;
    if (h.st) tip += `\nsubmit ${h.st - (h.sf || 0)}/${h.st} queued`;
    if (h.ok) tip += `\nretrieval ${fmtLatency(h.l)}`;
    bar.dataset.tip = tip;
    el.appendChild(bar);
  });

  if (!el.dataset.wired) {
    el.addEventListener("mousemove", showTooltip);
    el.addEventListener("mouseleave", hideTooltip);
    el.dataset.wired = "1";
  }
}

function renderLegend() {
  const el = document.getElementById("legend");
  el.innerHTML = "<h2 style='width:100%'>Legend</h2>";
  for (const key of Object.keys(STATES)) {
    const m = STATES[key];
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML =
      `<span class="swatch" style="background:${m.color}"></span>${m.label}`;
    el.appendChild(item);
  }
}

async function refresh() {
  try {
    const status = await loadJSON("status.json");
    renderBanner(status);
  } catch (e) {
    document.getElementById("message").textContent =
      "Could not load status data yet.";
  }
  try {
    const history = await loadJSON("history.json");
    if (Array.isArray(history) && history.length) {
      renderStats(history);
      renderTimeline(history);
    }
  } catch (e) { /* history optional */ }
}

document.getElementById("loaded").textContent = new Date().toLocaleTimeString();
renderLegend();
refresh();
setInterval(refresh, 5 * 60 * 1000);
