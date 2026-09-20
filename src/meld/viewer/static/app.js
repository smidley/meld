"use strict";
// The viewer's one page: state and streams polled from the API, one column per transcript fed by EventSource.
// Every string shown here came from a model or a tool, so it only ever reaches the document as a text node.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const KINDS = ["run_started", "message", "thinking", "tool_call", "tool_result", "usage", "run_finished",
               "usage_limit", "log_truncated", "cli_error", "raw"];
const BADGE_STATES = ["alive", "dead", "unknown", "frozen", "reconnecting"];
const STATE_EVERY_MS = 10000;
const STREAMS_EVERY_MS = 5000;
const columns = new Map();   // stream id -> {root, list, live, why, listed, liveness}

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
  // `spent` is Board.spend: group -> tier -> count, so it takes two levels of entries. One level stringified
  // the inner object and the header read "spent: claude [object Object]" on any run that had dispatched a
  // worker. The cap (tier -> ceiling) goes beside it, because a count means nothing without the number it is of.
  const spent = Object.entries(s.budget.spent || {})
    .flatMap(([group, tiers]) => Object.entries(tiers || {}).map(([tier, n]) => `${group}/${tier} ${n}`)).join(", ");
  const cap = Object.entries(s.budget.cap || {}).map(([tier, n]) => `${tier} ${n}`).join(", ");
  text(el("budget"), [spent && `spent: ${spent}`, cap && `cap: ${cap}`].filter(Boolean).join(" · "));
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

function columnFor(id) {
  // A frame can name a stream the five-second /api/streams poll has not listed yet; give it a column at once,
  // titled by its id, and let the next poll replace the title and the badge. `listed` is what keeps that
  // column alive: only a column a poll has named, and then stopped naming, is removed.
  let col = columns.get(id);
  if (!col) {
    const root = el("column").content.firstElementChild.cloneNode(true);
    col = { root, list: root.querySelector(".events"), live: root.querySelector(".live"),
            why: root.querySelector(".why"), listed: false, liveness: { state: "unknown", reason: "not listed yet" } };
    text(root.querySelector(".who"), id);
    el("streams").append(root);
    columns.set(id, col);
  }
  return col;
}

function openFeed() {
  // One connection for every transcript (spec 22): a browser allows about six to an origin, and one source per
  // column starved these two polls. EventSource reconnects by itself and sends Last-Event-ID, which carries
  // every stream's position, so resumption needs nothing here.
  const feed = new EventSource(api("/api/events"));
  for (const kind of KINDS) {
    feed.addEventListener(kind, (e) => {
      const msg = JSON.parse(e.data);
      appendEvent(columnFor(msg.stream), msg.event);
    });
  }
  feed.addEventListener("end", (e) => columnFor(JSON.parse(e.data).stream).root.classList.add("ended"));
  feed.onopen = () => badge(el("feed"), "alive", "feed live");
  feed.onerror = () => badge(el("feed"), "reconnecting", "feed reconnecting");
}

function renderStreams(list) {
  const seen = new Set();
  for (const s of list) {
    seen.add(s.id);
    const col = columnFor(s.id);
    col.listed = true;
    col.liveness = s.liveness;
    text(col.root.querySelector(".who"), s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`);
  }
  for (const [id, col] of columns) {
    if (col.listed && !seen.has(id)) { col.root.remove(); columns.delete(id); }
  }
  for (const col of columns.values()) {
    badge(col.live, col.liveness.state);
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
openFeed();
