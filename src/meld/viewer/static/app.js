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
// The verbs /api/action implements (spec 22): the routes this page has, which is a different axis from
// legality. A legal verb outside this set -- dispatch, collect, review, audit, reopen, reap, plan-issues,
// approve, request-changes, push-fix -- has no route here, and the page renders it as text rather than a
// button. Where such a verb can be run instead -- the CLI, GitHub's own review UI -- is not something this
// page knows, so it never says; see verbControls' tooltip below.
const DRIVEABLE = new Set(["claim", "release", "post", "tag", "challenge", "decide", "remember",
                           "plan-approve", "plan-ack", "merge"]);
// What each action needs from the bar: `ref`, free `text`, and a header `verb` for the two that carry one.
// Anything an action does not name is hidden and not sent, so `post` never carries a stale `--supersedes`
// and `plan-approve` never carries a ref.
const FIELDS = {
  "claim": ["ref"], "release": ["ref"], "merge": ["ref"], "decide": ["ref"],
  "plan-ack": ["ref"], "plan-approve": [],
  "remember": ["ref", "text"], "challenge": ["ref", "verb", "text"], "post": ["ref", "verb", "text"],
};
const POST_VERBS = ["ASK", "DIRECTIVE", "HALT", "RESUME", "REVERT", "PROVIDED", "RESOLVED", "ACK", "AMEND",
                    "APPROVE", "NEEDS-HUMAN"];
const CHALLENGE_ACTIONS = ["open", "evidence", "retract", "uphold", "dismiss"];
// Identity is `meld setup`'s, not a running page's: changing a repo, a human, or a group's bot/auth mid-milestone
// orphans that group's claims and its open PRs. Shown, never editable (spec 22). The API still accepts them, so
// what the form sends back is what it was given, with only the editable values replaced.
const READONLY_TABLES = ["repo", "humans"];
const READONLY_GROUP_FIELDS = ["cli", "bot", "auth", "token_env", "app_id", "installation_id",
                               "private_key_env", "private_key_file"];
let configState = null;     // {path, config, models, note} as last read, and the inputs' backing store

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
    if (!FIELDS[verb]) {
      // Both branches speak only about this page's own reach: legality lives in the `legal` list this row
      // renders and in the refusal the bar shows verbatim (spec 22). `DRIVEABLE` is what /api/action
      // implements, and spec 22 has every verb the page offers also existing as a `meld` command, so that
      // branch may say a driveable verb runs from the CLI -- a fact about the set, not a claim about the verb.
      // A legal verb outside the set gets no such sentence: where it can be run instead -- the CLI, GitHub's
      // own review UI -- is not something this page knows or should say, only that it has no control for it.
      const why = DRIVEABLE.has(verb)
        ? `${verb} runs from the CLI: this page has no form for it yet`
        : `${verb}: this page has no control for it`;
      const word = cell("dim", verb);
      word.title = why;
      into.append(word);
      continue;
    }
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

function options(select, values) {
  select.replaceChildren();
  for (const v of values) {
    const o = document.createElement("option");
    o.value = v;
    text(o, v);
    select.append(o);
  }
}

function showFields() {
  const wants = FIELDS[el("action").value] || [];
  for (const name of ["ref", "verb", "text"]) el(name).classList.toggle("hidden", !wants.includes(name));
  if (wants.includes("verb")) options(el("verb"), el("action").value === "challenge" ? CHALLENGE_ACTIONS : POST_VERBS);
}

function showResult(message, ok) {
  const node = el("result");
  // classList, never className: className would drop app.html's static "dim" class (the resting look) the
  // same way the comment on badge() above warns about -- this only ever adds/removes the two outcome classes.
  node.classList.remove("ok", "bad");
  node.classList.add(ok ? "ok" : "bad");
  text(node, message);
  node.title = message;   // the line truncates (app.css); the full answer stays reachable on hover
}

async function runAction() {
  const action = el("action").value;
  const wants = FIELDS[action] || [];
  const payload = { party: "human", verb: action };
  if (wants.includes("ref")) {
    const ref = el("ref").value.trim();
    payload.ref = ref;
    const n = /^#?(\d+)$/.exec(ref);
    if (n) payload.number = Number(n[1]);
  }
  if (wants.includes("text")) payload.text = el("text").value;
  // Both the header verb `post` carries and the five `challenge` actions travel as `action`: the body's own
  // `verb` key is the dispatch name (`post`, `challenge`), so a second meaning for it cannot fit in one object.
  if (wants.includes("verb")) payload.action = el("verb").value;
  showResult(`${action} …`, true);
  // One request in flight at a time: two quick Enters on a form whose submit button was still live sent two
  // identical `post`s, and a comment is not idempotent. Re-enabled in `finally`, so no failure -- a dropped
  // fetch, an unreadable body -- can leave the person with a dead button and a page they have to reload.
  const button = el("run");
  button.disabled = true;
  try {
    let response;
    try {
      response = await fetch(api("/api/action"), {
        method: "POST", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      showResult(`${action}: ${e.message}`, false);
      return;
    }
    let out = {};
    try { out = await response.json(); } catch (e) { out = {}; }
    if (response.ok) {
      showResult(`${action} ok: ${JSON.stringify(out.result)}`, true);
      pollState();                     // the server dropped its cached state; show the moved board at once
    } else if (out.refused) {
      // Verbatim, rule included: the rule is the whole content of a refusal, and summarising it would make the
      // page a second authority on what is legal.
      showResult(`refused ${out.verb} ${out.target}: ${out.rule}`, false);
    } else {
      showResult(`${action}: ${response.status} ${out.error || ""}`, false);
    }
  } finally {
    button.disabled = false;
  }
}

function isTable(value) {
  // A TOML sub-table, as tomllib hands it back: a plain object, never an array (a list is a scalar value here,
  // not a table) and never null (TOML has no null, but a stray JS null must not be walked as if it were one).
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function field(into, path, value, { readonly = false, choices = null } = {}) {
  // One control per scalar, generated from the payload: a field the schema gains appears here without a line
  // of JavaScript. A list becomes a comma-separated text box; a table is walked, never rendered as a value --
  // enforced by renderConfig's own guard below, since by the time a value reaches here it is never a table.
  const label = document.createElement("label");
  label.append(text(document.createElement("span"), path));
  let input;
  if (choices) {
    input = document.createElement("select");
    options(input, choices.includes(value) ? choices : [value, ...choices]);
    input.value = value;
  } else if (typeof value === "boolean") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = value;
  } else {
    input = document.createElement("input");
    input.type = typeof value === "number" ? "number" : "text";
    input.value = Array.isArray(value) ? value.join(", ") : String(value);
  }
  input.dataset.path = path;
  input.dataset.kind = Array.isArray(value) ? "list" : typeof value;
  if (readonly) { input.readOnly = true; input.disabled = choices != null || typeof value === "boolean"; }
  label.append(input);
  into.append(label);
}

function modelChoices(cli) {
  const specs = (configState.models || {})[cli] || [];
  return specs.map((s) => s.id);
}

function effortChoices(cli, model) {
  const spec = ((configState.models || {})[cli] || []).find((s) => s.id === model);
  return spec ? spec.efforts : [];
}

function renderConfig() {
  const into = el("config-fields");
  into.replaceChildren();
  const cfg = configState.config;
  for (const [table, values] of Object.entries(cfg)) {
    if (table === "groups") continue;
    into.append(text(document.createElement("h3"), `[${table}]`));
    for (const [key, value] of Object.entries(values)) {
      if (isTable(value)) continue;   // an unknown sub-table (e.g. a hand-added [repo.notes]) is not a field
      field(into, `${table}.${key}`, value, { readonly: READONLY_TABLES.includes(table) });
    }
  }
  for (const [name, group] of Object.entries(cfg.groups || {})) {
    into.append(text(document.createElement("h3"), `[groups.${name}]`));
    for (const [key, value] of Object.entries(group)) {
      if (key === "orchestrator" || key === "pm") continue;
      if (isTable(value)) continue;   // ditto, one level down: a group's own unknown sub-table
      field(into, `groups.${name}.${key}`, value, { readonly: READONLY_GROUP_FIELDS.includes(key) });
    }
    for (const role of ["orchestrator", "pm"]) {
      const r = group[role];
      if (!r) continue;
      field(into, `groups.${name}.${role}.model`, r.model, { choices: modelChoices(group.cli) });
      field(into, `groups.${name}.${role}.effort`, r.effort, { choices: effortChoices(group.cli, r.model) });
    }
  }
}

function collectConfig() {
  // Start from what the server gave us and replace only what the form shows, so a table the form does not
  // render -- today none, tomorrow whatever the schema gains before this page does -- rides through untouched.
  const out = JSON.parse(JSON.stringify(configState.config));
  for (const input of el("config-fields").querySelectorAll("[data-path]")) {
    if (input.readOnly || input.disabled) continue;
    const parts = input.dataset.path.split(".");
    let node = out;
    for (const part of parts.slice(0, -1)) {
      if (!isTable(node)) { node = undefined; break; }
      node = node[part];
    }
    if (!isTable(node)) continue;    // a quoted TOML key containing a literal "." does not shape-match its
                                      // dotted path -- skip this one field rather than break every other.
    const key = parts[parts.length - 1];
    if (input.dataset.kind === "boolean") node[key] = input.checked;
    else if (input.dataset.kind === "number") node[key] = Number(input.value);
    else if (input.dataset.kind === "list") node[key] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    else node[key] = input.value;
  }
  return out;
}

function showConfigResult(message, ok) {
  const node = el("config-result");
  node.classList.remove("ok", "bad");
  node.classList.add(ok ? "ok" : "bad");
  text(node, message);
  node.title = message;
}

async function loadConfig() {
  try {
    configState = await getJSON("/api/config");
    renderConfig();
    showConfigResult(`${configState.path} — ${configState.note}`, true);
  } catch (e) {
    showConfigResult(e.message, false);
  }
}

async function saveConfig() {
  if (!configState) return;
  const button = el("config-save");
  button.disabled = true;
  showConfigResult("saving …", true);
  try {
    const response = await fetch(api("/api/config"), {
      method: "PUT", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: collectConfig() }),
    });
    let out = {};
    try { out = await response.json(); } catch (e) { out = {}; }
    if (response.ok) {
      configState = out;
      renderConfig();
      showConfigResult(`saved — ${out.note}`, true);
      pollState();
    } else if (out.checks) {
      const bad = out.checks.filter((c) => c.ok === false).map((c) => `${c.name}: ${c.detail}`).join("; ");
      showConfigResult(`refused — ${bad}`, false);
    } else {
      showConfigResult(`${response.status} ${out.error || ""}`, false);
    }
  } catch (e) {
    showConfigResult(e.message, false);
  } finally {
    button.disabled = false;
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
  feed.addEventListener("end", (e) => {
    // Mark a column that exists; never create one. `columnFor` would give a retired stream a fresh column --
    // the trailing `end` frame of a log `discover` no longer lists, re-sent on every reconnect. `renderStreams`
    // bounds that ghost to two polls; not making it at all is better than removing it a moment later.
    const col = columns.get(JSON.parse(e.data).stream);
    if (col) col.root.classList.add("ended");
  });
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

let statePending = null;
async function pollState() {
  if (statePending) clearTimeout(statePending);
  try { renderState(await getJSON("/api/state")); } catch (e) { text(el("error"), e.message); }
  statePending = setTimeout(pollState, STATE_EVERY_MS);
}

async function pollStreams() {
  try { renderStreams((await getJSON("/api/streams")).streams); } catch (e) { text(el("error"), e.message); }
  setTimeout(pollStreams, STREAMS_EVERY_MS);
}

options(el("action"), Object.keys(FIELDS));
showFields();
el("action").addEventListener("change", showFields);
el("bar").addEventListener("submit", (e) => { e.preventDefault(); runAction(); });
// A row's button never acts: it fills the bar and hands the person the keyboard. One execution path, and a
// merge is never one stray click away.
el("board-panel").addEventListener("click", (e) => {
  const b = e.target.closest("button.verb");
  // Defence in depth, not the primary guard: verbControls (above) now renders a button only for a verb FIELDS
  // has an entry for, so button.verb never carries an action this bar can't spell and this can't fire in
  // practice. Kept so the two staying in sync is enforced twice, not assumed once.
  if (!b || !FIELDS[b.dataset.action]) return;
  el("action").value = b.dataset.action;
  showFields();
  el("ref").value = b.dataset.ref || "";
  el(FIELDS[b.dataset.action]?.includes("text") ? "text" : "run").focus();
});
el("config").addEventListener("toggle", () => { if (el("config").open && !configState) loadConfig(); });
el("config-save").addEventListener("click", saveConfig);

pollState();
pollStreams();
openFeed();
