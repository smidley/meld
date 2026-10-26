# Plan 19 — the headline (2026-10-26)

**Goal.** The owner's "do it" on the proposal that followed plan 18: a short "what is happening" line under the header in plain words, derived from the phase and the board, so a person opening the page knows where the milestone stands before reading anything else.

**From the spec.** Section 22 (what the page shows) and section 14 (what `meld chat` prints on opening).

## Decisions (each reversible)

1. **One sentence, composed on the server.** `chat.headline(status, decisions)` reads `meld status`'s own JSON — phase, halted, issue statuses, run states, open PRs and their `legal` — and the decisions list, and says: planning (and whether the plan awaits approval), executing with counts, the milestone complete, or halted; always ending with whether anything waits on the person. *Not taken:* composing it in the page from the same numbers, which would be a second place that knows what the statuses mean.
2. **`meld chat` prints it first** too, before the inbox, so the console and the page open the same way.
3. **The header's needs-human lines are gone**; the decision cards (plan 18) carry those items, and the headline counts them.

## Tasks

1. `chat.headline`; `run_chat` prints it; `viewer/server.py` carries it; `app.html`/`app.js` show it.
2. `tests/test_chat.py` (planning, planning with the plan agreed, executing with every status, halted, complete), `tests/test_viewer.py`.
3. README, spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the page checked in a browser against the playground.

## Carried

- The sentence counts decisions but does not name them; the cards do.
- A milestone with no issues yet in `executing` reads "issues: none yet".
