# meld dashboard: usability and information-architecture review

Grounded in `app.html`, `app.css`, `app.js`, `_build_state` in `server.py`, `chat.py` (`headline`, `decisions`, `journey`, `progress_line`, `thread_entries`) and the three screenshots. "The person" is whoever leaves this page open for weeks.

## 1. The person's jobs, ranked

| # | Job | Where the answer is today | Time to find | What gets in the way |
|---|-----|---------------------------|--------------|----------------------|
| 1 | Is anything waiting on me? | Last clause of `#headline` ("…; nothing waits on you."), and the `#decisions` panel, which only exists when non-empty and sits under the map, timeline and setup lines. | 3–6 s | The answer is the *end* of a 110-character sentence. In `board-questions-chat.jpg` the headline says "nothing waits on you" while three questions sit below it; whatever the cause (stale render or a bug), the page has two sources of truth for the one thing the person must not miss. |
| 2 | Is it moving? | "0 workers running" mid-headline; pulsing rings on `#map`; breathing dots on `#timeline`; `.icard.working` pulse. | 5–10 s, often wrong | Nothing says *when* something last happened. Every timeline dot "breathes" whether the run is live or three hours old, so motion carries no information. The real story in the 1680 screenshot, "pm · dead · launch 39" (codex's PM has been relaunched 39 times), is a clipped 24-px node and is not flagged anywhere. |
| 3 | Is any agent stuck or out of quota? | `#limits` frozen chips ("grok pm frozen · next try 5:09 pm"), map node text, red `cli_error` lines inside the Transcripts drawer, `#setup` yellow lines. | 10 s to never | Four places, none at the top. "Selected model is at capacity" is visible only with the drawer open. `#supervisors` (which carries `live.reason` and the frozen badges) is built on every poll but `class="hidden"` is never removed: it is dead code. |
| 4 | Where is each piece of work? | The Work lanes and the seven-step journey. | 3–5 s | Lanes bucket by label (`LANES`), not by journey step, so #3 with "PR #12 · changes requested" sits in In progress while "IN REVIEW · 0". Each card then says the same thing three times (strip, "now: … · then built, pull request, reviewed…", "PR #12 · changes requested by codex"). |
| 5 | What are the agents saying? | `#conversation`, default filter "the conversation". | 10–30 s per post | Posts are 8–12 lines of model prose with hashes, comment ids and `verified-by:` lines, in a 12rem box that scrolls inside a list that scrolls. Headers read "[codex] ACK #12". |
| 6 | How much is being spent? | Budget bars (runs per tier), `#limits` (tokens, $ for claude only), and the footer "spent: claude/medium 1, … · cap: small 200, medium 80, large 20". | 5 s, three places | Three renderings of overlapping data; dollars appear once, for one group. There is no quota ceiling in `limits.usage`, so the "token-limit monitor" the owner asked for cannot be a bar today, only a freeze notice. |

## 2. Findings, by severity

**F1. The thing the person must act on is not the first thing on the page.** The header is: repo line, headline sentence, three bar rows, a usage line, the agents map (120–250 px), the timeline (~100 px), setup lines, and *then* Questions for you. Fix: one status strip at the top (section 4), with the Questions panel directly under it whenever it is non-empty. Everything else moves below the lanes or into the drawer.

**F2. The headline is a sentence, not a status.** `headline()` concatenates counts ("issues: 1 in progress, 1 ready, 1 closed; 0 workers running; 1 open PR; nothing waits on you") and the only colour is the "Executing" badge. Replace with the strip in section 4, computed server-side next to `headline()` so `meld chat` and the page agree.

**F3. Stopped and crash-looping roles are not alarms.** `processes[g].roles[role]` carries `state`, `launch_no`, `reason`, `frozen`; `supervisors[g]` carries `state` and `reason`. Today they render as node text ("pm · dead · launch 39") and a never-shown chip row. Fix: the strip turns red and names the role; the agent row (F5) shows "codex PM stopped, restarted 38 times" with `reason` on hover; delete the `#supervisors` block.

**F4. The map does not earn 250 px at the top.** It draws, per group, three boxes and two curves; the only facts are liveness and the running workers, both of which the lanes already show on the cards ("codex's frontend worker is building this now"). The pill "dispatched" and the dashed flow are decoration. Fix: replace with one compact row per group: `● claude  running · orchestrator ok · PM ok · building #3 · $28.41 · 904k in / 256k out`. Keep the per-issue graph in the task dialog, where a graph of one issue's runs is useful.

**F5. The timeline is noise at rest.** Ninety minutes of dots, every dot animated (`#timeline .dot { animation: breathe }`), housekeeping dots at 45 % opacity, lanes labelled with raw party names (`joint`). In the playground screenshot it is an empty strip with five tick labels. It answers "who posted when", which the Conversation already answers with timestamps. Fix: remove it. If the owner wants a pulse, put "last post 12:41 pm (3 min ago)" in the strip; `thread[].at` and `runs[].started_at` support that.

**F6. Cards repeat themselves and bucket wrongly.** Fix: the first line of a card is the issue, the second is *one* sentence: `journey.now`. Drop the `then …` tail (the strip already shows what is left), drop the `.ipr` line (the sentence names the PR), keep verb buttons. Bucket lanes by the journey's `now` step (claimed/built → In progress; pull request/reviewed → In review; merged/audited → Done; nothing done → To do), so "In review" counts open PRs.

**F7. Long agent posts are unreadable.** `#turns .body` is `max-height: 12rem; overflow-y: auto` inside `#turns` which also scrolls: a scroll trap. Fix: show the first three lines, a "show all" toggle per post (a `<details>` does it), no inner scrollbar. Group consecutive posts by the same party within a few minutes under one header. Strip `verified-by:` lines the way `_lines()` already does for decisions. Colour-code the party chip only; do not colour the body.

**F8. The questions area selects the newest, hides the box for button-only questions, and speaks in verbs.** `renderDecisions` selects `list[list.length-1]`, so the 25-minute-old merge-conflict question is unselected behind an 8-minute-old challenge. `renderReplyBox` hides the whole form when the selected question has no typed reply, so the box pops in and out as the person clicks bubbles. Buttons read "Send as uphold" / "Send as dismiss"; the summary reads "3 item(s)". Fix: select the oldest; keep the form always visible, with its buttons changing ("Approve the plan" as the only button for a plan); "Uphold the challenge" / "Dismiss the challenge"; pluralise properly.

**F9. The drawer and tab row are the bottom of the page and open over the conversation.** `#drawer { height: 46vh }` below the tabs; in `board-drawer-transcripts.jpg` the Conversation is squeezed to one visible line. Tabs are plain grey text; "Act as the human" and "Configuration" are the same weight as "Transcripts". Fix: keep the tabs, but open the drawer as a right-hand panel at ≥1800 px and as a full-height overlay with a close button below that; move "show tool calls" and "show finished" into the Transcripts pane (they only apply there); label tabs "Transcripts (3 running)", "Pull requests (1)", "Agreements", "Act", "Settings".

**F10. The footer line mixes four audiences.** "spent: claude/medium 1, claude/large 1, codex/large 9 · cap: small 200, medium 80, large 20 · disk: 39357 MB free · logs: 19 MB · feed live · as of 2026-10-28T19:41:57Z". Spend duplicates the bars; "feed live" is only interesting when false; "as of" is ISO UTC on a page the owner asked to be 12-hour. Fix: footer shows only "updated 4 s ago" and, when wrong, red "connection lost, retrying" / "disk low: 1.2 GB free" / "logs 2.1 GB". Disk and logs are warnings, not readings.

**F11. Spend in three places, dollars in one.** Fix: one "Spend" block in the agent row: `$28.41 · 11 runs · large 9/20`; tokens on hover. Where `cost_usd` is absent (codex), say "no dollar figure from this CLI" once, not a token count that looks like a bill.

**F12. Empty and loading states.** First paint shows "loading…" in `#repo` and nothing else; a 502 from `/api/state` goes to a 12.5-px `#error` span in the footer. Planning phase shows four empty lanes with "TO DO · 0". Four identical yellow setup lines repeat "is configured but not running: make sure its GitHub login exists and can write to the repository (meld doctor checks)". Fix: a skeleton strip ("Connecting to meld…"); errors in the strip; lanes replaced by one line during planning ("No issues yet; the groups are agreeing a plan"); setup lines collapsed to "4 groups are not running" with one "Start all" button and details on click.

**F13. Copy the owner would not say.** Translate at render time in one map (as `INTENTS` and `WORDS` already do), raw token on hover:

| On the page | Say instead |
|---|---|
| `ACK #12`, `PROVIDED`, `RESOLVED` | "acknowledged #12", "provided for #14", "resolved #14" |
| `NEEDS-HUMAN`, `DIRECTIVE`, `HALT`/`RESUME` | "needs you", "instruction", "pause"/"resume" |
| `PROTOCOL-VIOLATION`, `DRIFT` | "broke a rule", "drifted from the plan" |
| `[joint]` | "both groups" |
| `CHALLENGE`, uphold/dismiss | "dispute", "agree with the dispute"/"reject the dispute" |
| lease, quorum | "holds #3 until 1:05 pm", "approvals needed (2)" |
| awake / dead / frozen / launch 39 | running / stopped / out of quota until 5:09 pm / restarted 38 times |
| dispatched (map pill), audited | building / "checked after merge" |
| "Act as the human" | "Act" |

Example, in `renderThread`: `const verb = cell("verb", VERB_WORDS[t.verb] || t.verb.toLowerCase()); verb.title = t.verb;`

## 3. Proposed page structure

1440-wide laptop (stacked):

```
┌──────────────────────────────────────────────────────────────┐ 44px
│ ● Building milestone 1 · 1 of 3 done · 2 workers · updated 4s │  status strip (sec. 4)
│   smidley/meld-test-maze · milestone 1          [Pause] [Act ▾]│
├──────────────────────────────────────────────────────────────┤ only when non-empty
│ Questions for you (2)                                        │ ≤ 40vh
│  ┌ codex · 25 min ─────────────────────────────────────────┐ │
│  │ PR #9 cannot be merged: merge conflict …   [reply ⌄]    │ │
│  └─────────────────────────────────────────────────────────┘ │
│  [ reply box ............................ ] [Send]           │
├──────────────────────────────────────────────────────────────┤ 1 row per group, 28px each
│ ● claude  running · PM ok · building #3 · $28.41 · 9/20 large│
│ ● codex   PM stopped, restarted 38 times · $— · 9/20 large    │
├──────────────────────────────────────────────────────────────┤ flex 0, ≤ 50vh
│ Work                                                          │
│ TO DO 1      │ IN PROGRESS 1 │ IN REVIEW 1   │ DONE 1         │
│ #4 Verify…   │ #3 Build…     │ #2 …          │                │
│ ready to     │ codex frontend│ PR #12 changes│ merged; audit  │
│ claim        │ building, 4m  │ requested     │ owed           │
│ ▬▬▬▬▬▬▬      │ ▬▬▬▬▬▬▬       │ ▬▬▬▬▬▬▬       │ ▬▬▬▬▬▬▬        │
├──────────────────────────────────────────────────────────────┤ flex 1 (rest of height)
│ Conversation            show [the conversation ▾]  ↓ newest  │
│ codex · 12:41 pm · acknowledged #12                           │
│   Proceed with the proposed launch-capable… (3 lines) show all│
├──────────────────────────────────────────────────────────────┤ 32px
│ Transcripts (3) · Pull requests (1) · Agreements · Settings   │  drawer opens as overlay
└──────────────────────────────────────────────────────────────┘
```

1920 monitor (side by side): left 1260 px holds strip, questions, agent rows, lanes (4 × ~300 px); right 640 px is the Conversation, full height, since a chat reads best at ~90 characters per line. The drawer opens *in the right column* (replacing the conversation, with a close tab) so transcripts never cover the lanes.

Hidden until relevant: Questions, setup lines, disk/log warnings, the task dialog, frozen notices.

## 4. Glanceable status strip

One line, one colour, computed server-side beside `headline()`. Precedence: halted › needs you › stopped or out of quota › blocked or silent › moving › done. A second clause may append the next-highest state. Right-aligned: "updated 4 s ago".

| State | Colour | Wording |
|---|---|---|
| Planning | grey | `Planning milestone 1 — the groups are agreeing a plan. Nothing needs you.` |
| Plan ready | amber | `The plan for milestone 1 is ready for your approval.` (Approve button inline) |
| Executing, N workers | green | `Building milestone 1 — 2 workers running, 1 of 3 issues done. Nothing needs you.` |
| Waiting on the person | amber | `2 questions need you — oldest 25 min.` (clicks to Questions) |
| One frozen agent | amber | `codex is out of quota until 5:09 pm; claude is still building.` |
| All frozen | red | `Every agent is out of quota; next try 5:09 pm.` |
| Role stopped / crash loop | red | `codex's PM has stopped (restarted 38 times). Open its transcript.` |
| Stuck issue | amber | `#3 is blocked: the worker could not finish. 1 worker still running.` |
| Silent | amber | `Nothing has happened for 25 min — last post 12:41 pm.` (threshold: `runners.silence_grace_minutes`, which the payload must add; 15 min until then) |
| Halted | red | `Paused by you. Nothing moves until you resume.` (Resume button inline) |
| Milestone done | green | `Milestone 1 is complete — 3 of 3 issues closed, $41.20 spent.` + `1 merged PR still awaits an audit.` when `audit.pending` is non-empty |

All of these are derivable from fields already in `/api/state` except the silence threshold.

## 5. Open questions for the owner

1. Is a right-hand Conversation column at ≥1800 px acceptable, given the earlier "lanes too cramped" verdict? It fixes F9 but gives lanes ~300 px each at 1920.
2. Should the timeline go (F5), or is a "last post N min ago" in the strip enough of its job? This decides ~100 px at the top of every view.
3. The token-limit monitor: no provider quota figure exists in `limits.usage`. Is "out of quota until 5:09 pm" from the freeze record enough, or should the server be asked to learn a ceiling per group?
