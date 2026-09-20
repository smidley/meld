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
// The verbs the API can run (spec 22). A legal verb outside this set -- dispatch, collect, review, audit,
// reopen, reap, plan-issues, approve, request-changes, push-fix -- belongs to a group or a worker run, so the
// page shows it as text rather than offering a button that would only ever be refused.
const DRIVEABLE = new Set(["claim", "release", "post", "tag", "challenge", "decide", "remember",
                           "plan-approve", "plan-ack", "merge"]);

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

function verbControls(into, legal, { ref, number }) {
  into.replaceChildren();
  for (const verb of legal || []) {
    if (!DRIVEABLE.has(verb)) { into.append(cell("dim", verb)); continue; }
    const b = text(document.createElement("button"), verb);
    b.className = "verb";
    b.dataset.action = verb;
    if (ref != null) b.dataset.ref = ref;
    if (number != null) b.dataset.number = String(number);
    into.append(b);
  }
}

function boardRow(into, id, what, legal, keys) {
  const row = el("row").content.firstElementChild.cloneNode(true);
  // Both tracks truncate: an agreement ref is a model-authored token of any length and a title can be long.
  // The full text stays reachable on hover, which is what the freeze badge does with its reset instant.
  const rid = text(row.querySelector(".rid"), id);
  const rwhat = text(row.querySelector(".rwhat"), what);
  rid.title = id;
  rwhat.title = what;
  verbControls(row.querySelector(".rverbs"), legal, keys);
  into.append(row);
}

function renderBoard(s) {
  const issues = el("issues");
  issues.replaceChildren();
  for (const i of s.issues || []) {
    const held = [i.status, i.claimed_by && `claimed by ${i.claimed_by}`, i.worker, i.challenged && "challenged"];
    boardRow(issues, `#${i.number}`, `${i.title} — ${held.filter(Boolean).join(", ")}`, i.legal,
             { ref: `#${i.number}`, number: i.number });
  }
  const pulls = el("pulls");
  pulls.replaceChildren();
  for (const p of s.pulls || []) {
    const how = [p.merged ? "merged" : p.state, p.issue && `closes #${p.issue}`,
                 p.approvals.length && `approved by ${p.approvals.join(", ")}`,
                 p.changes_requested.length && `changes requested by ${p.changes_requested.join(", ")}`];
    boardRow(pulls, `#${p.number}`, how.filter(Boolean).join(", "), p.legal, { ref: `#${p.number}`, number: p.number });
  }
  const agreements = el("agreements");
  agreements.replaceChildren();
  for (const [ref, a] of Object.entries((s.coordination && s.coordination.agreements) || {})) {
    boardRow(agreements, ref, a.joint_verified ? `${a.status} (verified)` : a.status, a.legal, { ref });
  }
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
  renderBoard(s);
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
            why: root.querySelector(".why"), who: root.querySelector(".who"),
            listed: false, liveness: { state: "unknown", reason: "not listed yet" } };
    text(col.who, id);
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
    text(col.who, s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`);
  }
  for (const [id, col] of columns) {
    if (seen.has(id)) continue;
    // One poll of grace, not a permanent exemption: a column the feed created before the poll had listed its
    // stream survives to be listed next time, while a column rebuilt by the trailing frames of a stream that
    // has already left /api/streams -- a relaunched role's old log, re-ended on every feed reconnect -- is
    // gone within two streams polls (up to ~10s) instead of never. Both come from the same `discover` the feed reads, so a
    // genuinely early column is always named by the following poll.
    if (col.listed) { col.root.remove(); columns.delete(id); } else { col.listed = true; }
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
