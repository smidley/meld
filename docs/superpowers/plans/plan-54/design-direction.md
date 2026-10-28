# meld watch: design direction

## 1. Diagnosis

The page is GitHub's Primer dark theme with the chrome removed. The `:root` colours are Primer's verbatim (`#0f1115`, `#58a6ff`, `#3fb950`, `#f85149`, `#d29922`), the type is one size (14px) with one meta size (12.5px) everywhere, and every region is a 1px-bordered box in the same tone: `#top`, `#decisions`, `.column`, `.panel`, `.icard`, `.ask`, `.badge`, `#supervisors .agent`, `button.verb`, `#tabs .tab.active`. Because borders do all the separating, nothing is figure and everything is ground; the eye has no place to land. The blue `--accent` is asked to do seven jobs at once: links, the `#phase` badge, `.inum`, `.jstep.now`, the `.icard.working` pulse, `.edge.flowing`, `.ask.selected`, `#newest`, and every button hover. On the board screenshot the brightest things are the three issue numbers and the "Executing" pill, none of which has changed or needs anyone. Meanwhile amber, the one colour that should mean "needs you", is spent on `#decisions`' full-width border, its `h2`, every `.ask`'s left rule and `#setup`'s four identical sentences, so it reads as decoration rather than a signal.

Hierarchy fails at the top: `#repo` (16/600) and `#headline` (16/500) are the same size, so the one sentence that summarises the run is not the headline. `.hint` sentences run inline after every `h2` at 100+ characters ("every issue of the milestone, left to right from to-do to done; each card shows…"), and `#turns .body` at 1680 wide sets 230-character lines, which is why the Conversation reads as a wall. `#bars` and `#limits` set labels, tracks and numbers in the same dim grey at 12.5px with proportional digits, so "904k in / 256k out" is indistinguishable from the label beside it. `#map` is drawn at its own width and sits in the left third of the header with 60% empty space to its right; its role nodes are 24px tall with text clipped by character count ("pm · frozen · launch 11…"), and the edges carry arrowheads the owner has asked not to see. In `#timeline` the lane labels start at x=4, hard against the edge, and every dot breathes on the same 3.2s cycle. On `.icard` the title's dotted underline makes it look like a link next to the real link `.inum`; the seven `.jstep` labels at 11px ellipsise at card width ("review…", "merg…"); `.rverbs button.verb` (3px radius) and `.aquick button` (14px pill) are two different button systems on one page. In the drawer, `#streams .events li`'s 5.5rem/5rem grid wraps "12:41:39 pm" onto two lines and breaks "run_started" as "run_start / ed". The footer `#machine` mixes tabs, spend, disk, logs, a green badge, two checkboxes and an ISO stamp (`2026-10-28T19:41:37Z`) on one 12.5px line.

## 2. Direction

The page should feel like an instrument panel set in ink: a dark slate field, quiet grey structure, tabular numerals, and colour only where it carries meaning. Think of a well-set departures board or a radio operator's log, not a GitHub page, not Grafana, not neon-on-black. Two moves do most of the work:

1. **Replace the border grammar with tonal layering.** Three background steps (field, section, card) separate regions by depth; 1px hairlines survive only between list rows. Cards and the open ask are the only raised objects, so they are what the eye lands on.
2. **One accent, amber, and it means "needs you".** Blue is retired from decoration. Group colours carry identity and nothing else carries colour: the group's name, its journey fills, its timeline dots, its spine on the map. Links are a muted sky, underlined, so they read as links without competing with the amber.

The headline becomes the page's thesis: one 18px line under the repo name saying what is happening and whether anything waits on you. Everything above the Work lanes is set so it can be read from across the room; everything below is set for reading at a desk.

## 3. Tokens

Font: the system UI face (`-apple-system`/SF Pro on the owner's Mac, Segoe UI on Windows, Roboto on Linux) because it is the only face available offline with real optical sizes and a tabular-numeral feature. The typographic move is not a face but `font-variant-numeric: tabular-nums` on every time, count and bar label, so numbers stop jittering as they refresh. Mono stays `ui-monospace`/SF Mono/Menlo for transcripts and code.

Group colours: `colorFor` already indexes a fixed `PALETTE` by configuration order, not a hash (the brief's assumption is out of date). Keep that: index order is stable for a run and lets the first group always be sky. Replace the array with the six below and fall back to `hash(name) % 6` only for a group absent from `groupOrder`. The six sit between 6.3:1 and 9.1:1 on the card surface and are separated in hue by at least 45°, so any pair is distinguishable to protan and deutan viewers except violet/rose, which differ by 2.5:1 in luminance. Human should become white ink (the person is the only white voice) and joint a lavender grey; both are JS constants.

Contrast ratios (WCAG, text over surface): `--fg` 14.8 on `--bg-1`, 13.6 on `--bg-2`; `--fg-2` 8.3 / 7.7; `--fg-3` 4.8 / 4.4 (muted text only on `--bg-0`..`--bg-2`; on `--bg-3` controls use `--fg-2`, 6.8); `--attn` 10.1 / 9.2, and `--attn-ink` on an `--attn` fill 10.4; `--ok` 8.5 / 7.9; `--bad` 6.2 / 5.7; `--unknown` 5.8 / 5.3; `--link` 9.5 / 8.8; groups on `--bg-2`: sky 7.4, tangerine 7.2, violet 6.3, teal 8.0, gold 9.1, rose 6.7; transcript inks on `--bg-1`: tool 9.5, result 10.9, usage 9.7, raw 8.2, thinking 7.1.

```css
:root {
  color-scheme: dark;
  /* surfaces: field, section, card, control */
  --bg-0: #0e1013; --bg-1: #14171c; --bg-2: #1b1f26; --bg-3: #232833;
  --line: #2b313b; --line-strong: #3b4350;
  /* ink */
  --fg: #e6e9ef; --fg-2: #a9b1be; --fg-3: #7b8491;
  --link: #9cc0ea;
  /* the one accent: something needs the person */
  --attn: #f0b94b; --attn-ink: #1a1200; --attn-soft: rgba(240, 185, 75, 0.14);
  /* semantic */
  --ok: #5fc784; --warn: var(--attn); --bad: #f07070; --unknown: #8a93a1;
  --ok-soft: rgba(95, 199, 132, 0.14); --bad-soft: rgba(240, 112, 112, 0.14);
  /* identity: six fixed group colours, indexed by configuration order */
  --g1: #6fb3f2; --g2: #f0955a; --g3: #b48cf5; --g4: #45c7c2; --g5: #e0bd56; --g6: #ee82b0;
  --human: #e6e9ef; --joint: #c6bfe6;
  /* transcript inks */
  --message: var(--fg); --thinking: #9aa3b2; --tool: #8ec2f0; --result: #9bd8a4; --usage: #c9b4f5; --raw: #bfae7a;
  /* legacy aliases so existing rules keep resolving */
  --bg: var(--bg-0); --panel: var(--bg-1); --panel2: var(--bg-2); --dim: var(--fg-3); --accent: var(--link);
  /* type */
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, system-ui, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --t-head: 500 18px/1.3 var(--sans);         /* #headline */
  --t-title: 600 15px/1.3 var(--sans);        /* #repo, #task-title, #wizard h1 (20px) */
  --t-section: 600 13px/1.3 var(--sans);      /* h2 .who, .lane h3 */
  --t-card: 500 13.5px/1.35 var(--sans);      /* .ititle, .atitle */
  --t-body: 400 14px/1.5 var(--sans);         /* .body, .atext */
  --t-meta: 400 12px/1.4 var(--sans);         /* .hint, .ibadges, .afrom, #limits, #machine */
  --t-mono: 400 12.5px/1.45 var(--mono);      /* transcripts, inputs */
  --tnum: "tnum" 1, "lnum" 1;
  /* spacing */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px;
  --gutter: 20px;
  /* radii */
  --r-ctl: 5px; --r-card: 8px; --r-sheet: 12px; --r-pill: 999px;
  /* elevation: cards have none; only the open ask and the dialog lift */
  --shadow-1: 0 1px 0 rgba(0,0,0,0.35), 0 6px 18px -8px rgba(0,0,0,0.6);
  --shadow-2: 0 2px 0 rgba(0,0,0,0.4), 0 24px 48px -16px rgba(0,0,0,0.75);
  /* motion */
  --d-micro: 160ms; --d-ui: 280ms; --d-sheet: 320ms; --d-fill: 600ms;
  --ease: cubic-bezier(0.2, 0.7, 0.2, 1); --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  interpolate-size: allow-keywords;
}
```

A light theme swaps only the first three groups of tokens: `--bg-0 #f4f5f7`, `--bg-1 #ffffff`, `--bg-2 #f7f8fa`, `--bg-3 #eceef2`, `--line #dfe3e9`, `--line-strong #c9cfd8`, `--fg #1b1f26`, `--fg-2 #4f5866`, `--fg-3 #656e7c` (4.7 on the field), `--link #2457a6`, `--attn #9a6400` with `--attn-ink #ffffff`, `--ok #1f7a3d`, `--bad #b42323`, groups `#1f6fc2 #b84c14 #6e3fc2 #0a7570 #8a6a00 #b8306f` (all ≥ 4.7 on `#f4f5f7`), shadows at a third of the alpha. Nothing else changes.

```css
html, body { background: var(--bg-0); color: var(--fg); font: var(--t-body); }
.num, time, .at, .blabel, #progress-text, #limits, #machine, .awhen, .jnow, .tick, #timeline text { font-variant-numeric: tabular-nums; font-feature-settings: var(--tnum); }
.strong { font-weight: 600; }
.dim { color: var(--fg-3); }
.hint { font: var(--t-meta); color: var(--fg-3); }
a { color: var(--link); text-decoration: underline; text-decoration-color: color-mix(in srgb, var(--link) 35%, transparent); text-underline-offset: 2px; }
a:hover { text-decoration-color: currentColor; }
:focus-visible { outline: 2px solid var(--attn); outline-offset: 2px; }
```

## 4. Components

### Header `#top`

What changes: the header loses its bottom border and sits on `--bg-1` as the first tonal step; `#headline` becomes the largest text on the page; the phase badge turns neutral (blue said "active", which it is not); HALTED is the only filled badge. Bars get a taller, unbordered track and tabular labels in two inks (label muted, number primary). `#limits` becomes a quiet second line.

```css
#top { padding: var(--s3) var(--gutter) var(--s2); background: var(--bg-1); border-bottom: 0; }
#board { gap: var(--s1) var(--s3); align-items: baseline; }
#repo { font: var(--t-title); color: var(--fg); }
#milestone { color: var(--fg-2); }
.badge { font: var(--t-meta); font-weight: 500; line-height: 18px; padding: 0 8px; border: 1px solid var(--line-strong); border-radius: var(--r-pill); color: var(--fg-2); }
#phase { text-transform: capitalize; color: var(--fg-2); border-color: var(--line-strong); font-weight: 500; }
#halted { color: var(--attn-ink); background: var(--bad); border-color: var(--bad); font-weight: 600; }
.badge.alive { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
.badge.dead { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 45%, transparent); }
.badge.unknown { color: var(--unknown); border-color: var(--line-strong); }
.badge.frozen, .badge.reconnecting { color: var(--attn); border-color: color-mix(in srgb, var(--attn) 50%, transparent); }
#headline { margin: var(--s1) 0 var(--s3); font: var(--t-head); letter-spacing: -0.005em; max-width: 90ch; text-wrap: balance; }
#bars { gap: var(--s1) var(--s5); margin: 0; font: var(--t-meta); }
#budget-bars { gap: var(--s1) var(--s4); }
.bar { gap: var(--s2); }
.bar .blabel { color: var(--fg-3); min-width: 7ch; }
.bar .track { flex: 0 0 8rem; height: 6px; background: var(--bg-3); border: 0; border-radius: 3px; }
.bar .fill { background: var(--ok); transition: width var(--d-fill) var(--ease); }
.bar .fill.warn { background: var(--attn); }
.bar .fill.bad { background: var(--bad); }
.bar .dim, #progress-text { color: var(--fg-2); }
#limits { margin: var(--s2) 0 0; gap: 2px var(--s5); font: var(--t-meta); color: var(--fg-3); }
#limits li .lgroup { font-weight: 600; }
#limits li.frozen { color: var(--attn); }
```

### Agents map `#map`

What changes: no arrowheads; edges are hairlines in `--line-strong`, a flowing edge is a slow dash in the group's colour, not blue; the group node is the heavy object and role nodes are light chips; node text uses the meta size with the group's colour only on the spine; the map is centred in its own full-width band so it no longer hugs the left. Sizes live in `drawMap`; the values below are what the CSS assumes (listed again in the JS appendix).

```css
#map { display: block; margin: var(--s3) auto 0; max-width: 100%; height: auto; }
#map text { font: var(--t-meta); font-weight: 500; fill: var(--fg); }
#map .sub { fill: var(--fg-3); font-weight: 400; }
#map .node { fill: var(--bg-2); stroke: var(--line); stroke-width: 1; }
#map .node.live { stroke-width: 1.5; animation: ring 2.4s var(--ease) infinite; }
#map .edge, #task-graph .edge { fill: none; stroke: var(--line-strong); stroke-width: 1; marker-end: none; }
#map .edge.flowing, #task-graph .edge.flowing { stroke: var(--edge-color, var(--fg-3)); stroke-dasharray: 3 7; stroke-linecap: round; animation: dash 1.6s linear infinite; }
@keyframes dash { to { stroke-dashoffset: -10; } }
#map .arrow, #task-graph .arrow { display: none; }
#map .pill, #task-graph .pill { fill: var(--bg-3); stroke: none; }
#map .elabel, #task-graph .elabel { font-size: 10.5px; fill: var(--fg-2); letter-spacing: 0.02em; }
#map .ring-track, #task-graph .ring-track { stroke: var(--bg-3); stroke-width: 2.5; }
#map .ring, #task-graph .ring { stroke-width: 2.5; transition: stroke-dashoffset var(--d-fill) var(--ease); }
@keyframes ring { 0%, 100% { stroke-opacity: 1; } 50% { stroke-opacity: 0.45; } }
```

### Timeline `#timeline`

What changes: lane labels get a left gutter and sit in `--fg-2`; lanes become faint dotted hairlines; the breathing is reduced to a 15% swell over 4.5s with the existing per-dot delay; attention dots get an amber halo rather than a stroke; ticks are tabular meta.

```css
#timeline { display: block; margin: var(--s3) 0 0; max-width: 100%; height: auto; }
#timeline text { font: var(--t-meta); fill: var(--fg-2); }
#timeline .sub { fill: var(--fg-2); }
#timeline .tick { fill: var(--fg-3); font-size: 11px; }
#timeline .lane { stroke: var(--line); stroke-dasharray: 1 5; stroke-linecap: round; }
#timeline .now { stroke: var(--fg-3); stroke-dasharray: 2 3; }
#timeline .dot { cursor: pointer; transform-box: fill-box; transform-origin: center; animation: breathe 4.5s ease-in-out infinite; transition: r var(--d-micro) var(--ease); }
@keyframes breathe { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
#timeline .dot.attention { stroke: var(--attn); stroke-width: 6; stroke-opacity: 0.3; paint-order: stroke; }
#timeline .dot:hover { stroke: var(--fg); stroke-width: 2; stroke-opacity: 1; }
```

### Setup and supervisors rows `#setup`, `#supervisors`

What changes: setup notices stop being amber paragraphs; they are neutral rows with an amber dot and a proper button. Supervisor chips lose their border and show the group's colour as a dot and the state as a badge, and a busy chip's "doing" text becomes the chip's second line.

```css
#setup { margin: var(--s3) 0 0; padding: 0; list-style: none; display: grid; gap: var(--s1); font: var(--t-meta); color: var(--fg-2); }
#setup li { display: flex; gap: var(--s2); align-items: center; padding: var(--s1) 0; }
#setup li::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--attn); flex: 0 0 auto; }
#setup li.bad { color: var(--bad); }
#setup li.bad::before { background: var(--bad); }
#setup code { font: var(--t-mono); background: var(--bg-3); padding: 0 5px; border-radius: 3px; }
#setup button.start { margin-left: auto; }
#supervisors { gap: var(--s2) var(--s3); margin: var(--s3) 0 0; }
#supervisors .agent { gap: var(--s2); background: var(--bg-2); border: 0; border-radius: var(--r-pill); padding: 3px 12px 3px 10px; font-size: 13px; }
#supervisors .agent .dot { font-size: 10px; line-height: 1; }
#supervisors .agent .doing { color: var(--fg-3); font: var(--t-meta); }
#supervisors .agent.busy .dot { animation: blink 2.4s ease-in-out infinite; }
@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }
```

### Questions for you `#decisions`, `.ask`, `#reply`

What changes: this is the only place amber is spent. The section has no border; it is a `--bg-1` sheet with a 3px amber top edge and the amber title. Each `.ask` is a plain card; only the selected one lifts and takes an amber ring (not blue). The party pill carries the group colour; quick-reply buttons and the Send buttons use the one button system below, with the primary action filled amber.

```css
#decisions { margin: var(--s3) var(--gutter) 0; background: var(--bg-1); border: 0; border-top: 3px solid var(--attn); border-radius: var(--r-card); box-shadow: var(--shadow-1); }
#decisions h2 { margin: 0; padding: var(--s3) var(--s4) var(--s2); font: var(--t-section); color: var(--attn); border-bottom: 0; display: flex; gap: var(--s2); align-items: baseline; flex-wrap: wrap; }
#decisions h2 .hint { color: var(--fg-3); }
#asks { padding: 0 var(--s4) var(--s2); max-height: 40vh; }
.ask { margin: 0 0 var(--s2); padding: var(--s3) var(--s4); background: var(--bg-2); border: 1px solid transparent; border-left: 1px solid transparent; border-radius: var(--r-card); max-width: 68ch; cursor: pointer;
       transition: background var(--d-micro) var(--ease), box-shadow var(--d-micro) var(--ease), border-color var(--d-micro) var(--ease); }
.ask:hover { background: var(--bg-3); }
.ask.selected { border-color: var(--attn); border-left-color: var(--attn); box-shadow: var(--shadow-1), 0 0 0 3px var(--attn-soft); }
.ask .afrom { font: var(--t-meta); display: flex; gap: var(--s2); align-items: baseline; }
.ask .aparty { font-weight: 600; border: 1px solid color-mix(in srgb, currentColor 45%, transparent); border-radius: var(--r-pill); padding: 0 8px; line-height: 18px; }
.ask .awhen, .ask .awhere { color: var(--fg-3); }
.ask .atitle { margin-top: var(--s1); font: var(--t-card); color: var(--fg); }
.ask .atext { margin-top: var(--s1); color: var(--fg-2); max-height: 12rem; }
.ask .aquick { gap: var(--s2); margin-top: var(--s2); }
#reply { gap: var(--s1) var(--s2); padding: var(--s2) var(--s4) var(--s3); border-top: 1px solid var(--line); }
#reply-to { font: var(--t-meta); color: var(--fg-3); }
#reply-text { font: var(--t-body); min-height: 2.6em; }
#reply-send { gap: var(--s2); }
#reply-send button:first-child { background: var(--attn); color: var(--attn-ink); border-color: var(--attn); font-weight: 600; }
#reply-send button:first-child:hover { background: color-mix(in srgb, var(--attn) 88%, white); color: var(--attn-ink); }
#reply-outcome { font: var(--t-meta); }
```

### Work lanes `#issues.kanban`, `.lane`, `.icard`

What changes: the section is a `--bg-1` sheet, lanes are unboxed columns separated by space, lane headings are small caps with a tabular count. Cards are the raised object: `--bg-2`, no border, 8px radius; the title is plain (no dotted underline) and the number is a muted link. The journey strip gets room: 6px bars, 10.5px labels with letter-spacing, and the step under way carries the group colour (set as `--group` on the card by the JS; falls back to `--fg-2`). The "working" pulse is a soft inner ring in the group colour, not a blue box-shadow. Verb buttons use the one button system.

```css
#live { gap: var(--s3); padding: var(--s3) var(--gutter); }
.column { background: var(--bg-1); border: 0; border-radius: var(--r-card); }
.column h2 { margin: 0; padding: var(--s3) var(--s4) var(--s2); font: var(--t-section); border-bottom: 0; display: flex; gap: var(--s2); align-items: baseline; flex-wrap: wrap; }
.column h2 .who { color: var(--fg); }
.column h2 .hint { flex: 1 1 24ch; min-width: 0; }
.column.ended { opacity: 0.6; }
#issues.kanban { gap: var(--s5); padding: var(--s1) var(--s4) var(--s4); }
.lane h3 { margin: 0 0 var(--s1); font: var(--t-meta); font-weight: 600; color: var(--fg-3); text-transform: uppercase; letter-spacing: 0.08em; font-variant-numeric: tabular-nums; }
.flowline { height: 2px; margin: 0 0 var(--s3); opacity: 0.35; background: repeating-linear-gradient(90deg, var(--line-strong) 0 4px, transparent 4px 10px); animation: flow 2.4s linear infinite; }
.flowline.flowing { opacity: 0.9; background: repeating-linear-gradient(90deg, var(--fg-2) 0 4px, transparent 4px 10px); animation-duration: 0.6s; }
@keyframes flow { to { background-position: 10px 0; } }
.icard { --group: var(--fg-2); background: var(--bg-2); border: 1px solid transparent; border-radius: var(--r-card); padding: var(--s3) var(--s4); margin-bottom: var(--s2);
         transition: background var(--d-micro) var(--ease), box-shadow var(--d-ui) var(--ease), border-color var(--d-ui) var(--ease); }
.icard:hover { background: var(--bg-3); }
.icard.attention { border-color: var(--attn); }
.icard.working { border-color: transparent; box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--group) 55%, transparent); animation: pulse 3s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--group) 55%, transparent); } 50% { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--group) 20%, transparent); } }
.icard.moved { animation: settle 1.6s var(--ease-out); }
@keyframes settle { 0% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--group) 50%, transparent); } 100% { box-shadow: 0 0 0 0 transparent; } }
.icard .ihead { gap: var(--s2); align-items: baseline; }
.icard .inum { font: var(--t-meta); font-weight: 500; color: var(--fg-3); flex: 0 0 auto; }
.icard .inum a { color: inherit; text-decoration: none; }
.icard .inum a:hover { color: var(--link); }
.icard .ititle { font: var(--t-card); color: var(--fg); cursor: pointer; text-decoration: none; }
.icard .ititle:hover { color: var(--link); }
.icard .ibadges { font: var(--t-meta); color: var(--fg-3); margin-top: var(--s1); }
.icard .iwho { font-weight: 600; }
.icard .journey { gap: 3px; margin-top: var(--s3); }
.icard .jstep { font-size: 10.5px; line-height: 1.2; letter-spacing: 0.02em; color: var(--fg-3); }
.icard .jstep::before { height: 5px; border-radius: 2px; background: var(--bg-3); margin-bottom: var(--s1); transition: background var(--d-fill) var(--ease); }
.icard .jstep.done { color: var(--fg-2); }
.icard .jstep.done::before { background: var(--ok); }
.icard .jstep.now { color: var(--fg); font-weight: 600; }
.icard .jstep.now::before { background: var(--group); animation: blink 2.4s ease-in-out infinite; }
.icard .jnow { font: var(--t-meta); color: var(--fg-2); margin-top: var(--s2); }
.icard .iactivity { font: var(--t-meta); font-weight: 500; margin-top: var(--s2); }
.icard .iprogress { height: 3px; background: var(--bg-3); border-radius: 2px; margin-top: var(--s1); }
.icard .ifill { transition: width var(--d-fill) var(--ease); }
.icard .ipr { font: var(--t-meta); color: var(--fg-3); margin-top: var(--s1); }
.icard .rverbs { margin-top: var(--s2); gap: var(--s1); }
```

### Conversation `#conversation`, `#turns li`, `.party`, `.body`

What changes: a measure. Bodies are capped at 78ch, which halves the apparent density and makes the Conversation readable for hours. Rows are separated by a hairline on `--bg-1`; the party pill is the only coloured thing on a row; verb and time are meta. The "fresh" arrival stays, slower and eased.

```css
#conversation h2 select { margin-left: auto; }
#turns li { padding: var(--s3) var(--s4); border-bottom: 1px solid var(--line); transition: background var(--d-ui) var(--ease); }
#turns li:last-child { border-bottom: 0; }
#turns .head { gap: var(--s2); font: var(--t-meta); }
#turns .party { font-weight: 600; font-size: 12px; border: 1px solid color-mix(in srgb, currentColor 45%, transparent); border-radius: var(--r-pill); padding: 0 8px; line-height: 18px; }
#turns .verb { font-weight: 600; color: var(--fg-2); }
#turns .head .dim { color: var(--fg-3); }
#turns .body { margin: var(--s1) 0 0; max-width: 78ch; color: var(--fg); max-height: 14rem; }
#turns li.human .body { color: var(--fg); }
#turns li.agreement { background: color-mix(in srgb, var(--joint) 6%, transparent); }
#turns li.flash, .column.flash { background: var(--bg-3); }
#turns li.fresh { animation: arrive var(--d-sheet) var(--ease-out); }
@keyframes arrive { from { transform: translateY(-4px); opacity: 0; } to { transform: none; opacity: 1; } }
#newest { font: var(--t-meta); color: var(--fg); background: var(--bg-3); border: 0; border-radius: var(--r-pill); padding: 2px 10px; }
```

### Tab row and drawer `#tabs`, `#drawer`, `#streams .column`, `.events`

What changes: the tab row becomes a real tab bar with an underline indicator instead of a filled box; the machine line is split visually into status (spend, disk, logs) and controls (checkboxes) by spacing, and the feed badge is a dot plus a word. Transcript columns widen their time and kind cells so nothing wraps, and rows become readable mono at 12.5px with the kind column set small-caps-ish.

```css
#tabs { gap: 0; padding: 0 var(--gutter); background: var(--bg-1); border-top: 1px solid var(--line); }
#tabs .tab { font: var(--t-body); font-size: 13px; color: var(--fg-3); background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: var(--s2) var(--s3); transition: color var(--d-micro) var(--ease), border-color var(--d-micro) var(--ease); }
#tabs .tab:hover { color: var(--fg); }
#tabs .tab.active { color: var(--fg); background: transparent; border-bottom-color: var(--fg); }
#machine { margin-left: auto; gap: var(--s1) var(--s4); font: var(--t-meta); color: var(--fg-3); }
#machine #feed { font-size: 11px; padding-left: 14px; position: relative; border: 0; }
#machine #feed::before { content: ""; position: absolute; left: 4px; top: 50%; width: 6px; height: 6px; margin-top: -3px; border-radius: 50%; background: currentColor; }
#machine #feed.alive { color: var(--ok); }
#detail, #finished { color: var(--fg-3); display: inline-flex; gap: 5px; align-items: center; }
#error:empty { display: none; }
#drawer { height: 46vh; background: var(--bg-1); border-top: 1px solid var(--line); transition: height var(--d-sheet) var(--ease), border-top-color var(--d-sheet) var(--ease); }
#drawer.closed { height: 0; border-top-color: transparent; }
#pulls-panel, #agreements-panel, #act { padding: var(--s3) var(--gutter); }
.panel { background: transparent; border: 0; }
.panel h2 { padding: 0 0 var(--s2); font: var(--t-section); border-bottom: 1px solid var(--line); }
#streams { gap: var(--s3); padding: var(--s3) var(--gutter); }
#streams .column { flex: 0 0 36rem; max-width: 48rem; background: var(--bg-2); }
#streams .column h2 { padding: var(--s2) var(--s3); border-bottom: 1px solid var(--line); }
#streams .column h2 .why { font: var(--t-meta); }
#streams .events { font: var(--t-mono); }
#streams .events li { grid-template-columns: 7.5rem 6.5rem 1fr; gap: var(--s2); padding: 3px var(--s3); border-bottom: 0; }
#streams .events li:hover { background: var(--bg-3); }
.events .at { color: var(--fg-3); white-space: nowrap; }
.events .kind { color: var(--fg-3); white-space: nowrap; letter-spacing: 0.02em; }
li.message .text { color: var(--message); }
li.thinking .text { color: var(--thinking); }
li.tool_call .kind, li.tool_call .text { color: var(--tool); }
li.tool_result .kind { color: var(--result); }
li.tool_result.failed .kind, li.tool_result.failed .text, li.cli_error .text, li.usage_limit .text { color: var(--bad); }
li.usage .text, li.run_started .text, li.run_finished .text { color: var(--usage); }
li.log_truncated .text { color: var(--attn); }
li.raw .text { color: var(--raw); }
li.synthesized .text { color: var(--fg-3); font-style: italic; }
.brow { padding: var(--s1) var(--s3); border-bottom: 1px solid var(--line); }
.brow:hover { background: var(--bg-3); }
.rid { color: var(--link); }
```

### Buttons and inputs

What changes: one button system. Default buttons are `--bg-3` with no visible border, 5px radius, 13px text; hover lightens the surface, not the text colour; the primary (Run, Send as ruling) is filled amber. Inputs share the surface and show the amber focus ring. This replaces `button.verb`, `.aquick button`, `#reply-send button`, `#bar button`, `#setup button.start`, `#wizard button`, `#config-save`, `.gcard .ghead button`, `.gadd button`, `#task-tree button` in one rule set.

```css
button.verb, .ask .aquick button, #reply-send button, #bar button, #setup button.start, #wizard button, #config-save,
.gcard .ghead button, .gadd button, #task-tree button {
  font: var(--t-body); font-size: 13px; line-height: 1.2; color: var(--fg); background: var(--bg-3); border: 1px solid transparent; border-radius: var(--r-ctl);
  padding: 4px 11px; cursor: pointer; transition: background var(--d-micro) var(--ease), color var(--d-micro) var(--ease), border-color var(--d-micro) var(--ease); }
button.verb, #task-tree button { font-size: 12px; padding: 2px 8px; }
button.verb:hover, .ask .aquick button:hover, #reply-send button:hover, #bar button:hover, #setup button.start:hover, #wizard button:hover, #config-save:hover,
.gcard .ghead button:hover, .gadd button:hover, #task-tree button:hover { background: color-mix(in srgb, var(--bg-3) 70%, var(--fg-3)); color: var(--fg); border-color: transparent; }
#bar #run, #wizard #wz-create { background: var(--attn); color: var(--attn-ink); font-weight: 600; }
#bar #run:hover, #wizard #wz-create:hover { background: color-mix(in srgb, var(--attn) 88%, white); color: var(--attn-ink); }
button:disabled { opacity: 0.45; cursor: default; }
#bar select, #bar input, #reply-text, #conversation h2 select, #wizard form input, #wizard form textarea, #config-fields input, #config-fields select, #config-fields textarea {
  font: var(--t-body); color: var(--fg); background: var(--bg-3); border: 1px solid transparent; border-radius: var(--r-ctl); padding: 5px 9px; transition: border-color var(--d-micro) var(--ease), background var(--d-micro) var(--ease); }
#bar input, #config-fields input, #config-fields select, #config-fields textarea { font: var(--t-mono); padding: 4px 8px; }
#bar select:focus, #bar input:focus, #reply-text:focus, #wizard form input:focus, #wizard form textarea:focus, #config-fields input:focus, #config-fields textarea:focus { outline: none; border-color: var(--attn); background: var(--bg-2); }
#config-fields input[readonly] { color: var(--fg-3); background: transparent; }
#result, #config-result, #reply-outcome, #wz-result { font: var(--t-meta); }
#result.ok, #config-result.ok, #reply-outcome.ok { color: var(--ok); }
#result.bad, #config-result.bad, #reply-outcome.bad, #error { color: var(--bad); }
```

### Task dialog `#task`

```css
#task { background: var(--bg-1); color: var(--fg); border: 0; border-radius: var(--r-sheet); padding: var(--s4) var(--s5); box-shadow: var(--shadow-2); }
#task::backdrop { background: rgba(6, 8, 10, 0.6); backdrop-filter: blur(2px); }
#task-title { font: var(--t-title); }
#task-close { color: var(--fg-3); font-size: 20px; line-height: 1; padding: 0 var(--s1); }
#task-close:hover { color: var(--fg); }
#task-graph text { font: var(--t-meta); font-weight: 500; fill: var(--fg); }
#task-graph .node { fill: var(--bg-2); stroke: var(--line); stroke-width: 1; }
#task-graph .node.live { stroke-width: 1.5; animation: ring 2.4s var(--ease) infinite; }
#task-graph .sub { fill: var(--fg-3); font-weight: 400; }
#task-tree ul { border-left: 1px solid var(--line); }
#task-tree .meta { font: var(--t-meta); color: var(--fg-3); }
```

### Footer line and responsiveness

The `#machine` span is the footer. It stays on the tab row but the content order should become: tabs, then (right) spend · disk · logs, a gap, the feed dot, a gap, the two checkboxes, the stamp. The stamp must go through `clock12` (JS) so it reads "as of 7:41:37 pm", matching every other time on the page.

```css
#machine #stamp { color: var(--fg-3); }
@media (max-width: 1280px) { :root { --gutter: 16px; } #streams .column { flex-basis: 32rem; } }
@media (max-width: 980px) { #issues.kanban { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--s4); } }
@media (max-width: 900px) {
  :root { --gutter: 12px; }
  .column h2 .hint, #decisions h2 .hint { display: none; }
  #headline { font-size: 16px; }
  #bars { gap: var(--s1) var(--s3); }
  .bar .track { flex-basis: 6rem; }
  #streams .column { flex-basis: 28rem; }
  #streams .events li { grid-template-columns: 6.5rem 5.5rem 1fr; }
  #machine { flex: 1 1 100%; margin-left: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .flowline, .icard.working, .icard.moved, #timeline .dot, #map .edge, #task-graph .edge, #map .live, #task-graph .live, #supervisors .agent.busy .dot, .icard .jstep.now::before { animation: none; }
  .appears, #drawer, #task, #task::backdrop, details::details-content, .icard, .ask, #tabs .tab { transition: none; }
}
```

## 5. Motion

Motion on a page watched for hours must be ambient, never attention-seeking. Three registers:

- **Arrival** (something new exists): a 280–320ms fade with a 4px rise, `--ease-out`. Used by `.appears`, `#turns li.fresh`, `.icard.moved` (the FLIP travel stays at 800ms with its current curve; the settle halo shrinks over 1.6s). A card that arrives is seen once and then still.
- **Progress** (a quantity changed): width and dash-offset tween over `--d-fill` 600ms with `--ease`. Bars, rings, journey fills. Never animated on first paint beyond that one tween.
- **Alive** (a process is running right now): the slowest register, and the only looping one. Breathing dots at 4.5s and 15% swell with the existing staggered delays; `.icard.working` and `.node.live` fade a ring between 55% and 20% over 3s; the busy supervisor dot blinks over 2.4s; flowing edges drift dashes at 1.6s; the lane flow line drifts at 2.4s and quickens to 0.6s only for the minute after a card lands. Nothing alive moves in position. The earlier 0.9s dash, 1.6s blink and 2s box-shadow pulse are what made the page feel restless.

Sheets: the drawer slides over `--d-sheet` 320ms `--ease`; the dialog fades over 250ms with the backdrop; the configuration `<details>` opens by height over 300ms. Hover states resolve in `--d-micro` 160ms. Text never animates colour on its own; surfaces do. `prefers-reduced-motion` turns off every loop and every transition.

## 6. Defects the CSS above fixes

1. `#repo` and `#headline` the same size; headline now 18px and the only large text.
2. `#phase` badge in accent blue reads as an alert; now neutral, `#halted` the only filled badge.
3. `#bars` labels and numbers in one grey; label `--fg-3`, number `--fg-2`, tabular digits stop the jitter on refresh.
4. `#limits` indistinguishable from `#bars`; now a quieter second line with 8px separation.
5. `#map` left-hugging with empty right; centred via `margin: auto` in its band.
6. `#map .edge` arrowheads; marker hidden and `.arrow` not rendered (also drop `marker-end` in `drawEdge`).
7. `#map .edge.flowing` in blue; now the group's colour via `--edge-color` (JS sets `style="--edge-color: …"` on the path) at a slower drift.
8. `#timeline .sub` lane labels flush to x=4 and the same ink as dots' labels; now `--fg-2` with the gutter raised to 16 in `drawTimeline`.
9. `#timeline .dot` all breathing hard; 15% swell at 4.5s.
10. `#timeline .dot.attention` stroke in warn; amber halo via `paint-order`.
11. `.column`, `#top`, `.panel`, `#decisions` 1px borders stacking into a grid of boxes; borders removed, three surface tones instead.
12. `.column h2 .hint` 100-character sentences inline; now meta size, wraps as a flex item, hidden under 900px (copy should still be shortened in `app.html`).
13. `.icard .ititle` dotted underline competing with `.inum` link; underline removed, number muted.
14. `.icard .inum` in accent blue on every card; now `--fg-3`, link only on hover.
15. `.icard .jstep` labels ellipsised; 10.5px with 3px gaps and 16px card padding gives each step ~40px at a 1280 four-lane board.
16. `.icard .jstep.now` in blue; now the group colour.
17. `.icard.working` blue box-shadow pulse; inset ring in the group colour at 3s.
18. `.icard.moved` blue halo; group colour, eased.
19. `.flowline` 1.6s drift on every lane; 2.4s at 35% opacity, fast only when flowing.
20. `#decisions` amber border + amber `h2` + amber rule on each `.ask`; one amber top edge and the title, asks neutral until selected.
21. `.ask.selected` switching to blue; amber ring.
22. `.ask .aquick button` pill vs `button.verb` rectangle; one button system.
23. `#turns .body` 230-character lines at 1680; 78ch measure.
24. `#turns .party` and `#turns .verb` and time all at body size; head row is meta, body is body.
25. `#tabs .tab.active` filled box; underline tab bar.
26. `#machine` one undifferentiated 12.5px line; grouped by spacing, feed badge as a dot.
27. `#streams .events li` 5.5rem/5rem columns wrapping "12:41:39 pm" and breaking "run_started"; 7.5rem/6.5rem with nowrap.
28. `#streams .column` bordered box inside a bordered drawer; column on `--bg-2` inside a flat `--bg-1` drawer.
29. `#setup li` four amber sentences with inline buttons; neutral rows with an amber dot and right-aligned button.
30. `#supervisors .agent` bordered chip; borderless pill with a colour dot.
31. Focus states absent; amber `:focus-visible` ring everywhere.

Not fixable in CSS, listed for the JS pass: `#stamp` prints an ISO timestamp (use `clock12`); `drawEdge` sets `marker-end`; `drawMap` sizes (`rowH` 62 → 68, role node height 24 → 28, `groupW` 120 → 136, `roleW` 160 → 184, `left` 8 → 16) and `clip()` character budgets (7 → 6.6 px per char at 12px); `drawTimeline` `left` 92 → 100 and `svgText(svg, 4, …)` → 16; `colorFor` to adopt the six-colour palette, white human and lavender joint; `issueCard` to set `card.style.setProperty("--group", colorFor("group", i.claimed_by))`.

## Renames

None. Every selector above uses an existing id or class. New custom properties (`--bg-0..3`, `--fg-2`, `--fg-3`, `--attn`, `--group`, `--edge-color`, `--g1..6`) are additions; `--bg`, `--panel`, `--panel2`, `--dim`, `--accent` remain as aliases so any rule not replaced still resolves.
