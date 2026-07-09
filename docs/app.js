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

function renderTimeline(history) {
  const el = document.getElementById("timeline");
  el.innerHTML = "";
  const recent = history.slice(-96); // last ~48h at 30 min cadence
  for (const h of recent) {
    const bar = document.createElement("div");
    const meta = STATES[h.s] || STATES.INCONCLUSIVE;
    bar.className = `bar ${meta.cls}`;
    let tip = `${new Date(h.t).toLocaleString()}\n${meta.label}`;
    if (h.st) tip += `\nsubmit ${h.st - (h.sf || 0)}/${h.st} queued`;
    if (h.ok) tip += `\nretrieval ${fmtLatency(h.l)}`;
    bar.title = tip;
    el.appendChild(bar);
  }
  if (recent.length) {
    document.getElementById("axis-start").textContent =
      relTime(recent[0].t).replace(" ago", " ago");
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
