# Plan 55 — faster and cheaper (2026-10-28)

**Goal.** A run spends its tokens on the work, not on relaunching, re-reading and talking about talking.

**From the spec.** Section 9 (roles, the rubric, dispatch), section 17 (the supervisor's relaunch rows), section 8 (posting).

**What the owner said.** "Look at the agent communication from the maze prototype. What can we improve to make things go faster and be more efficient? It's still working on the first issue, and the cost is high and it's taken a long time."

**What the maze run showed.** 181 posts in three hours (109k tokens of thread), four ROSTER posts of 28k to 38k characters, 25 PROPOSE-WORKER essays, 18 ACKs; the codex PM launched 48 times, each launch re-reading an 11k-token prompt and the thread: 65M input tokens; seven builds on one issue, the last three at max effort on the largest model, five of them lost to causes that were not the work.

## Decisions (each reversible)

1. **Continue the session instead of relaunching it.** After a clean exit or a capacity stop, a role whose transcript names its session is relaunched as that session (`LaunchSpec.resume`; Codex `exec resume`, Claude `--resume`) with the launch block as its whole prompt; a role's Codex session is no longer `--ephemeral`; a crash or a recycle starts afresh; `MAX_RESUMES` (24) bounds the chain.
2. **One line per roster entry.** `roster.brief` in the prompt and in `meld roster` (`--full` for the rest).
3. **Less talk.** The prompts: ACK only an ASK, DIRECTIVE or PROPOSE addressed to you; PROPOSE-WORKER is for proposing a worker type, retry notes go through `meld tag` and the collect comment; STATUS at its cadence, not on every launch.
4. **An attempt budget per issue.** `RunRecord.attempt`, `budget.attempts_per_issue` (3), `rules._dispatch` refuses past it after a failed or blocked build and tells the PM a person decides; the store now reads `attempt` and plan 50's `ended_at` back (it had been dropping both, so the capacity cooldown never held).
5. **Criteria the builder can check**, and browser checks named as the reviewer's (orchestrator prompt); push-fix only for a single waved-through commit (reviewer prompt).
6. **The plan-issues race** from plan 54 is on this branch too.

## Tasks

1. `adapters/base.py`, `adapters/codex.py`, `adapters/claude.py`, `transcript.py` (`session_id_of`), `prompts.py` (`continue_prompt`, the brief roster), `runners.py`, `roster.py` (`brief`), `cli.py` (`roster --full`), `config.py`, `board.py`, `state.py`, `rules.py`, `dispatch.py`, the three prompts; tests throughout.
2. Spec sections 9 and 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the rehearsal run three times for the race.

## Carried

- Whether a resumed Codex session keeps its sandbox and working directory is taken from Codex's own `resume` help (it accepts neither flag); the first real relaunch will say.
- A resumed role's transcript is a new log; the page's transcript view shows the continuation only.
