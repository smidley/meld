# Plan 73 — the prompt ends by saying begin (2026-10-30)

**Goal.** A fresh session never mistakes its instructions for a message to comment on.

**From the spec.** Section 9 (the role prompt), section 17 (launches and resumes).

**What the maze run showed.** On fresh launches claude's PM and orchestrator read the role prompt and replied, in one turn for about $0.14, that it was "an elaborate Meld multi-agent framework description with no actual question or task attached" and "doesn't match this session's actual environment" (the checkout's folder is `greeter`, the repository `meld-test-maze`), then exited; the supervisor relaunched within seconds. Seventy-four such sessions in one day, in bursts of two or three before a session that worked.

## Decisions (each reversible)

1. **`## Begin` closes the role prompt**, after the launch block: the text is standing instructions, not a message to assess; the role and group; the working directory is the repository's checkout whatever its folder is called; start with `meld status`, act on what is legal, then `meld wait`; no commentary about the prompt.

## Tasks

1. `prompts.py`; `tests/test_role_prompts.py`.
2. Spec section 9, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; the next fresh launches on the maze should run `meld status` instead of declining.

## Carried

- A declined fresh session is a one-turn exit that the supervisor treats as a crash; if the closing section is not enough, the adapter could recognise the decline and retry with the continue prompt.
