# Plan 74 — the link is on GitHub (2026-11-03)

**Goal.** When Meld starts running for a repository, the repository says where to watch it.

**From the spec.** Section 22 (the viewer).

**What the runs showed.** The viewer's URL is printed once by `meld start` and otherwise lives only in a browser tab; on the maze and linkcheck runs the owner opened GitHub, saw the issues move, and had no way from there to the page that explains them.

## Decisions (each reversible)

1. **Two places, one writer.** `dashboard.publish(gh, link)` PATCHes the repository's `homepage` and rewrites a `## Dashboard` section at the end of the coordination issue's body (`find_active_milestone` names the issue). Idempotent: an unchanged body is not rewritten. The section names the host, because the page is loopback-only (section 22) and the link works from that machine alone.
2. **Never fatal.** `try_publish` turns `GitHubError` or `BoardError` into a one-line note; `meld start`/`watch` print it to stderr and serve anyway; the wizard's `_adopt_context` ignores it.
3. **The agents never read it.** `assemble.build_board` strips the section from `goal`, so prompts and briefs are unchanged.

## Tasks

1. `src/meld/dashboard.py`; hooks in `cli.watch` (shared by `start`) and `viewer/server.py::_adopt_context`; `assemble.build_board`.
2. `tests/test_dashboard.py` (section append/replace/strip, both PATCHes over `FakeGh`, idempotence, the note on failure).
3. Spec section 22, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the next `meld start` on linkcheck should set `https://github.com/smidley/meld-linkcheck`'s homepage and append the section to issue #1.

## Carried

- A repository without a coordination issue yet (a fresh wizard run before setup) only gets the homepage once the board exists; the wizard's adoption hook covers that.
