# Plan 61 — a network blip is not a question for a person (2026-10-29)

**Goal.** A few minutes without GitHub costs a run a few minutes, not a human decision.

**From the spec.** Section 7 (`github.py` is the only GitHub path), the preamble's error rule.

**What the maze run showed.** At 00:38 to 00:41 UTC on 2026-10-29 every live role logged "error connecting to api.github.com"; codex's PM followed the preamble (wait a minute, retry once) and posted `NEEDS-HUMAN github-connectivity-1`. By the time the owner read it GitHub answered, its status page said all systems operational, and nothing was wrong.

## Decisions (each reversible)

1. **`github.py` retries a connection failure itself.** A `gh` run with no response and a connection error on stderr is tried again after 2, 5, 15 and 30 seconds (`CONNECT_RETRY_SECONDS`, injected `sleep`) before `GitHubError`; an HTTP answer of any status is never retried here.
2. **The roles wait longer for that one kind of error.** The preamble: a connection error has already been retried for a minute, so wait five minutes and try again, up to three times, before `NEEDS-HUMAN`; and when the board answers again after the ask, the group posts `RESOLVED` on its own ask (plan 52) so it stops waiting on a person.

## Tasks

1. `github.py`, `tests/fakegh.py` (`connect_fail_next`), `tests/test_github.py`, `prompts/_preamble.md`.
2. Spec section 7 (the module line), `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green.

## Carried

- The viewer's poll also waits out a blip, up to a minute, before its strip turns red.
