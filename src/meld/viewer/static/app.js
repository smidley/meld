"use strict";
// The viewer's one page: state and streams polled from the API, one column per transcript fed by EventSource.
// Every string shown here came from a model or a tool, so it only ever reaches the document as a text node.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const KINDS = ["run_started", "message", "thinking", "tool_call", "tool_result", "usage", "run_finished",
               "usage_limit", "log_truncated", "cli_error", "raw"];
const BADGE_STATES = ["alive", "dead", "unknown", "frozen", "waiting"];
const STATE_EVERY_MS = 10000;
const STREAMS_EVERY_MS = 5000;
const MAX_SOURCES = 4;       // two connections stay free for the state/streams polls -- browsers cap ~6 per origin
const SOURCE_PRIORITY = { alive: 0, dead: 1, unknown: 2 };
const columns = new Map();   // stream id -> {root, list, live, why, source, liveness}

function api(path, params) {
  const url = new URL(path, location.origin);
  url.searchParams.set("t", TOKEN);
  for (const [k, v] of Object.entries(params || {})) url.searchParams.set(k, v);
  return url;
}

const el = (id) => document.getElementById(id);

function text(node, value) { node.textContent = value == null ? "" : String(value); return node; }

function badge(node, state, label) {
  // classList, never className: className replaces the whole attribute, which would erase the static
  // ".live" hook a later poll re-queries -- that erasure is what threw on the second poll of any column.
  node.classList.remove(...BADGE_STATES);
  node.classList.add("badge");
  if (state) node.classList.add(state);   // classList.add("") throws; the phase badge passes state=""
  return text(node, label || state);
}

async function getJSON(path) {
  const response = await fetch(api(path), { cache: "no-store" });
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).error || ""; } catch (e) { detail = ""; }
    throw new Error(`${path}: ${response.status}${detail ? " " + detail : ""}`);
  }
  return response.json();
}

function renderState(state) {
  const s = state.status;
  text(el("repo"), s.repo);
  text(el("milestone"), s.milestone ? `milestone ${s.milestone}` : "");
  badge(el("phase"), "", s.phase || "");
  el("halted").classList.toggle("hidden", !s.halted);
  const spent = Object.entries(s.budget.spent || {}).map(([k, v]) => `${k} ${v}`).join(", ");
  text(el("budget"), spent ? `spent: ${spent}` : "");
  const disk = state.disk.free_mb == null ? "disk: unreadable" : `disk: ${state.disk.free_mb} MB free`;
  text(el("disk"), disk).classList.toggle("bad", state.disk.free_mb != null && state.disk.free_mb < state.disk.warn_free_mb);
  text(el("logs"), `logs: ${state.logs.total_mb} MB`).classList.toggle("bad", state.logs.total_mb > state.logs.warn_total_mb);
  text(el("stamp"), `as of ${state.generated_at}`);
  text(el("error"), "");

  const sup = el("supervisors");
  sup.replaceChildren();
  for (const [group, live] of Object.entries(state.supervisors)) {
    const span = document.createElement("span");
    const reason = text(document.createElement("span"), ` ${live.reason}`);
    reason.className = "dim";
    span.append(text(document.createElement("span"), `${group}: `), badge(document.createElement("span"), live.state), reason);
    for (const [role, freeze] of Object.entries(state.frozen[group] || {})) {
      if (freeze) {
        const f = badge(document.createElement("span"), "frozen", `${role} frozen`);
        f.title = freeze.resets_at ? `resets ${freeze.resets_at}` : `since ${freeze.at}, re-probing`;
        span.append(" ", f);
      }
    }
    sup.append(span);
  }
  const nh = el("needs-human");
  nh.replaceChildren();
  for (const item of s.needs_human || []) {
    const line = text(document.createElement("span"), `needs human: #${item.issue} ${item.title} (${item.age_minutes}m)`);
    line.className = "bad";
    nh.append(line);
  }
}

function cell(className, value) {
  const span = text(document.createElement("span"), value);
  span.className = className;
  return span;
}

function appendEvent(col, ev) {
  const li = document.createElement("li");
  li.className = ev.kind + (ev.ok === false ? " failed" : "") + (ev.meta && ev.meta.synthesized ? " synthesized" : "");
  li.append(cell("at", (ev.at || "").slice(11, 19)),
            cell("kind", ev.tool ? `${ev.kind} ${ev.tool}` : ev.kind),
            cell("text", ev.text));
  const list = col.list;
  const pinned = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.append(li);
  if (pinned) list.scrollTop = list.scrollHeight;
}

function openStream(col, id) {
  // EventSource reconnects by itself and sends Last-Event-ID, so resumption needs nothing here.
  const source = new EventSource(api("/api/events", { stream: id }));
  for (const kind of KINDS) source.addEventListener(kind, (e) => appendEvent(col, JSON.parse(e.data)));
  source.addEventListener("end", () => { col.root.classList.add("ended"); source.close(); openWaiting(); });
  source.onerror = () => col.root.classList.add("reconnecting");
  source.onopen = () => col.root.classList.remove("reconnecting");
  col.source = source;
}

function openWaiting() {
  // The only place that opens an EventSource: at most MAX_SOURCES at once, so the two polls always keep a
  // free connection. A column with no source yet is waiting for a slot; one whose source is already
  // closed has ended and is never reopened -- reopening would replay its whole transcript from the start.
  let openCount = 0;
  const waiting = [];
  for (const [id, col] of columns) {
    if (col.source == null) waiting.push([id, col]);
    else if (col.source.readyState !== EventSource.CLOSED) openCount++;
  }
  waiting.sort((a, b) => (SOURCE_PRIORITY[a[1].liveness.state] ?? 3) - (SOURCE_PRIORITY[b[1].liveness.state] ?? 3));
  for (const [id, col] of waiting) {
    if (openCount >= MAX_SOURCES) break;
    openStream(col, id);
    openCount++;
  }
}

function renderStreams(list) {
  const seen = new Set();
  for (const s of list) {
    seen.add(s.id);
    let col = columns.get(s.id);
    if (!col) {
      const root = el("column").content.firstElementChild.cloneNode(true);
      col = { root, list: root.querySelector(".events"), live: root.querySelector(".live"),
              why: root.querySelector(".why"), source: null };
      const who = s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`;
      text(root.querySelector(".who"), who);
      el("streams").append(root);
      columns.set(s.id, col);
    }
    col.liveness = s.liveness;
  }
  for (const [id, col] of columns) {
    if (!seen.has(id)) { if (col.source) col.source.close(); col.root.remove(); columns.delete(id); }
  }
  openWaiting();
  for (const col of columns.values()) {
    badge(col.live, col.source ? col.liveness.state : "waiting");
    text(col.why, col.liveness.reason);
  }
}

async function pollState() {
  try { renderState(await getJSON("/api/state")); } catch (e) { text(el("error"), e.message); }
  setTimeout(pollState, STATE_EVERY_MS);
}

async function pollStreams() {
  try { renderStreams((await getJSON("/api/streams")).streams); } catch (e) { text(el("error"), e.message); }
  setTimeout(pollStreams, STREAMS_EVERY_MS);
}

pollState();
pollStreams();
