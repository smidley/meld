"use strict";
// The viewer's one page: state and streams polled from the API, one column per transcript fed by EventSource.
// Every string shown here came from a model or a tool, so it only ever reaches the document as a text node.
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const KINDS = ["run_started", "message", "thinking", "tool_call", "tool_result", "usage", "run_finished",
               "usage_limit", "log_truncated", "cli_error", "raw"];
const BADGE_STATES = ["alive", "dead", "unknown", "frozen", "reconnecting"];
const STATE_EVERY_MS = 10000;
// One colour per party, so who is talking reads at a glance in the conversation and on the transcript columns:
// groups in configuration order, the human green, the framework's [joint] voice lilac.
const PALETTE = ["#58a6ff", "#f0883e", "#a371f7", "#39c5cf", "#e3b341"];
let groupOrder = [];
function colorFor(kind, group) {
  if (kind === "human") return "#3fb950";
  if (kind === "joint") return "#d2a8ff";
  const i = groupOrder.indexOf(group);
  return PALETTE[(i < 0 ? 0 : i) % PALETTE.length];
}
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
  "tag": ["ref", "tag"],
};
const POST_VERBS = ["ASK", "DIRECTIVE", "HALT", "RESUME", "REVERT", "PROVIDED", "RESOLVED", "ACK", "AMEND",
                    "APPROVE", "NEEDS-HUMAN"];
const CHALLENGE_ACTIONS = ["open", "evidence", "retract", "uphold", "dismiss"];
// What a person can do from the page, in plain words. Each intent is one request the bar sends: `action` is the dispatch
// name /api/action takes, `verb` the header verb (post) or the challenge action, `fields` what the person must give,
// `explain` what will happen, shown under the form before Run. An ASK or a DIRECTIVE carries no ref: meld numbers it.
const INTENTS = {
  ask: { label: "ask the agents a question", action: "post", verb: "ASK", fields: ["text"], text: "your question",
         explain: "Posts [human] ASK on the coordination issue; the groups answer it together as a [joint] ANSWER." },
  directive: { label: "give the agents a directive", action: "post", verb: "DIRECTIVE", fields: ["text"], text: "what they must do",
               explain: "Posts [human] DIRECTIVE; every group must ACK it, and the orchestrators treat it as overriding their plan." },
  halt: { label: "pause all work", action: "post", verb: "HALT", fields: [],
          explain: "Posts [human] HALT: no claim, dispatch, review or merge happens until you resume." },
  resume: { label: "resume work", action: "post", verb: "RESUME", fields: [], explain: "Posts [human] RESUME and lifts the halt." },
  provided: { label: "say I provided what was asked", action: "post", verb: "PROVIDED", fields: ["ref", "text"], ref: "which issue, e.g. #14",
              text: "where you put it (a variable or 1Password item), never the value",
              explain: "Posts [human] PROVIDED on that issue; it leaves needs-human on the next tick and the work resumes." },
  resolved: { label: "say something is resolved", action: "post", verb: "RESOLVED", fields: ["ref", "text"], ref: "which ref, e.g. #14 or budget-1",
              text: "what you decided", explain: "Posts [human] RESOLVED on that ref: an ask, a budget alert, or a hold on an issue." },
  revert: { label: "ask for a merged PR to be reverted", action: "post", verb: "REVERT", fields: ["ref", "text"], ref: "which PR, e.g. #21", text: "why",
            explain: "Posts [human] REVERT on that pull request; the groups revert it." },
  approve_proposal: { label: "approve a proposal as it stands", action: "post", verb: "APPROVE", fields: ["ref"], ref: "which ref, e.g. ans-1",
                      explain: "Posts [human] APPROVE on that ref: the proposer's text stands, ending a disagreement." },
  amend: { label: "amend a proposal", action: "post", verb: "AMEND", fields: ["ref", "text"], ref: "which ref, e.g. plan-add-2", text: "the amended text",
           explain: "Posts [human] AMEND with your text; the round restarts on it." },
  challenge: { label: "challenge a claim", action: "challenge", verb: "open", fields: ["ref", "text"], ref: "which issue or PR, e.g. #13", text: "what you doubt",
               explain: "Opens a CHALLENGE on it: dispatch and merge stop on that issue or PR until the challenge is settled." },
  evidence: { label: "add evidence to a challenge", action: "challenge", verb: "evidence", fields: ["ref", "text"], ref: "the challenged ref",
              text: "the evidence, with a verified-by: line", explain: "Posts EVIDENCE on the open challenge." },
  retract: { label: "withdraw a challenge", action: "challenge", verb: "retract", fields: ["ref", "text"], ref: "the challenged ref", text: "why",
             explain: "Retracts the open challenge and clears the label." },
  uphold: { label: "settle a challenge: uphold it", action: "challenge", verb: "uphold", fields: ["ref", "text"], ref: "the challenged ref", text: "your ruling",
            explain: "Upholds the challenge; the claim is treated as wrong." },
  dismiss: { label: "settle a challenge: dismiss it", action: "challenge", verb: "dismiss", fields: ["ref", "text"], ref: "the challenged ref", text: "your ruling",
             explain: "Dismisses the challenge; the claim stands and the label clears." },
  claim: { label: "claim an issue for myself", action: "claim", fields: ["ref"], ref: "which issue, e.g. #11",
           explain: "Labels the issue claimed by you; no group will dispatch it while you hold it." },
  release: { label: "release an issue", action: "release", fields: ["ref"], ref: "which issue, e.g. #11",
             explain: "Removes every claim label; the issue is ready for a group again." },
  merge: { label: "merge a pull request", action: "merge", fields: ["ref"], ref: "which PR, e.g. #21",
           explain: "Squash-merges it, if the review quorum, the checks and no open challenge all hold." },
  approve: { label: "approve the plan", action: "plan-approve", fields: [],
             explain: "Adds meld:plan-approved and makes every planned issue ready to claim." },
  ack: { label: "acknowledge a plan addition", action: "plan-ack", fields: ["ref"], ref: "which addition, e.g. plan-add-1",
         explain: "Makes that addition's issues ready." },
  tag: { label: "adjust an issue's tag: worker, model, effort, size", action: "tag", fields: ["ref", "tag"], ref: "which issue, e.g. #12",
         explain: "Rewrites the issue's worker, model, effort and size labels; a field left as is keeps its label. The next dispatch on the issue uses them, and a retry after a failed run needs a changed model or effort." },
  other: { label: "something else: post a header verb (advanced)", action: "post", fields: ["verb", "ref", "text"], ref: "ref, if the verb takes one",
           text: "your message", explain: "Posts the chosen verb as [human] on the coordination issue, exactly as `meld post` would." },
};
// A row's verb button names the dispatch name; this is the intent it opens in the bar.
const INTENT_FOR_ACTION = { claim: "claim", release: "release", merge: "merge", "plan-ack": "ack", "plan-approve": "approve",
                            post: "other", challenge: "challenge", tag: "tag" };
// Identity is `meld setup`'s, not a running page's: changing a repo, a human, or a group's bot/auth mid-milestone
// orphans that group's claims and its open PRs. Shown, never editable (spec 22). The API still accepts them, so
// what the form sends back is what it was given, with only the editable values replaced.
const READONLY_TABLES = ["repo", "humans"];
const READONLY_GROUP_FIELDS = ["cli", "bot", "auth", "token_env", "app_id", "installation_id",
                               "private_key_env", "private_key_file"];
let configState = null;     // the GET payload as last read, and the inputs' backing store

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

function boardRow(into, id, what, legal, keys, refused) {
  const row = el("row").content.firstElementChild.cloneNode(true);
  // Both tracks truncate: an agreement ref is a model-authored token of any length and a title can be long.
  // The full text stays reachable on hover, which is what the freeze badge does with its reset instant.
  const rid = text(row.querySelector(".rid"), id);
  const rwhat = text(row.querySelector(".rwhat"), what);
  rid.title = id;
  // Hovering the row also says why each verb that is missing from `legal` is missing: the rule `meld status`
  // reports under `refused`, verbatim, so a person never has to guess why there is no merge button.
  const why = Object.entries(refused || {}).map(([verb, rule]) => `${verb}: ${rule}`);
  rwhat.title = why.length ? `${what}\n\nnot legal now:\n${why.join("\n")}` : what;
  verbControls(row.querySelector(".rverbs"), legal, keys);
  into.append(row);
}

let lastBoard = "";
let lastTurn = 0;
let following = true;   // the list follows the newest turn until the reader scrolls up; "newest" resumes it
function followNewest() {
  const list = el("turns");
  list.scrollTop = list.scrollHeight;
  following = true;
  el("newest").classList.add("hidden");
}
function renderThread(turns) {
  // Append what is new by comment id; never rebuild, so the list keeps its place. New turns slide in, and the list
  // scrolls to the newest one unless the reader has scrolled up to read, in which case "newest" brings them back.
  const list = el("turns");
  let added = false;
  for (const t of turns || []) {
    if (t.id <= lastTurn) continue;
    lastTurn = t.id;
    added = true;
    const li = document.createElement("li");
    if (lastTurn > 0 && list.children.length) li.classList.add("fresh");
    // `housekeeping` and `agreement` are the server's classification (chat.thread_entries): the page keeps no verb lists.
    li.className = [t.kind, t.housekeeping && "housekeeping", t.agreement && "agreement"].filter(Boolean).join(" ");
    li.title = `comment ${t.id}`;
    li.id = `turn-${t.id}`;
    const head = document.createElement("div");
    head.className = "head";
    const party = cell("party", t.party);
    party.style.color = colorFor(t.kind, t.group);
    head.append(party, cell("verb", [t.verb, t.ref, t.qualifier].filter(Boolean).join(" ")), cell("dim", (t.at || "").slice(0, 16).replace("T", " ")));
    li.append(head, cell("body", t.text));
    list.append(li);
  }
  if (added && following) requestAnimationFrame(followNewest);
}

const LANES = [["To do", ["planned", "ready", "unlabeled"]], ["In progress", ["in_progress", "blocked", "needs_human"]],
               ["In review", ["in_review"]], ["Done", ["closed"]]];
let prevLane = new Map();   // issue number -> lane name at the last render, so a card that moved can slide in
let liveRuns = [];          // the running worker runs from the last state, by group and issue
let laneFlowUntil = new Map();   // lane name -> when its flow line stops running fast after a card arrived
function issueCard(i, lane) {
  const card = document.createElement("div");
  card.dataset.issue = String(i.number);
  const runs = liveRuns.filter((r) => r.issue === i.number && r.kind === "build");
  const pr = (lastPulls || []).find((p) => p.issue === i.number && p.state === "open");
  const checks = pr ? liveRuns.filter((r) => r.pr === pr.number && (r.kind === "review" || r.kind === "audit")) : [];
  const moved = prevLane.has(i.number) && prevLane.get(i.number) !== lane;
  if (moved) laneFlowUntil.set(lane, Date.now() + 60000);
  card.className = "icard" + (i.status === "blocked" || i.status === "needs_human" || i.challenged ? " attention" : "")
                 + (runs.length || checks.length ? " working" : "") + (moved ? " moved" : "");
  const head = document.createElement("div");
  head.className = "ihead";
  const title = cell("ititle", i.title);
  title.title = "everything that touched this issue";
  title.addEventListener("click", () => openTask(i.number));
  head.append(cell("inum", `#${i.number}`), title);
  const badges = document.createElement("div");
  badges.className = "ibadges";
  if (i.claimed_by) {
    const who = cell("iwho", i.claimed_by);
    who.style.color = colorFor("group", i.claimed_by);
    badges.append(who);
  }
  const flags = [i.worker, i.status === "planned" && "planned", i.status === "blocked" && "blocked",
                 i.status === "needs_human" && "needs a person", i.challenged && "challenged"].filter(Boolean);
  if (flags.length) badges.append(text(document.createElement("span"), (i.claimed_by ? " · " : "") + flags.join(" · ")));
  card.append(head, badges);
  for (const r of [...runs, ...checks]) {
    // Who is on it, and how far through its time that run is: a bar that fills as the deadline nears.
    const line = cell("iactivity", r.kind === "build" ? `${r.group}'s ${r.worker || "build"} worker is building this now`
                                   : `${r.group}'s ${r.worker || r.kind} worker is ${r.kind === "audit" ? "auditing" : "reviewing"} PR #${r.pr} now`);
    line.style.color = colorFor("group", r.group);
    const bar = document.createElement("div");
    bar.className = "iprogress";
    const fill = document.createElement("div");
    fill.className = "ifill";
    fill.style.width = `${Math.round(Math.min(1, runFraction(r)) * 100)}%`;
    fill.style.background = colorFor("group", r.group);
    bar.append(fill);
    card.append(line, bar);
  }
  if (pr) {
    const reviewing = liveRuns.filter((r) => r.pr === pr.number && (r.kind === "review" || r.kind === "audit")).map((r) => `${r.group} reviewing`);
    const state = [pr.approvals.length && `approved by ${pr.approvals.join(", ")}`, pr.changes_requested.length && `changes requested by ${pr.changes_requested.join(", ")}`,
                   (pr.checks === "pending" || pr.checks === "red") && `checks ${pr.checks}`, ...reviewing].filter(Boolean);
    card.append(cell("ipr", `PR #${pr.number}` + (state.length ? ` · ${state.join(" · ")}` : " · awaiting review")));
  }
  const verbs = document.createElement("div");
  verbs.className = "rverbs";
  verbControls(verbs, i.legal, { ref: `#${i.number}`, number: i.number });
  const why = Object.entries(i.refused || {}).map(([verb, rule]) => `${verb}: ${rule}`);
  card.title = why.length ? `not legal now:\n${why.join("\n")}` : "";
  card.append(verbs);
  return card;
}
let lastPulls = [];
let lastStatus = null;
let lastThread = [];
let lastGenerated = "";

function clock12(ms) {
  // A time of day the way a person says it, in the browser's own zone: "9:05 pm". The page's other stamps stay UTC
  // because they are the server's record; the timeline is read, not quoted.
  const d = new Date(ms), h = d.getHours(), m = String(d.getMinutes()).padStart(2, "0");
  return `${h % 12 || 12}:${m} ${h < 12 ? "am" : "pm"}`;
}

const ATTENTION = new Set(["NEEDS-HUMAN", "BUDGET", "PROTOCOL-VIOLATION", "CHALLENGE", "DRIFT", "HALT"]);
function drawTimeline(state) {
  // The thread as a strip: one dot per post, a lane per voice, time left to right over the last ninety minutes (or the
  // whole thread when it is shorter), so the rhythm of the conversation -- who spoke, how often, what needed a person --
  // reads at a glance. Clicking a dot shows that turn in the conversation.
  const svg = el("timeline");
  svg.replaceChildren();
  const turns = (state.thread || []).filter((t) => t.at);
  const now = Date.parse(state.generated_at || "") || Date.now();
  if (!turns.length) { svg.setAttribute("height", 0); return; }
  const span = Math.min(90 * 60 * 1000, Math.max(5 * 60 * 1000, now - Date.parse(turns[0].at)));
  const from = now - span;
  const shown = turns.filter((t) => Date.parse(t.at) >= from);
  const voices = [];
  for (const t of shown) { const v = t.group || t.kind; if (!voices.includes(v)) voices.push(v); }   // a group's two roles share a lane
  const left = 92, right = 24, laneH = 18, top = 8, width = Math.max(640, svg.parentElement.clientWidth || 640);
  const height = top + voices.length * laneH + 20;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);
  svg.setAttribute("height", height);
  const xOf = (iso) => left + ((Date.parse(iso) - from) / span) * (width - left - right);
  voices.forEach((v, i) => {
    const y = top + i * laneH + laneH / 2;
    svgText(svg, 4, y + 4, clip(v, 12), "sub");
    svgEl("line", { class: "lane", x1: left, y1: y, x2: width - right, y2: y }, svg);
  });
  // Time ticks every fifteen minutes, labelled HH:MM in UTC like every other stamp on the page.
  const step = 15 * 60 * 1000;
  for (let t = Math.ceil(from / step) * step; t <= now; t += step) {
    const x = xOf(new Date(t).toISOString());
    svgText(svg, x, height - 4, clock12(t), "tick").setAttribute("text-anchor", "middle");
  }
  svgEl("line", { class: "now", x1: width - right, y1: top, x2: width - right, y2: height - 16 }, svg);
  for (const t of shown) {
    const y = top + voices.indexOf(t.group || t.kind) * laneH + laneH / 2;
    const attention = ATTENTION.has(t.verb);
    const color = t.group ? colorFor("group", t.group) : (t.kind === "human" ? "#d29922" : "#8b949e");
    const dot = svgEl("circle", { class: "dot" + (attention ? " attention" : ""), cx: xOf(t.at), cy: y,
                                  r: attention ? 5 : (t.housekeeping ? 2.5 : 4), fill: color, "fill-opacity": t.housekeeping ? 0.45 : 1 }, svg);
    svgEl("title", {}, dot).textContent = `${clock12(Date.parse(t.at))} [${t.party}] ${t.verb}${t.ref ? " " + t.ref : ""}${t.qualifier ? " " + t.qualifier : ""}`;
    dot.style.animationDelay = `-${(t.id % 29) * 0.11}s`;   // each dot breathes on its own beat, never all together
    dot.addEventListener("click", () => showTurn(t.id));
  }
}

// ---- drawing: inline SVG built node by node (no markup strings), the page's only graphics ----
const SVG = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs, parent) {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
  if (parent) parent.append(n);
  return n;
}
function svgText(parent, x, y, value, cls) {
  const t = svgEl("text", { x, y, ...(cls ? { class: cls } : {}) }, parent);
  t.textContent = value;
  return t;
}
function clip(value, n) { const s = String(value == null ? "" : value); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
function arrowFor(svg) {
  // One arrowhead marker per drawing, named after the svg so two drawings on the page never share an id.
  const id = `${svg.id}-arrow`;
  if (!svg.querySelector(`#${id}`)) {
    const marker = svgEl("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, svgEl("defs", {}, svg));
    svgEl("path", { class: "arrow", d: "M 0 0 L 10 5 L 0 10 z" }, marker);
  }
  return `url(#${id})`;
}
function drawEdge(parent, x1, y1, x2, y2, label, { flowing = false } = {}) {
  // A cubic curve from the right edge of one node to the left edge of the next. A flowing edge carries moving dashes:
  // that is the page's picture of communication in progress, in the direction it goes. The label sits as a pill
  // three quarters of the way along, where curves into different targets have already parted.
  const mx = (x1 + x2) / 2;
  const svg = parent.ownerSVGElement || parent;
  svgEl("path", { class: "edge" + (flowing ? " flowing" : ""), d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`, "marker-end": arrowFor(svg) }, parent);
  if (label) {
    const t = 0.72, u = 1 - t;
    const px = u * u * u * x1 + 3 * u * u * t * mx + 3 * u * t * t * mx + t * t * t * x2;
    const py = u * u * u * y1 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y2;
    const w = label.length * 6.2 + 10;
    svgEl("rect", { class: "pill", x: px - w / 2, y: py - 8, width: w, height: 15, rx: 7 }, parent);
    svgText(parent, px, py + 3.5, label, "elabel").setAttribute("text-anchor", "middle");
  }
}
function drawRing(parent, cx, cy, r, fraction, color) {
  // How far through its time a run is: a thin arc that fills clockwise from the top; red once it is past its deadline.
  const circ = 2 * Math.PI * r, f = Math.min(1, Math.max(0, fraction));
  svgEl("circle", { class: "ring-track", cx, cy, r }, parent);
  svgEl("circle", { class: "ring", cx, cy, r, stroke: f >= 1 ? "#f85149" : color, "stroke-dasharray": circ, "stroke-dashoffset": circ * (1 - f),
                    transform: `rotate(-90 ${cx} ${cy})` }, parent);
}
function runFraction(r) {
  // Elapsed over allowed, against the server's clock (the state's own stamp), never the browser's.
  const start = Date.parse(r.started_at || ""), end = Date.parse(r.deadline || ""), now = Date.parse(lastGenerated || "");
  if (!(start < end) || !now) return 0;
  return (now - start) / (end - start);
}
function drawNode(parent, x, y, w, h, color, title, sub, { live = false, onclick = null, ring = null } = {}) {
  const g = svgEl("g", {}, parent);
  const rect = svgEl("rect", { class: "node" + (live ? " live" : "") + (onclick ? " clickable" : ""), x, y, width: w, height: h, rx: 6,
                               ...(color ? { stroke: color, "stroke-width": live ? 2.5 : 1.5 } : {}) }, g);
  if (color) svgEl("rect", { x: x + 1, y: y + 1, width: 5, height: h - 2, rx: 3, fill: color }, g);
  const textW = ring == null ? w : w - 26;
  svgText(g, x + 12, y + (sub ? 18 : h / 2 + 4), clip(title, Math.floor(textW / 7)));
  if (sub) svgText(g, x + 12, y + 34, clip(sub, Math.floor(textW / 6.4)), "sub");
  if (ring != null) drawRing(g, x + w - 16, y + h / 2, 9, ring, color || "#58a6ff");
  if (onclick) { g.style.cursor = "pointer"; g.addEventListener("click", onclick); }
  return { x, y, w, h };
}

function drawMap(state) {
  // The agents and what each is on: a row per group -- the group, its two roles with their liveness, then one node per
  // running worker with its issue or PR -- wired left to right. The map is the page's picture of who does what.
  const svg = el("map");
  svg.replaceChildren();
  const s = state.status || {};
  const groups = Object.keys(state.supervisors || {});
  const running = (s.runs || []).filter((r) => r.state === "running");
  const rowH = 62, left = 8, groupW = 120, roleW = 160, workW = 200, gap = 26, gapW = 76;
  const rows = Math.max(1, groups.length);
  const widest = Math.max(1, ...groups.map((g) => running.filter((r) => r.group === g).length));
  const width = left + groupW + gap + roleW + gapW + widest * (workW + 10) + 8;
  svg.setAttribute("viewBox", `0 0 ${Math.max(width, 640)} ${rows * rowH + 4}`);
  svg.setAttribute("width", Math.max(width, 640));   // drawn at its own size, never scaled up to the page width
  svg.setAttribute("height", rows * rowH + 4);
  groups.forEach((g, i) => {
    const y = i * rowH + 4, color = colorFor("group", g);
    const sup = (state.supervisors || {})[g] || {};
    const WORDS = { alive: "running", dead: "stopped", unknown: "not running here" };
    const node = drawNode(svg, left, y, groupW, 52, color, g, WORDS[sup.state] || sup.state || "", { live: sup.state === "alive" });
    const roles = ((state.processes || {})[g] || {}).roles || {};
    const rx = left + groupW + gap;
    let ri = 0;
    for (const [role, info] of Object.entries(roles)) {
      const ry = y + ri * 27;
      const liveRole = info.state === "alive";
      const r = drawNode(svg, rx, ry, roleW, 24, liveRole ? color : null, `${role} · ${info.frozen ? "frozen" : (info.state === "alive" ? "awake" : info.state)}`, null, { live: liveRole });
      drawEdge(svg, node.x + node.w, node.y + node.h / 2, r.x, r.y + r.h / 2, "", { flowing: liveRole });
      ri++;
    }
    const wx = rx + roleW + gapW;
    running.filter((r) => r.group === g).forEach((r, k) => {
      const what = r.kind === "build" ? `building #${r.issue}` : `${r.kind === "audit" ? "auditing" : "reviewing"} PR #${r.issue}`;
      const w = drawNode(svg, wx + k * (workW + 10), y + 14, workW, 36, color, what, `${r.worker || r.kind} · ${r.model || ""}`, { live: true,
                         ring: runFraction(r), onclick: r.kind === "build" ? () => openTask(r.issue) : () => showTranscript(r) });
      drawEdge(svg, rx + roleW, y + 27 + 12, w.x, w.y + w.h / 2, k === 0 ? "dispatched" : "", { flowing: true });
    });
  });
}

function drawBars(state) {
  const s = state.status || {};
  const issues = s.issues || [];
  const done = issues.filter((i) => i.status === "closed").length;
  const pct = issues.length ? Math.round((done / issues.length) * 100) : 0;
  el("progress-fill").style.width = `${pct}%`;
  text(el("progress-text"), issues.length ? `${done} of ${issues.length} issues done` : "no issues yet");
  const bars = el("budget-bars");
  bars.replaceChildren();
  const cap = (s.budget || {}).cap || {};
  for (const [group, tiers] of Object.entries((s.budget || {}).spent || {})) {
    for (const tier of Object.keys(cap)) {
      const used = (tiers || {})[tier] || 0;
      if (!used && tier !== "large") continue;
      const bar = document.createElement("div");
      bar.className = "bar";
      const label = text(document.createElement("span"), `${group} ${tier}`);
      label.className = "blabel";
      label.style.color = colorFor("group", group);
      const track = document.createElement("div");
      track.className = "track";
      const fill = document.createElement("div");
      const share = cap[tier] ? Math.min(100, Math.round((used / cap[tier]) * 100)) : 0;
      fill.className = "fill" + (share >= 90 ? " bad" : share >= 70 ? " warn" : "");
      fill.style.width = `${share}%`;
      track.append(fill);
      const t = text(document.createElement("span"), `${used} of ${cap[tier] || "?"} runs`);
      t.className = "dim";
      bar.append(label, track, t);
      bars.append(bar);
    }
  }
}

function drawTaskGraph(number) {
  // Everything that touched one issue, as a graph: the issue, each build run, the pull request, each review and audit
  // run -- left to right, a running node pulsing, a run node opening its transcript.
  const s = lastStatus;
  const svg = el("task-graph");
  svg.replaceChildren();
  const issue = (s.issues || []).find((i) => i.number === number);
  if (!issue) return;
  const runs = s.runs || [];
  const builds = runs.filter((r) => r.kind === "build" && r.issue === number);
  const prs = (s.pulls || []).filter((p) => p.issue === number);
  const checks = prs.flatMap((p) => runs.filter((r) => (r.kind === "review" || r.kind === "audit") && r.issue === p.number).map((r) => ({ ...r, pr: p.number })));
  // The third column is every run on a PR plus every audit verdict on the thread whose run is gone, one row each.
  const verdicts = prs.flatMap((p) => Object.entries(p.audited_by || {})
    .filter(([group]) => !checks.some((r) => r.kind === "audit" && r.pr === p.number && r.group === group))
    .map(([group, verdict]) => ({ pr: p.number, group, verdict })));
  const third = [...checks.map((r) => ({ run: r })), ...verdicts.map((v) => ({ verdict: v }))];
  const colW = 200, gapX = 70, rowH = 60;
  const rows = Math.max(1, builds.length, prs.length, third.length);
  const width = 4 * (colW + gapX), height = rows * rowH + 10;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", width);      // its own size: the dialog grows to it and scrolls past the viewport
  svg.setAttribute("height", height);
  const col = (i) => 10 + i * (colW + gapX);
  const y = (i, n) => 5 + i * rowH + ((rows - n) * rowH) / 2;
  const holderColor = issue.claimed_by ? colorFor("group", issue.claimed_by) : null;
  const root = drawNode(svg, col(0), y(0, 1), colW, 44, holderColor, `#${issue.number} ${issue.status.replace("_", " ")}`,
                        issue.claimed_by ? `${issue.claimed_by} · ${issue.worker || ""} · ${issue.model || ""}` : "unclaimed");
  const buildNodes = builds.map((r, i) => {
    const n = drawNode(svg, col(1), y(i, builds.length), colW, 44, colorFor("group", r.group), `${r.group} ${r.worker || "build"} · ${r.state === "running" ? "running" : (r.outcome || r.state)}`,
                       `${r.model || ""}/${r.effort || ""} · ${(r.started_at || "").slice(11, 16)}`,
                       { live: r.state === "running", ring: r.state === "running" ? runFraction(r) : null, onclick: () => showTranscript(r) });
    drawEdge(svg, root.x + root.w, root.y + root.h / 2, n.x, n.y + n.h / 2, "built by", { flowing: r.state === "running" });
    return n;
  });
  const prNodes = prs.map((p, i) => {
    const state = [p.merged ? "merged" : p.state, p.checks !== "none" && `checks ${p.checks}`].filter(Boolean).join(" · ");
    const verdicts = [p.approvals.length && `approved by ${p.approvals.join(", ")}`, p.changes_requested.length && `changes: ${p.changes_requested.join(", ")}`].filter(Boolean).join(" · ");
    const n = drawNode(svg, col(2), y(i, prs.length), colW, 44, p.merged ? "#3fb950" : null, `PR #${p.number} · ${state}`, verdicts || "awaiting review");
    const from = buildNodes[buildNodes.length - 1] || root;
    drawEdge(svg, from.x + from.w, from.y + from.h / 2, n.x, n.y + n.h / 2, "opened");
    return [p.number, n];
  });
  third.forEach((item, i) => {
    const yy = y(i, third.length);
    if (item.run) {
      const r = item.run;
      const n = drawNode(svg, col(3), yy, colW, 44, colorFor("group", r.group), `${r.group} ${r.kind} · ${r.state === "running" ? "running" : (r.outcome || r.state)}`,
                         `${r.worker || r.kind} · ${r.model || ""}/${r.effort || ""}`,
                         { live: r.state === "running", ring: r.state === "running" ? runFraction(r) : null, onclick: () => showTranscript(r) });
      const [, from] = prNodes.find(([num]) => num === r.pr) || [null, root];
      drawEdge(svg, from.x + from.w, from.y + from.h / 2, n.x, n.y + n.h / 2, r.kind === "audit" ? "audited by" : "reviewed by", { flowing: r.state === "running" });
    } else {
      const v = item.verdict;
      const n = drawNode(svg, col(3), yy, colW, 44, colorFor("group", v.group), `${v.group} audit · ${v.verdict}`, "on the thread");
      const [, from] = prNodes.find(([num]) => num === v.pr) || [null, root];
      drawEdge(svg, from.x + from.w, from.y + from.h / 2, n.x, n.y + n.h / 2, "audited by");
    }
  });
}

function treeItem(parent, who, color, metaText, run) {
  const li = document.createElement("li");
  const name = cell("who", who);
  if (color) name.style.color = color;
  li.append(name, document.createTextNode(" "), cell("meta", metaText));
  if (run) {
    const b = text(document.createElement("button"), "transcript");
    b.type = "button";
    b.addEventListener("click", () => showTranscript(run));
    li.append(b);
  }
  parent.append(li);
  return li;
}

function showTranscript(run) {
  // The transcript column of a run: the stream whose group, kind and number match. Opens the drawer, shows finished
  // columns if the run has ended, scrolls the column into view and flashes it.
  openTab("streams");
  const col = [...columns.values()].find((c) => c.meta && c.meta.group === run.group && c.meta.kind === run.kind && c.meta.issue === run.issue);
  if (!col) return;
  if (col.root.classList.contains("dead") || col.root.classList.contains("ended")) {
    el("show-finished").checked = true;
    el("streams").classList.remove("live-only");
  }
  col.root.scrollIntoView({ inline: "center", block: "nearest" });
  col.root.classList.add("flash");
  setTimeout(() => col.root.classList.remove("flash"), 1500);
  el("task").close();
}

function runLine(r) {
  const when = (r.started_at || "").slice(11, 16);
  const how = r.state === "running" ? "running" : (r.outcome || (r.timed_out ? "timed out" : r.state));
  return `${r.worker || r.kind} worker · ${r.model || "?"}/${r.effort || "?"} · ${how}${when ? ` · started ${when}` : ""}`;
}

function openTask(number) {
  // Everything that touched one issue, as a tree: the issue, its holder, each build run, its pull request with the
  // verdicts and checks, each review and audit run, and the thread turns about it.
  const s = lastStatus;
  if (!s) return;
  const issue = (s.issues || []).find((i) => i.number === number);
  if (!issue) return;
  text(el("task-title"), `#${issue.number} ${issue.title}`);
  const tree = el("task-tree");
  tree.replaceChildren();
  const root = treeItem(tree, issue.claimed_by ? `claimed by ${issue.claimed_by}` : "unclaimed", issue.claimed_by ? colorFor("group", issue.claimed_by) : null,
                        [issue.status.replace("_", " "), issue.worker && `worker ${issue.worker}`, issue.model && `${issue.model}/${issue.effort || "?"}`, issue.size && `size ${issue.size}`].filter(Boolean).join(" · "));
  const under = document.createElement("ul");
  root.append(under);
  const runs = s.runs || [];
  for (const r of runs.filter((x) => x.kind === "build" && x.issue === number)) treeItem(under, r.group, colorFor("group", r.group), runLine(r), r);
  const prs = (s.pulls || []).filter((p) => p.issue === number);
  for (const p of prs) {
    const state = [p.merged ? "merged" : p.state, p.checks !== "none" && `checks ${p.checks}`, p.approvals.length && `approved by ${p.approvals.join(", ")}`,
                   p.changes_requested.length && `changes requested by ${p.changes_requested.join(", ")}`].filter(Boolean).join(" · ");
    const pr = treeItem(under, `PR #${p.number}`, null, state);
    const below = document.createElement("ul");
    pr.append(below);
    for (const r of runs.filter((x) => (x.kind === "review" || x.kind === "audit") && x.issue === p.number)) {
      treeItem(below, `${r.group} ${r.kind}`, colorFor("group", r.group), runLine(r), r);
    }
    for (const [group, verdict] of Object.entries(p.audited_by || {})) treeItem(below, `${group} audit`, colorFor("group", group), verdict);
  }
  const refs = new Set([`#${number}`, ...prs.map((p) => `#${p.number}`)]);
  const turns = lastThread.filter((t) => refs.has(t.ref));
  if (turns.length) {
    const said = treeItem(under, "on the coordination issue", null, `${turns.length} turn${turns.length === 1 ? "" : "s"}`);
    const list = document.createElement("ul");
    said.append(list);
    for (const t of turns.slice(-8)) {
      const li = treeItem(list, t.party, colorFor(t.kind, t.group), `${t.verb} ${t.ref} · ${(t.at || "").slice(11, 16)}`);
      const b = text(document.createElement("button"), "show");
      b.type = "button";
      b.addEventListener("click", () => { el("task").close(); showTurn(t.id); });
      li.append(b);
    }
  }
  drawTaskGraph(number);
  el("task").showModal();
}

function renderBoard(s) {
  // Rebuilding every row on every poll swallowed a click that landed mid-rebuild and dropped focus from a control;
  // most polls change nothing, so a board identical to the one already drawn is left alone.
  const key = JSON.stringify([s.issues, s.pulls, s.coordination && s.coordination.agreements, s.runs]);
  if (key === lastBoard) return;
  lastBoard = key;
  liveRuns = (s.runs || []).filter((r) => r.state === "running");
  lastPulls = s.pulls || [];
  lastStatus = s;
  // The work, as four lanes an issue moves through; a card is the issue, its holder in that group's colour, and
  // its verb buttons (hover the card for why the others are missing).
  const issues = el("issues");
  // Where every card was before the rebuild, so one that changed lane can be seen travelling there (FLIP: draw it in
  // its new place, then animate from the old place to none).
  const before = new Map([...issues.querySelectorAll(".icard")].map((c) => [c.dataset.issue, c.getBoundingClientRect()]));
  issues.replaceChildren();
  for (const [name, statuses] of LANES) {
    const lane = document.createElement("div");
    lane.className = "lane";
    const items = (s.issues || []).filter((i) => statuses.includes(i.status));
    lane.append(text(document.createElement("h3"), `${name} · ${items.length}`));
    const flow = document.createElement("div");
    flow.className = "flowline";
    flow.title = "work flows left to right; the line runs fast for a minute after a card arrives here";
    lane.append(flow);
    for (const i of items) lane.append(issueCard(i, name));
    flow.classList.toggle("flowing", (laneFlowUntil.get(name) || 0) > Date.now());
    issues.append(lane);
  }
  for (const card of issues.querySelectorAll(".icard.moved")) {
    const old = before.get(card.dataset.issue);
    if (!old || typeof card.animate !== "function") continue;
    const now = card.getBoundingClientRect();
    const dx = old.left - now.left, dy = old.top - now.top;
    if (dx || dy) card.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], { duration: 800, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
  }
  prevLane = new Map((s.issues || []).map((i) => [i.number, (LANES.find(([, statuses]) => statuses.includes(i.status)) || [""])[0]]));
  const pulls = el("pulls");
  pulls.replaceChildren();
  for (const p of s.pulls || []) {
    // A failing or pending check is the usual reason a merge button is missing, so the row says so.
    const how = [p.merged ? "merged" : p.state, p.issue && `closes #${p.issue}`, !p.merged && (p.checks === "pending" || p.checks === "red") && `checks ${p.checks}`,
                 p.approvals.length && `approved by ${p.approvals.join(", ")}`,
                 p.changes_requested.length && `changes requested by ${p.changes_requested.join(", ")}`];
    boardRow(pulls, `#${p.number}`, `${p.title} — ${how.filter(Boolean).join(", ")}`, p.legal, { ref: `#${p.number}`, number: p.number }, p.refused);
  }
  const agreements = el("agreements");
  agreements.replaceChildren();
  for (const [ref, a] of Object.entries((s.coordination && s.coordination.agreements) || {})) {
    const settled = a.joint_verified ? `${a.status} (verified)` : a.status;
    boardRow(agreements, ref, [a.proposer && a.summary ? `${a.proposer}: ${a.summary}` : a.summary, settled].filter(Boolean).join(" — "), a.legal, { ref });
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

async function fillTagChoices() {
  // The tag form offers every CLI's models from the same catalogue the configuration form shows, grouped by CLI: a
  // person tags an issue for whichever group will claim it, and `meld tag` validates a human's choice against nothing.
  if (!configState) await loadConfig();
  const model = el("tag-model");
  if (model.options.length > 1) return;
  for (const [cli, specs] of Object.entries((configState && configState.models) || {})) {
    const group = document.createElement("optgroup");
    group.label = cli;
    for (const spec of specs) {
      const o = document.createElement("option");
      o.value = `${cli}|${spec.id}`;
      text(o, spec.id);
      group.append(o);
    }
    model.append(group);
  }
  model.addEventListener("change", () => {
    const [cli, id] = (model.value || "|").split("|");
    const effort = el("tag-effort");
    effort.replaceChildren();
    const keep = document.createElement("option");
    keep.value = "";
    text(keep, "effort: leave as is");
    effort.append(keep);
    for (const e of id ? effortChoices(cli, id) : []) {
      const o = document.createElement("option");
      o.value = e;
      text(o, e);
      effort.append(o);
    }
  });
}

function showFields() {
  const intent = INTENTS[el("action").value] || INTENTS.other;
  for (const name of ["ref", "verb", "text", "tag"]) el(name).classList.toggle("hidden", !intent.fields.includes(name));
  if (intent.fields.includes("verb")) options(el("verb"), POST_VERBS);
  if (intent.fields.includes("tag")) fillTagChoices();
  el("ref").placeholder = intent.ref || "which issue or PR, e.g. #13";
  el("text").placeholder = intent.text || "your message";
  text(el("explain"), intent.explain);
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

async function postAction(payload) {
  // The page's one write (spec 22): POST /api/action as the human. Returns what to tell the person: a success
  // with the verb's result, a refusal verbatim (rule included -- summarising it would make the page a second
  // authority on what is legal), or the transport's own failure.
  let response;
  try {
    response = await fetch(api("/api/action"), { method: "POST", cache: "no-store",
                                                headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  } catch (e) {
    return { ok: false, message: `${payload.verb}: ${e.message}` };
  }
  let out = {};
  try { out = await response.json(); } catch (e) { out = {}; }
  if (response.ok) return { ok: true, message: `${payload.verb} ok: ${JSON.stringify(out.result)}` };
  if (out.refused) return { ok: false, message: `refused ${out.verb} ${out.target}: ${out.rule}` };
  return { ok: false, message: `${payload.verb}: ${response.status} ${out.error || ""}` };
}

function showTurn(id) {
  // A card's title jumps to the thread turn it came from; a turn the filter hides is shown by widening the filter.
  const li = document.getElementById(`turn-${id}`);
  if (!li) return;
  if (li.offsetParent === null) {
    el("convo-filter").value = "all";
    el("conversation").dataset.filter = "all";
  }
  li.scrollIntoView({ block: "center" });
  li.classList.add("flash");
  setTimeout(() => li.classList.remove("flash"), 1500);
}

let lastDecisions = "";
function renderDecisions(list) {
  // Rebuilt only when the set changes, so a message being typed into a card survives the ten-second poll.
  const key = JSON.stringify(list || []);
  if (key === lastDecisions) return;
  lastDecisions = key;
  const root = el("decisions");
  root.replaceChildren();
  root.classList.toggle("hidden", !(list && list.length));
  for (const d of list || []) {
    const card = el("card").content.firstElementChild.cloneNode(true);
    const title = text(card.querySelector(".ctitle"), d.age_minutes != null ? `${d.title} (${d.age_minutes} min)` : d.title);
    if (d.comment_id) {
      title.classList.add("link");
      title.title = "show this in the conversation";
      title.addEventListener("click", () => showTurn(d.comment_id));
    }
    text(card.querySelector(".csummary"), d.summary);
    const choices = card.querySelector(".cchoices");
    const outcome = card.querySelector(".coutcome");
    let input = null;
    if (d.choices.some((c) => c.needs_text)) {
      input = document.createElement("input");
      input.placeholder = (d.choices.find((c) => c.hint) || {}).hint || "your message";
      input.title = input.placeholder;
      choices.append(input);
    }
    for (const c of d.choices) {
      const b = text(document.createElement("button"), c.label);
      b.type = "button";
      b.addEventListener("click", async () => {
        const payload = { party: "human", ...c.body };
        if (c.needs_text) {
          const message = (input && input.value.trim()) || "";
          if (!message) { text(outcome, `${c.label} needs a message`).className = "coutcome bad"; return; }
          payload.text = message;
        }
        for (const other of choices.querySelectorAll("button")) other.disabled = true;
        text(outcome, `${c.label} …`).className = "coutcome dim";
        const result = await postAction(payload);
        text(outcome, result.message).className = "coutcome " + (result.ok ? "ok" : "bad");
        if (result.ok) pollState(); else for (const other of choices.querySelectorAll("button")) other.disabled = false;
      });
      choices.append(b);
    }
    root.append(card);
  }
}

async function runAction() {
  const intent = INTENTS[el("action").value] || INTENTS.other;
  const action = intent.action;
  const payload = { party: "human", verb: action };
  if (intent.fields.includes("ref")) {
    const ref = el("ref").value.trim();
    payload.ref = ref;
    const n = /^#?(\d+)$/.exec(ref);
    if (n) payload.number = Number(n[1]);
  }
  if (intent.fields.includes("text")) payload.text = el("text").value;
  if (intent.fields.includes("tag")) {
    const model = (el("tag-model").value || "|").split("|")[1];
    if (model) payload.model = model;
    for (const [key, id] of [["effort", "tag-effort"], ["size", "tag-size"], ["worker", "tag-worker"]]) {
      const value = el(id).value.trim();
      if (value) payload[key] = value;
    }
  }
  // Both the header verb `post` carries and the five `challenge` actions travel as `action`: the body's own
  // `verb` key is the dispatch name (`post`, `challenge`), so a second meaning for it cannot fit in one object.
  if (intent.verb) payload.action = intent.verb;
  else if (intent.fields.includes("verb")) payload.action = el("verb").value;
  showResult(`${action} …`, true);
  // One request in flight at a time: two quick Enters on a form whose submit button was still live sent two
  // identical `post`s, and a comment is not idempotent. Re-enabled in `finally`, so no failure -- a dropped
  // fetch, an unreadable body -- can leave the person with a dead button and a page they have to reload.
  const button = el("run");
  button.disabled = true;
  try {
    const result = await postAction(payload);
    showResult(result.message, result.ok);
    if (result.ok) pollState();        // the server dropped its cached state; show the moved board at once
  } finally {
    button.disabled = false;
  }
}

function isTable(value) {
  // A TOML sub-table, as tomllib hands it back: a plain object, never an array (a list is a scalar value here,
  // not a table) and never null (TOML has no null, but a stray JS null must not be walked as if it were one).
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function unknownPaths() {
  // The dotted paths GET named as keys the schema does not define. Every save is refused until they are
  // gone from the *file* -- blanking the box does not remove a key -- so the form marks them rather than
  // rendering them as ordinary settings a person could reasonably expect to be able to fix here.
  return new Set(configState && configState.unknown ? configState.unknown : []);
}

function isUnknownPath(path) {
  // A whole table the schema does not know is reported as the table (`notes`), not key by key -- the same
  // word the server's own 400 uses -- so a field inside one is unknown by inheritance: `notes.kept` is
  // marked because `notes` is. Splitting the field's own dotted path is safe enough for a visual mark
  // (`field` built it by joining in the first place); nothing is walked or written from it.
  const unknown = unknownPaths();
  const parts = path.split(".");
  for (let i = 1; i <= parts.length; i++) if (unknown.has(parts.slice(0, i).join("."))) return true;
  return false;
}

function field(into, path, value, { readonly = false, choices = null, label = path } = {}) {
  // One control per scalar, generated from the payload: a field the schema gains appears here without a line
  // of JavaScript. A list becomes a comma-separated text box; a table is walked, never rendered as a value --
  // enforced by renderConfig's own guard below, since by the time a value reaches here it is never a table.
  // `label` is what's shown -- relative to the heading above it (renderConfig passes it), so two sibling
  // fields that only differ in their last segment (groups.claude.orchestrator.model vs .effort) don't both
  // truncate to the same visible text. `path`, the full dotted form, is unaffected: it is still what's in
  // dataset.path (collectConfig's own walk) and in the title (the full path stays reachable on hover).
  const labelEl = document.createElement("label");
  const span = text(document.createElement("span"), label);
  span.title = path;
  labelEl.append(span);
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
    if (input.type === "number") {
      // For the person's sake only -- `config.py`'s floors are the authority, and they differ per field (0
      // for an allowance like a budget or a lease grace, 1 for anything the framework counts by). `min` is
      // therefore the weakest floor any field has rather than a guess at this one's: a value below its own
      // floor still comes back named, from the parser. `step` is what keeps the spinner from walking a
      // setting into a fraction, which `int()` used to truncate silently and the parser now refuses.
      input.min = "0";
      input.step = "1";
    }
    input.value = Array.isArray(value) ? value.join(", ") : String(value);
  }
  input.dataset.path = path;
  input.dataset.kind = Array.isArray(value) ? "list" : typeof value;
  if (readonly) { input.readOnly = true; input.disabled = choices != null || typeof value === "boolean"; }
  if (isUnknownPath(path)) {
    // Findable, and honest about why: this one is the reason the whole form refuses to save.
    labelEl.classList.add("unknown");
    span.title = `${path} — not a setting meld knows; remove it from the file by hand, or no save can pass`;
  }
  labelEl.append(input);
  into.append(labelEl);
  return input;
}

function modelChoices(cli) {
  const specs = (configState.models || {})[cli] || [];
  return specs.map((s) => s.id);
}

function effortChoices(cli, model) {
  const spec = ((configState.models || {})[cli] || []).find((s) => s.id === model);
  return spec ? spec.efforts : [];
}

function linkModelAndEffort(modelInput, effortInput, cli) {
  // spec 22: "model and effort choices that cannot be wrong rather than rejecting them afterwards" -- a model
  // change must refresh its sibling effort choices too, or the two fall out of sync (the effort stays valid
  // for the *old* model) and config_checks 409s on a combination this page itself just offered.
  modelInput.addEventListener("change", () => {
    const choices = effortChoices(cli, modelInput.value);
    // an unrecognised model (e.g. "gpt-x") has no efforts to offer -- keep the current value, which is all
    // there is, exactly as the initial render already falls back for an unrecognised model or effort.
    const keep = choices.length === 0 ? effortInput.value
      : choices.includes(effortInput.value) ? effortInput.value : choices[0];
    options(effortInput, choices.includes(keep) ? choices : [keep, ...choices]);
    effortInput.value = keep;
  });
}

function renderConfig() {
  const into = el("config-fields");
  into.replaceChildren();
  const cfg = configState.config;
  for (const [table, values] of Object.entries(cfg)) {
    if (table === "groups") continue;
    // an unknown table that holds nothing but sub-tables (e.g. a hand-added [notes.today]) renders no fields
    // below -- a heading over nothing is worse than no heading, so it is skipped too.
    const entries = Object.entries(values).filter(([, value]) => !isTable(value));
    if (!entries.length) continue;
    into.append(text(document.createElement("h3"), `[${table}]`));
    for (const [key, value] of entries) {
      // The heading above already names the table, so the label shows only the key -- see field()'s own
      // `label` option. `path` (below) still carries the full dotted form for dataset.path and the title.
      field(into, `${table}.${key}`, value, { readonly: READONLY_TABLES.includes(table), label: key });
    }
  }
  for (const [name, group] of Object.entries(cfg.groups || {})) {
    const entries = Object.entries(group).filter(([key, value]) => key !== "orchestrator" && key !== "pm" && !isTable(value));
    const roles = ["orchestrator", "pm"].filter((role) => group[role]);
    if (!entries.length && !roles.length) continue;   // ditto, one level down: a group with nothing to show
    into.append(text(document.createElement("h3"), `[groups.${name}]`));
    for (const [key, value] of entries) {
      field(into, `groups.${name}.${key}`, value, { readonly: READONLY_GROUP_FIELDS.includes(key), label: key });
    }
    for (const role of roles) {
      const r = group[role];
      // Relative to [groups.<name>], not to the role: "orchestrator.model" and "orchestrator.effort" are
      // still distinct after truncation, which "groups.claude.orche…" for both was not (R25).
      const modelInput = field(into, `groups.${name}.${role}.model`, r.model,
        { choices: modelChoices(group.cli), label: `${role}.model` });
      const effortInput = field(into, `groups.${name}.${role}.effort`, r.effort,
        { choices: effortChoices(group.cli, r.model), label: `${role}.effort` });
      linkModelAndEffort(modelInput, effortInput, group.cli);
    }
  }
}

function collectConfig() {
  // Start from what the server gave us and replace only what the form shows, so a table the form does not
  // render -- today none, tomorrow whatever the schema gains before this page does -- rides through untouched.
  const out = JSON.parse(JSON.stringify(configState.config));
  const invalid = [];
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
    else if (input.dataset.kind === "number") {
      // Number("") is 0, not NaN, and Chrome hands back "" from <input type=number> for typed garbage
      // ("26x") too -- so neither a cleared field nor a mistyped one is caught by Number.isFinite alone.
      // Both must be checked, and neither is an edit: writing 0 for either would be a silent, dangerous
      // guess (a zeroed concurrency stops all dispatch; a zeroed poll interval is a zero-sleep loop).
      // Left untouched here -- saveConfig refuses the whole save locally rather than send either one.
      const n = Number(input.value);
      if (input.value.trim() === "" || !Number.isFinite(n)) { invalid.push(input.dataset.path); continue; }
      node[key] = n;
    }
    else if (input.dataset.kind === "list") node[key] = input.value.split(",").map((s) => s.trim()).filter(Boolean);
    else node[key] = input.value;
  }
  return { config: out, invalid };
}

function showConfigResult(message, ok) {
  // ok is true (success, green), false (refusal, red), or null (in progress -- neither, so "saving …" is not
  // painted as if it had already succeeded).
  const node = el("config-result");
  node.classList.remove("ok", "bad");
  if (ok === true) node.classList.add("ok");
  else if (ok === false) node.classList.add("bad");
  text(node, message);
  node.title = message;
}

function configTrouble() {
  // The two states GET now reports that block every save, whatever the person changes. Both are
  // spec-mandated behaviour correctly implemented -- what was missing is saying so on the read, instead of
  // letting the first Save be where a person discovers the file has to be hand-edited first.
  const out = [];
  if (configState.error) {
    // This used to promise that no save could succeed until the file itself was opened and corrected by
    // hand, which is false: it was written when reaching this fallback meant a structurally sparser file,
    // and since every numeric setting gained a floor, nearly every way to reach it is a value out of range
    // on a field the raw parse still renders -- so the person can very often fix it right here and save.
    // Promise only what is true: a save is refused until the problem is fixed, wherever that turns out to
    // be. (The negative pin in the viewer suite keeps the dead wording from coming back, so do not quote
    // it here.)
    out.push(`this file does not parse, so the form below is the raw file rather than the full schema`
             + `, and a save is refused until the problem is fixed: ${configState.error}`
             + ` — change it here if the form shows that field, or edit the file by hand if it does not`);
  }
  const unknown = configState.unknown || [];
  if (unknown.length) {
    out.push(`no save can pass while the file carries ${unknown.length === 1 ? "a setting" : "settings"} `
             + `meld does not know: ${unknown.join(", ")} — remove ${unknown.length === 1 ? "it" : "them"} `
             + `from the file by hand; clearing the box here does not`);
  }
  return out.join("; ");
}

async function loadConfig() {
  try {
    configState = await getJSON("/api/config");
    renderConfig();
    const trouble = configTrouble();
    if (trouble) showConfigResult(`${configState.path} — ${trouble}`, false);
    else showConfigResult(`${configState.path} — ${configState.note}`, true);
  } catch (e) {
    showConfigResult(e.message, false);
  }
}

async function saveConfig() {
  if (!configState) return;
  const button = el("config-save");
  button.disabled = true;
  showConfigResult("saving …", null);
  try {
    const { config, invalid } = collectConfig();
    if (invalid.length) {
      // Not an edit (see collectConfig): refused here, before anything is sent, so a blank or mistyped
      // number field never becomes a silent 0 on the file.
      showConfigResult(`fix before saving -- not a number: ${invalid.join(", ")}`, false);
      return;
    }
    const response = await fetch(api("/api/config"), {
      method: "PUT", cache: "no-store",
      headers: { "Content-Type": "application/json" },
      // The version GET gave us: the server refuses the save with a 409 if the file has changed since,
      // rather than reverting a hand edit this panel never saw. A successful save re-renders from the
      // answer, which carries the new version, so the next save from the same panel is current again.
      body: JSON.stringify({ config, version: configState.version }),
    });
    let out = {};
    try { out = await response.json(); } catch (e) { out = {}; }
    if (response.ok) {
      // Render the new state before committing it: a 200 with an unexpected body (not reachable through the
      // real server today, but cheap to guard) must not leave configState pointing at something renderConfig
      // cannot show -- that combination is what previously left the form blank until a full page reload.
      const next = out;
      try {
        configState = next;
        renderConfig();
      } catch (e) {
        configState = null;   // reopening the panel now reloads instead of showing a permanently blank form
        throw e;
      }
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

  text(el("headline"), state.headline || "");
  groupOrder = Object.keys(state.supervisors || {});
  lastThread = state.thread || [];
  lastGenerated = state.generated_at || "";
  lastStatus = state.status;
  drawBars(state);
  drawMap(state);
  drawTimeline(state);
  renderDecisions(state.decisions);
  renderThread(state.thread);
  // The agents, one chip per group in its colour: its supervisor in plain words (the reason on hover), and any frozen role.
  const WORDS = { alive: "running", dead: "stopped", unknown: "not running here" };
  const sup = el("supervisors");
  sup.replaceChildren();
  for (const [group, live] of Object.entries(state.supervisors)) {
    const span = document.createElement("span");
    span.className = "agent";
    span.title = live.reason;
    const dot = cell("dot", "●");
    dot.style.color = colorFor("group", group);
    span.append(dot, text(document.createElement("span"), group), badge(document.createElement("span"), live.state, WORDS[live.state] || live.state));
    // what the group is doing right now, from its running worker runs
    const busy = (s.runs || []).filter((r) => r.group === group && r.state === "running");
    if (busy.length) {
      span.classList.add("busy");
      span.append(cell("doing", busy.map((r) => r.kind === "build" ? `building #${r.issue}` : `${r.kind === "audit" ? "auditing" : "reviewing"} PR #${r.pr}`).join(", ")));
    }
    for (const [role, freeze] of Object.entries(state.frozen[group] || {})) {
      if (freeze) {
        const f = badge(document.createElement("span"), "frozen", `${role} frozen`);
        f.title = freeze.resets_at ? `resets ${freeze.resets_at}` : `since ${freeze.at}, re-probing`;
        span.append(" ", f);
      }
    }
    sup.append(span);
  }
  // The needs-human items are decision cards now (plan 18); this row carries only the state errors below.
  const nh = el("needs-human");
  nh.replaceChildren();
  // The non-fatal read problems `meld status` warns a PM about (a corrupt run record, an unreadable result file).
  for (const err of s.state_errors || []) {
    const line = text(document.createElement("span"), `state error: ${err}`);
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
  list.append(li);
  if (col.following) list.scrollTop = list.scrollHeight;
}

function scrollColumns() {
  // Every column that is following shows its newest line: called when the drawer opens and when a view option changes,
  // because a column that filled while hidden has no scroll position of its own to keep.
  for (const col of columns.values()) if (col.following) col.list.scrollTop = col.list.scrollHeight;
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
            listed: false, liveness: { state: "unknown", reason: "not listed yet" }, following: true };
    // A column follows its newest line until the reader scrolls it up; scrolling back to the bottom resumes.
    col.list.addEventListener("scroll", () => { col.following = col.list.scrollHeight - col.list.scrollTop - col.list.clientHeight < 40; });
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
    col.meta = s;
    text(col.who, s.kind === "role" ? `${s.group} / ${s.role}` : `${s.group} / ${s.kind} #${s.issue}`);
    col.who.style.color = colorFor("group", s.group);
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
  // Live first: alive columns, then unknown, then dead, in that order on the page; a dead column is also classed so
  // "show finished" can hide it. Re-appending moves a column, so the order follows liveness as it changes.
  const rank = { alive: 0, unknown: 1, dead: 2 };
  const ordered = [...columns.values()].sort((a, b) => (rank[a.liveness.state] ?? 1) - (rank[b.liveness.state] ?? 1));
  for (const col of ordered) {
    badge(col.live, col.liveness.state);
    text(col.why, col.liveness.reason);
    col.root.classList.toggle("dead", col.liveness.state === "dead");
    el("streams").append(col.root);
  }
  scrollColumns();
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

el("convo-filter").addEventListener("change", (e) => { el("conversation").dataset.filter = e.target.value; followNewest(); });
el("turns").addEventListener("scroll", () => {
  const list = el("turns");
  following = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  el("newest").classList.toggle("hidden", following);
});
el("newest").addEventListener("click", followNewest);
el("streams").classList.add("narration", "live-only");
el("task-close").addEventListener("click", () => el("task").close());
el("show-finished").addEventListener("change", (e) => { el("streams").classList.toggle("live-only", !e.target.checked); scrollColumns(); });
// The drawer: one tab open at a time, or none; the Configuration tab opens its <details>, whose toggle loads the form.
function openTab(name) {
  for (const b of document.querySelectorAll("#tabs .tab")) b.classList.toggle("active", b.dataset.tab === name);
  for (const pane of el("drawer").querySelectorAll(".tabpane")) pane.classList.toggle("hidden", pane.id !== name);
  el("drawer").classList.toggle("closed", !name);
  if (name === "config") el("config").open = true;
  if (name === "streams") scrollColumns();
}
for (const b of document.querySelectorAll("#tabs .tab")) {
  b.addEventListener("click", () => openTab(b.classList.contains("active") ? null : b.dataset.tab));
}
el("show-tools").addEventListener("change", (e) => { el("streams").classList.toggle("narration", !e.target.checked); scrollColumns(); });
for (const [key, intent] of Object.entries(INTENTS)) {
  const o = document.createElement("option");
  o.value = key;
  text(o, intent.label);
  el("action").append(o);
}
showFields();
el("action").addEventListener("change", showFields);
el("bar").addEventListener("submit", (e) => { e.preventDefault(); runAction(); });
// A row's button never acts: it fills the bar and hands the person the keyboard. One execution path, and a
// merge is never one stray click away.
// Verb buttons live in the work lanes and in two drawer tabs, so the delegate is the document; the bar sits in the
// "Act as the human" tab, which opens so the filled bar is in view.
document.addEventListener("click", (e) => {
  const b = e.target.closest("button.verb");
  // Defence in depth, not the primary guard: verbControls (above) now renders a button only for a verb FIELDS
  // has an entry for, so button.verb never carries an action this bar can't spell and this can't fire in
  // practice. Kept so the two staying in sync is enforced twice, not assumed once.
  if (!b || !FIELDS[b.dataset.action]) return;
  openTab("act");
  el("action").value = INTENT_FOR_ACTION[b.dataset.action] || "other";
  showFields();
  el("ref").value = b.dataset.ref || "";
  el(FIELDS[b.dataset.action]?.includes("text") ? "text" : "run").focus();
});
el("config").addEventListener("toggle", () => { if (el("config").open && !configState) loadConfig(); });
el("config-save").addEventListener("click", saveConfig);

pollState();
pollStreams();
openFeed();
