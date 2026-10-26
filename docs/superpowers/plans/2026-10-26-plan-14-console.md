# Plan 14 — the console (2026-10-26)

**Goal.** Handoff residual 13 from the first live run: `meld chat`'s `/resolved` took only an issue or PR ref, so a `BUDGET budget-<n>` had to be answered from the raw CLI; and a coordination thread passed a hundred comments in hours, so every ad-hoc reader — a person catching up, a restarted role rebuilding its picture — needed pagination it did not have.

**From the spec.** Section 14 (the human console) and the preamble's rule that a restarted role rebuilds from GitHub.

## Decisions (each reversible)

1. **One paginated reader of the thread: `meld thread [--last N]`.** It prints every parsed, authorized comment oldest first, as `#<id> <time> <header>` then the text, through the board's own paginated reads; `--last N` bounds it (default 30, 0 for all). `/recent [N]` in chat (default 10) uses the same renderer. *Not taken:* telling roles which `gh` flags paginate — a model reads the thread however it likes, and a `meld` verb is the one way that cannot stop at thirty.
2. **`/provided`, `/resolved` and `/revert` take any ref the grammar accepts** (`#13`, `budget-1`, `dir-2`, `audit-40-1`), refusing only a token that is not ref-shaped, so the text cannot be mistaken for a ref. *Not taken:* resolving the single open item when no ref is given.
3. **The preamble names `meld thread`** beside `meld status` as what a restarted role rebuilds from.
4. **`/recent` shows every comment, not `/wait`'s filtered view.** Catching up is the point; `--verbose` is a `/wait` concern.

## Tasks

1. `chat.thread_lines`, `chat._REF_RE`, `/recent`; `cli.thread`; `tests/test_chat.py`.
2. `prompts/_preamble.md` line 2 of the rules.
3. README's verbs table and console paragraph; spec section 14; `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The chat test drives `/recent 2`, a `budget-1` resolve, a `#13` provide, a text-only resolve (refused) and a non-numeric `/recent` (refused); the CLI test drives `meld thread --last 1` and `--last 0` against the board fixture.

## Carried

- `meld thread` prints parsed, authorized comments only; a stranger's or malformed comment is visible through `meld status`'s `ignored_comments` count and `tick`'s `IGNORED-COMMENT`, not here.
- No `--since ID` cursor; `/wait` already covers "what is new since I looked".
