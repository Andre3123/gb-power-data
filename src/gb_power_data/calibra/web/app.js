// Calibra web app: screens only. Every number comes from the API (/v1/...), never from here.
"use strict";

const S = { key: null, me: null, files: { f: null, o: null }, guess: null, mapping: null, agent: null };
const $ = (id) => document.getElementById(id);

// ---- small helpers -------------------------------------------------------------------
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined) n.append(kid.nodeType ? kid : String(kid));
  return n;
}
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 1000) / 10}%`);
function money(x, unit) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  const sym = { GBP: "£", EUR: "€", USD: "$" }[unit];
  const abs = Math.abs(x);
  const s = abs >= 1e6 ? `${(abs / 1e6).toFixed(2)}m` : abs >= 1e4 ? `${Math.round(abs / 1e3)}k`
    : abs.toLocaleString("en-GB", { maximumFractionDigits: abs < 10 ? 2 : 0 });
  const sign = x < 0 ? "−" : "";
  return sym ? `${sign}${sym}${s}` : `${sign}${s}${unit && unit !== "unit" ? " " + unit : ""}`;
}
const utc = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "–");
function show(id, on = true) { $(id).hidden = !on; }
function fail(id, err) { const e = $(id); e.textContent = err.message || String(err); e.hidden = false; }
function store(k, v) { try { v === null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (_) { /* private mode */ } }
function recall(k) { try { return sessionStorage.getItem(k); } catch (_) { return null; } }

async function api(path, { method = "GET", json, form } = {}) {
  const headers = { Authorization: `Bearer ${S.key}` };
  let body;
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (form) body = form;
  const r = await fetch(path, { method, headers, body });
  const text = await r.text();
  let data = text;
  try { data = JSON.parse(text); } catch (_) { /* plain text */ }
  if (!r.ok) {
    let d = data && data.detail !== undefined ? data.detail : data;
    if (Array.isArray(d)) d = d.map((x) => `${(x.loc || []).slice(1).join(".") || "request"}: ${x.msg}`).join("\n");
    const err = new Error(typeof d === "string" ? d : JSON.stringify(d));
    err.status = r.status;
    throw err;
  }
  return data;
}

// ---- routing -------------------------------------------------------------------------
const VIEWS = ["signin", "agents", "onboard", "report", "connect"];
async function route() {
  const [view, arg] = (location.hash.slice(1) || "agents").split("/");
  if (!S.key) { S.key = recall("calibra_key"); }
  if (!S.key && view !== "signin") { location.hash = "signin"; return; }
  if (S.key && !S.me) {
    try { S.me = (await api("/v1/me")).customer; } catch (e) { signOut(); return; }
  }
  $("nav").hidden = !S.key;
  $("who").textContent = S.me ? S.me : "";
  const v = VIEWS.includes(view) ? view : "agents";
  for (const name of VIEWS) show(`view-${name}`, name === v);
  window.scrollTo(0, 0);
  if (v === "agents") await loadAgents();
  if (v === "onboard") startOnboard();
  if (v === "report") await loadReport(decodeURIComponent(arg || ""));
  if (v === "connect") await loadConnect(decodeURIComponent(arg || ""));
}
function signOut() { S.key = null; S.me = null; store("calibra_key", null); location.hash = "signin"; }
window.addEventListener("hashchange", route);

// ---- 1. sign in ----------------------------------------------------------------------
$("signin-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("signin-error").hidden = true;
  S.key = $("key").value.trim();
  try {
    S.me = (await api("/v1/me")).customer;
    store("calibra_key", S.key);
    $("key").value = "";
    location.hash = "agents";
  } catch (e) {
    S.key = null;
    fail("signin-error", e.status === 401 ? new Error("This key is not recognised. Check it starts with cal_ and was copied in full.") : e);
  }
});
$("signout").addEventListener("click", signOut);

// ---- 2. agents -----------------------------------------------------------------------
async function loadAgents() {
  const { agents } = await api("/v1/agents");
  show("agents-empty", agents.length === 0);
  show("agents-list", agents.length > 0);
  const body = $("agents-body");
  body.replaceChildren(...agents.map((a) => {
    const go = () => { location.hash = `report/${encodeURIComponent(a.agent_id)}`; };
    return el("tr", { class: "clickable", onclick: go, tabindex: 0, onkeydown: (e) => { if (e.key === "Enter") go(); } },
      el("td", {}, el("b", { text: a.agent_id })),
      el("td", { class: "num", text: a.forecasts.toLocaleString("en-GB") }),
      el("td", { class: "num", text: a.resolved.toLocaleString("en-GB") }),
      el("td", { class: "mono", text: a.first.slice(0, 10) }),
      el("td", { class: "mono", text: a.last.slice(0, 10) }),
      el("td", {}, el("a", { href: `#report/${encodeURIComponent(a.agent_id)}`, text: "Track record" })));
  }));
}

// ---- 3. onboarding -------------------------------------------------------------------
const ROLES = [
  ["ignore", "Ignore"],
  ["target_start", "Target start"],
  ["target_end", "Target end"],
  ["as_of", "Made at"],
  ["point", "Your forecast"],
  ["lower", "Your range: low"],
  ["upper", "Your range: high"],
  ["day_type", "Day type"],
  ["forecast_id", "Forecast id"],
  ["data_cutoff", "Latest data used"],
  ["model_version", "Model version"],
  ["feature", "Input to check"],
  ["asset", "Physical assumption"],
];
const MULTI = new Set(["ignore", "feature", "asset"]);
const ZONES = ["UTC", "Europe/London", "Europe/Dublin", "Europe/Amsterdam", "Europe/Berlin", "Europe/Paris",
  "Europe/Madrid", "Europe/Rome", "America/New_York", "America/Chicago", "America/Los_Angeles", "Australia/Sydney"];

function setStep(n) {
  for (const i of [1, 2, 3, 4]) show(`step-${i}`, i === n);
  for (const li of $("steps").children) {
    const k = Number(li.dataset.step);
    li.className = k === n ? "on" : k < n ? "done" : "";
  }
}
function startOnboard() {
  for (const id of ["error-1", "error-2", "error-3"]) $(id).hidden = true;
  setStep(1);
}
document.querySelectorAll("[data-back]").forEach((b) => b.addEventListener("click", () => setStep(Number(b.dataset.back))));

$("step-1").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("error-1").hidden = true;
  S.files.f = $("file-f").files[0] || null;
  S.files.o = $("file-o").files[0] || null;
  const form = new FormData();
  form.append("forecasts", S.files.f);
  if (S.files.o) form.append("outcomes", S.files.o);
  try {
    S.guess = await api("/v1/mapping/guess", { method: "POST", form });
    buildMapping();
    setStep(2);
  } catch (e) { fail("error-1", e); }
});

function buildMapping() {
  const g = S.guess.forecasts;
  const roleOf = {};
  for (const [field, col] of Object.entries(g.columns)) roleOf[col] = { role: field };
  for (const [name, col] of Object.entries(g.assets || {})) if (!roleOf[col]) roleOf[col] = { role: "asset", name };
  for (const [name, col] of Object.entries(g.features)) if (!roleOf[col]) roleOf[col] = { role: "feature", name };
  $("agent-id").value = $("agent-id").value || "my-forecaster";
  const tz = $("tz");
  const local = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const zones = [...new Set([...ZONES, local])];
  tz.replaceChildren(...zones.map((z) => el("option", { value: z, text: z, selected: z === "UTC" })));
  $("map-body").replaceChildren(...g.header.map((col) => {
    const r = roleOf[col] || { role: "ignore" };
    const sel = el("select", { "data-col": col, "aria-label": `What is ${col}?` },
      ROLES.map(([v, label]) => el("option", { value: v, text: label, selected: v === r.role })));
    const name = el("input", { "data-name": col, value: r.name || col.toLowerCase().replace(/[^a-z0-9_]+/g, "_"), "aria-label": "Name in Calibra" });
    const source = el("input", { "data-source": col, placeholder: "source, e.g. datasheet", "aria-label": "Source" });
    const details = el("div", { class: "details" }, name, source);
    const sync = () => { name.hidden = !["feature", "asset"].includes(sel.value); source.hidden = sel.value !== "asset"; check(); };
    sel.addEventListener("change", sync);
    name.addEventListener("input", check);
    setTimeout(sync);
    return el("tr", {},
      el("td", {}, el("b", { text: col })),
      el("td", { class: "mono", title: g.samples[col].join(" | "), text: g.samples[col].filter(Boolean).join(" · ") }),
      el("td", {}, sel), el("td", {}, details));
  }));
  const o = S.guess.outcomes;
  show("outcome-map", !!o);
  if (o) {
    $("realised-col").replaceChildren(...o.header.map((c) => el("option", { value: c, text: c, selected: c === o.realised })));
    const start = g.columns.target_start, fid = g.columns.forecast_id;
    const opts = [];
    for (const c of o.header) {
      opts.push(el("option", { value: `ts:${c}`, text: `${c} (the target start)`, selected: c === start && !o.header.includes(fid) }));
      opts.push(el("option", { value: `id:${c}`, text: `${c} (the forecast id)`, selected: !!fid && c === fid }));
    }
    $("match-col").replaceChildren(...opts);
    $("realised-col").addEventListener("change", check);
  }
  check();
}

function readMapping() {
  const columns = {}, features = {}, assets = {}, problems = [];
  const count = {};
  for (const sel of $("map-body").querySelectorAll("select")) {
    const col = sel.dataset.col, role = sel.value;
    count[role] = (count[role] || 0) + 1;
    if (role === "feature" || role === "asset") {
      const name = $("map-body").querySelector(`[data-name="${CSS.escape(col)}"]`).value.trim();
      if (!/^[A-Za-z0-9_]+$/.test(name)) problems.push(`Give "${col}" a name using letters, digits or _.`);
      if (features[name] || assets[name]) problems.push(`The name "${name}" is used twice.`);
      if (role === "feature") features[name] = col;
      else {
        const src = $("map-body").querySelector(`[data-source="${CSS.escape(col)}"]`).value.trim();
        assets[name] = src ? { column: col, source: src } : { column: col };
      }
    } else if (role !== "ignore") columns[role] = col;
  }
  for (const [role, n] of Object.entries(count)) {
    if (!MULTI.has(role) && n > 1) problems.push(`Only one column can be "${ROLES.find((r) => r[0] === role)[1]}".`);
  }
  for (const need of ["target_start", "as_of", "point"]) {
    if (!columns[need]) problems.push(`Choose the column for "${ROLES.find((r) => r[0] === need)[1]}".`);
  }
  if (!!columns.lower !== !!columns.upper) problems.push("Choose both ends of your range, or neither.");
  show("period-field", !columns.target_end);
  const agent = $("agent-id").value.trim();
  if (!/^[A-Za-z0-9._-]+$/.test(agent)) problems.push("Give the optimiser a name using letters, digits, . _ or -.");
  const m = {
    agent_id: agent, timezone: $("tz").value, target_name: $("target-name").value.trim() || "value",
    unit: $("unit").value.trim() || "unit", columns, features, assets, outcomes: {},
  };
  if (!columns.target_end) m.period = $("period").value;
  if (S.guess.outcomes) {
    m.outcomes.realised = $("realised-col").value;
    const [kind, col] = $("match-col").value.split(/:(.*)/s);
    m.outcomes[kind === "id" ? "forecast_id" : "target_start"] = col;
  }
  return { m, problems };
}
function check() {
  const { problems } = readMapping();
  $("map-problems").replaceChildren(...problems.map((p) => el("li", { text: p })));
  $("to-3").disabled = problems.length > 0;
}
["agent-id", "tz", "period", "target-name", "unit", "match-col"].forEach((id) => $(id).addEventListener("input", check));

$("step-2").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("error-2").hidden = true;
  const { m, problems } = readMapping();
  if (problems.length) return;
  S.mapping = m;
  const form = new FormData();
  form.append("mapping", JSON.stringify(m));
  form.append("forecasts", S.files.f);
  try {
    const p = await api("/v1/mapping/preview", { method: "POST", form });
    $("preview-body").replaceChildren(...p.preview.map((f) => el("tr", {},
      el("td", { class: "mono", text: utc(f.target_start) }),
      el("td", { class: "mono", text: utc(f.as_of) }),
      el("td", { class: "num", text: money(f.point, m.unit) }),
      el("td", { class: "num", text: f.lower === null ? "none" : `${money(f.lower, m.unit)} to ${money(f.upper, m.unit)}` }),
      el("td", { text: f.day_type || "–" }),
      el("td", { class: "mono", text: Object.entries(f.features).map(([k, v]) => `${k}=${v}`).join(", ") || "–" }))));
    $("preview-count").textContent = `${p.rows.toLocaleString("en-GB")} forecasts in your file` +
      (S.guess.outcomes ? `, ${S.guess.outcomes.rows.toLocaleString("en-GB")} actual results.` : ". No actuals file: forecasts will wait to be scored.");
    setStep(3);
  } catch (e) { fail("error-2", e); }
});

$("do-import").addEventListener("click", async () => {
  $("error-3").hidden = true;
  setStep(4);
  const form = new FormData();
  form.append("mapping", JSON.stringify(S.mapping));
  form.append("forecasts", S.files.f);
  if (S.files.o) form.append("outcomes", S.files.o);
  try {
    const r = await api("/v1/import", { method: "POST", form });
    $("import-status").textContent = `Done: ${r.submitted} forecasts recorded, ${r.resolved} scored.`;
    location.hash = `report/${encodeURIComponent(r.agent_id)}`;
  } catch (e) { setStep(3); fail("error-3", e); }
});

// ---- 4. report -----------------------------------------------------------------------
function barRow(label, sub, value, cls, target) {
  const track = el("div", { class: "track" }, el("div", { class: `bar-fill ${cls}`, style: `width:${Math.max(0, Math.min(1, value)) * 100}%` }));
  if (target !== undefined) track.append(el("div", { class: "target", style: `left:${target * 100}%` }, el("span", { text: `promised ${pct(target)}` })));
  return el("div", { class: "row" }, el("div", { class: "label" }, label, sub ? el("small", { text: sub }) : null), track, el("div", { class: "val", text: pct(value) }));
}
function answer(big, what, vs, cls = "") {
  return el("div", { class: "answer" }, el("div", { class: `big ${cls}`, text: big }), el("div", { class: "what", text: what }), el("div", { class: "vs", text: vs }));
}
function niceTicks(lo, hi, n = 4) {
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((x) => x >= raw);
  const ticks = [];
  for (let v = Math.floor(lo / step) * step; v < hi + step - 1e-9; v += step) ticks.push(v);
  return ticks;
}
function axisMoney(v, top, unit) {
  const sym = { GBP: "£", EUR: "€", USD: "$" }[unit] || "";
  const sign = v < 0 ? "−" : "", a = Math.abs(v);
  const s = top >= 1e6 ? `${+(a / 1e6).toFixed(2)}m` : top >= 1e4 ? `${+(a / 1e3).toFixed(1)}k` : a.toLocaleString("en-GB");
  return `${sign}${sym}${s}`;
}
function timeChart(rows, unit, width) {
  const W = Math.max(320, Math.round(width)), H = W < 600 ? 240 : 280;
  const L = W < 600 ? 48 : 62, R = 10, T = 12, B = 28;
  const t = rows.map((r) => Date.parse(r.target_start.replace(" ", "T").slice(0, 19) + "Z"));
  const vals = rows.flatMap((r) => (r.calibrated ? [r.lower, r.upper, r.realised] : [r.realised]));
  const ticks = niceTicks(Math.min(...vals), Math.max(...vals) || 1);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const top = Math.max(Math.abs(lo), Math.abs(hi));
  const t0 = Math.min(...t), t1 = Math.max(...t);
  const x = (ms) => L + ((ms - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const y = (v) => T + (1 - (v - lo) / (hi - lo || 1)) * (H - T - B);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": "Each forecast's range from Calibra and the actual result" });
  const g = svgEl("g", { class: "axis" });
  for (const v of ticks) {
    g.append(svgEl("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), "stroke-dasharray": "2 4" }));
    const tx = svgEl("text", { x: L - 8, y: y(v) + 4, "text-anchor": "end" });
    tx.textContent = axisMoney(v, top, unit); g.append(tx);
  }
  for (const [ms, anchor] of [[t0, "start"], [(t0 + t1) / 2, "middle"], [t1, "end"]]) {
    const tx = svgEl("text", { x: x(ms), y: H - 8, "text-anchor": anchor });
    tx.textContent = new Date(ms).toISOString().slice(0, 10); g.append(tx);
  }
  svg.append(g);
  const gap = (W - L - R) / Math.max(1, rows.length);
  const bw = Math.max(1, Math.min(6, gap * 0.7));
  rows.forEach((r, i) => {
    if (!r.calibrated) return;
    svg.append(svgEl("line", { class: "range", x1: x(t[i]), x2: x(t[i]), y1: y(r.lower), y2: y(r.upper), "stroke-width": bw }));
  });
  const rad = Math.max(1.4, Math.min(3, gap * 0.45));
  rows.forEach((r, i) => {
    const miss = r.calibrated && r.realised < r.lower;
    svg.append(svgEl("circle", { class: miss ? "dot miss" : "dot", cx: x(t[i]), cy: y(r.realised), r: miss ? rad + 0.8 : rad }));
  });
  return svg;
}

async function loadReport(agent) {
  S.agent = agent;
  $("r-eyebrow").textContent = `Track record · ${agent}`;
  $("r-connect").href = `#connect/${encodeURIComponent(agent)}`;
  for (const id of ["r-answers", "r-honesty", "r-scope", "r-proof", "r-time", "r-worst"]) $(id).replaceChildren();
  let rep;
  try { rep = await api(`/v1/agents/${encodeURIComponent(agent)}/report`); }
  catch (e) {
    $("r-title").textContent = "No scored forecasts yet";
    $("r-honesty-say").textContent = e.message + ". Send actual results to see the track record.";
    return;
  }
  const [{ rows }, verify] = await Promise.all([
    api(`/v1/agents/${encodeURIComponent(agent)}/forecasts`), api("/v1/ledger/verify")]);
  const unit = rows.length ? rows[0].unit : "unit";
  const cal = rep.calibra_range, own = rep.agent_own_range, flagged = rep.by_scope && rep.by_scope.flagged;
  $("r-title").textContent = `${rep.resolved.toLocaleString("en-GB")} forecasts, scored`;

  $("r-answers").replaceChildren(
    cal ? answer(pct(cal.coverage), "of Calibra's ranges held", `It promised ${pct(rep.promised_coverage)}. Fell below the low end on ${pct(cal.promise_broken_share)} of forecasts.`)
      : answer("–", "Calibra's ranges", "Not enough results yet to learn a range.", "dim"),
    own ? answer(pct(own.coverage), "of your own ranges held", `On the same ${own.forecasts.toLocaleString("en-GB")} forecasts.`, own.coverage < rep.promised_coverage - 0.05 ? "loss" : "")
      : answer("–", "Your own range", "You sent no range of your own. Add low and high columns to compare.", "dim"),
    flagged && flagged.share_of_shortfall !== null
      ? answer(pct(flagged.share_of_shortfall), "of the missed money was flagged in advance", `Flagged: ${flagged.forecasts} of ${rep.calibrated} forecasts (${pct(flagged.forecasts / rep.calibrated)}).`, "loss")
      : answer("0", "forecasts flagged", "All inputs looked like your history, or there was no miss to explain.", "dim"));

  if (cal) {
    $("r-honesty-say").textContent = own
      ? `Your own range held ${pct(own.coverage)} of the time; Calibra's, learned from your past errors, held ${pct(cal.coverage)}. ` +
        `Calibra's average width: ${money(cal.mean_width, unit)} (yours: ${money(own.mean_width, unit)}).`
      : `Calibra's range held ${pct(cal.coverage)} of the time, against the ${pct(rep.promised_coverage)} promised. Average width: ${money(cal.mean_width, unit)}.`;
    const kids = [];
    if (own) kids.push(barRow("Your own range", "as you sent it", own.coverage, own.coverage < rep.promised_coverage - 0.05 ? "loss" : "grey", rep.promised_coverage));
    kids.push(barRow("Calibra's range", "learned from your errors", cal.coverage, "good", own ? undefined : rep.promised_coverage));
    kids.push(el("div", { class: "foot", text: `Share of the ${rep.calibrated.toLocaleString("en-GB")} forecasts whose actual result landed inside the range.` }));
    $("r-honesty").replaceChildren(...kids);
  }
  if (rows.length) {
    const draw = () => $("r-time").replaceChildren(timeChart(rows, unit, $("r-time").clientWidth));
    draw();
    window.onresize = () => { clearTimeout(window._rt); window._rt = setTimeout(draw, 150); };
  }

  if (rep.by_scope) {
    const total = rep.calibrated, fl = flagged || { forecasts: 0, shortfall: 0, share_of_shortfall: 0 };
    const missed = Object.values(rep.by_scope).reduce((a, b) => a + b.shortfall, 0);
    $("r-scope-say").textContent = missed > 0
      ? `In total ${money(missed, unit)} fell below Calibra's low end. Flagged forecasts were ${pct(fl.forecasts / total)} of all forecasts and held ${pct(fl.share_of_shortfall || 0)} of that.`
      : "No actual result fell below Calibra's low end.";
    const split = (cap, right, share) => el("div", { class: "split" },
      el("div", { class: "cap" }, el("span", { text: cap }), el("span", { class: "hint", text: right })),
      el("div", { class: "bar2" }, el("div", { class: "seg flag", style: `width:${share * 100}%`, text: share >= 0.08 ? pct(share) : "" }),
        el("div", { class: "seg rest", style: `width:${(1 - share) * 100}%`, text: share < 0.92 ? "not flagged" : "" })));
    $("r-scope").replaceChildren(
      split("Share of forecasts flagged", `${fl.forecasts} of ${total}`, fl.forecasts / total),
      split("Share of the missed money", money(fl.shortfall, unit), missed > 0 ? fl.share_of_shortfall || 0 : 0));
    const worst = rows.filter((r) => r.calibrated && r.realised < r.lower)
      .map((r) => ({ ...r, gap: r.lower - r.realised })).sort((a, b) => b.gap - a.gap).slice(0, 5);
    $("r-worst").replaceChildren(...worst.map((r) => el("tr", {},
      el("td", { class: "mono", text: r.target_start.slice(0, 16) }),
      el("td", { class: "num", text: money(r.lower, unit) }), el("td", { class: "num", text: money(r.realised, unit) }),
      el("td", { class: "num", text: money(r.gap, unit) }), el("td", { text: r.in_scope ? "no" : "yes" }))));
  }

  const m = rep.methods || {};
  const check = (ok, title, text) => el("div", { class: "check" }, el("div", { class: ok ? "tick" : "tick bad", text: ok ? "✓" : "!" }), el("div", {}, el("b", { text: title }), el("span", { text })));
  $("r-proof").replaceChildren(
    check(verify.ok, verify.ok ? "The record has not been edited" : "The record was changed after it was written",
      verify.ok ? `${verify.entries.toLocaleString("en-GB")} entries in the hash chain. Fingerprint: ${verify.head}` : verify.problems.slice(0, 3).join("; ")),
    check(true, "No range used the future",
      `Each forecast's range was learned only from results whose period had ended before it was made. ${rep.live} of ${rep.resolved} forecasts were sent before their target period; the rest are history replayed in time order.`),
    check(true, "How each range was learned",
      `${m.mondrian || 0} from your own day type, ${m.pooled || 0} from all your forecasts, ${m.none || 0} had no range yet (fewer than 9 earlier results).`));
}

// ---- 5. connect ----------------------------------------------------------------------
let snippets = {};
async function loadConnect(agent) {
  S.agent = agent || S.agent || "my-forecaster";
  $("c-eyebrow").textContent = `Connect · ${S.agent}`;
  const base = location.origin;
  let names = ["input_1"];
  let dayType = null;
  try {
    const st = await api(`/v1/agents/${encodeURIComponent(S.agent)}/status`);
    if (Object.keys(st.gate_bands).length) names = Object.keys(st.gate_bands);
  } catch (_) { /* new agent */ }
  try {
    const { rows } = await api(`/v1/agents/${encodeURIComponent(S.agent)}/forecasts`);
    const last = rows[rows.length - 1];
    if (last && last.day_type) dayType = last.day_type;
  } catch (_) { /* none yet */ }
  const start = new Date(); start.setUTCDate(start.getUTCDate() + 1); start.setUTCHours(0, 0, 0, 0);
  const end = new Date(start); end.setUTCDate(end.getUTCDate() + 1);
  const day = start.toISOString().slice(0, 10);
  const iso = (d) => d.toISOString().replace(".000Z", "+00:00");
  const features = Object.fromEntries(names.map((n) => [n, 0]));
  const forecast = { forecast_id: day, agent_id: S.agent, target_start: iso(start), target_end: iso(end),
    as_of: iso(new Date(Math.floor(Date.now() / 60000) * 60000)), point: 0, ...(dayType ? { day_type: dayType } : {}), features };
  const fjson = JSON.stringify({ forecasts: [forecast] }, null, 2);
  const ojson = JSON.stringify({ outcomes: [{ forecast_id: day, agent_id: S.agent, realised: 0 }] }, null, 2);
  $("c-env").textContent = `export CALIBRA_KEY="${S.key.slice(0, 8)}…"   # your full key`;
  snippets = {
    curl: { code: `# 1. before the target period: send the forecast\ncurl -X POST ${base}/v1/forecasts \\\n  -H "Authorization: Bearer $CALIBRA_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${fjson}'\n\n# 2. after it: send what really happened\ncurl -X POST ${base}/v1/outcomes \\\n  -H "Authorization: Bearer $CALIBRA_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${ojson}'`,
      note: "Replace the zeros with your forecast, your inputs and, later, the actual value. Sending the same forecast twice is safe." },
    python: { code: `import os\nimport httpx\n\ncalibra = httpx.Client(\n    base_url="${base}",\n    headers={"Authorization": f"Bearer {os.environ['CALIBRA_KEY']}"},\n)\n\n# before the target period\nr = calibra.post("/v1/forecasts", json=${fjson.replace(/\n/g, "\n" + " ".repeat(0))})\nr.raise_for_status()\nprint(r.json()["receipts"][0])  # Calibra's range and scope verdict\n\n# after it\ncalibra.post("/v1/outcomes", json=${ojson}).raise_for_status()`,
      note: "Any HTTP library works; the full list of calls is in the API docs." },
    mcp: { code: JSON.stringify({ mcpServers: { calibra: { command: "uvx", args: ["--from", "git+https://github.com/Andre3123/gb-power-data", "calibra-mcp"], env: { CALIBRA_API_URL: base, CALIBRA_KEY: "<your key>" } } } }, null, 2),
      note: "Add this to your AI agent's MCP settings (Claude, an IDE, or any MCP client). The agent gets check_scope, submit_forecast, calibration_status and agent_report. It cannot record actual results, so it never grades itself. Needs uv installed." },
    file: { code: `New forecasts and results as files:\n\n1. Export them with the same columns as your history.\n2. Open "Add data" and upload them.\n3. Forecasts already recorded are kept as they are;\n   new ones are added; new results are scored.\n\nSave your column choices to reuse them:`,
      note: "", save: true },
  };
  selectTab("curl");
}
function selectTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  const s = snippets[name];
  $("c-snippet").textContent = s.code;
  $("c-note").replaceChildren(s.note);
  if (s.save && S.mapping) {
    $("c-note").append(el("button", { type: "button", class: "ghost", text: "Save my column mapping (.json)", onclick: () => {
      const blob = new Blob([JSON.stringify(S.mapping, null, 2)], { type: "application/json" });
      const a = el("a", { href: URL.createObjectURL(blob), download: `calibra-mapping-${S.mapping.agent_id}.json` });
      document.body.append(a); a.click(); a.remove();
    } }));
  } else if (s.save) $("c-note").append("Your column choices are available after an import in this session.");
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
async function copy(text, flag) {
  try { await navigator.clipboard.writeText(text); show(flag); setTimeout(() => show(flag, false), 1500); }
  catch (_) { window.getSelection().selectAllChildren($("c-snippet")); }
}
$("c-copy").addEventListener("click", () => copy($("c-snippet").textContent, "c-copied2"));
$("c-copy-key").addEventListener("click", () => copy(S.key, "c-copied"));

route();

// Example files, made in the browser, so a first-time user can see the exact shape.
function exampleCsv(kind) {
  const f = ["target_start,made_at,forecast,low,high"], o = ["target_start,actual"];
  for (let i = 0; i < 40; i++) {
    const d = new Date(Date.UTC(2025, 0, 1 + i)), prev = new Date(Date.UTC(2024, 11, 31 + i));
    const day = d.toISOString().slice(0, 10), made = prev.toISOString().slice(0, 10) + " 09:00";
    const fc = 100 + Math.round(10 * Math.sin(i / 3)), miss = Math.round(8 * Math.cos(i * 1.7));
    f.push(`${day} 17:00,${made},${fc},${fc - 10},${fc + 10}`);
    o.push(`${day} 17:00,${fc + miss}`);
  }
  return (kind === "f" ? f : o).join("\n") + "\n";
}
function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}
$("dl-f").addEventListener("click", () => download("forecasts.csv", exampleCsv("f")));
$("dl-o").addEventListener("click", () => download("outcomes.csv", exampleCsv("o")));
