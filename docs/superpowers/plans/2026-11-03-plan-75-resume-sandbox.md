# Plan 75 — a resumed codex session keeps its sandbox (2026-11-03)

**Goal.** A codex role relaunched as a resume can still reach GitHub.

**From the spec.** Section 15 (the codex adapter), section 17 (launches and resumes, plan 55).

**What the linkcheck run showed.** Codex's PM planned milestone 1 on its first launch, then exited cleanly; launches 2 to 5 were `codex exec resume` and each ran one turn in which `meld status` failed with "error connecting to api.github.com", the session reporting "restricted network access and a read-only filesystem". Codex's orchestrator, on its own second launch, sat in the same failure. `codex exec resume` (codex-cli 0.159.1) has no `-s` flag and does not restore the sandbox the session was started with; the adapter's comment said it did.

## Decisions (each reversible)

1. **Restate the sandbox on resume as config.** `resume` accepts `-c key=value`; `sandbox_mode` is the config key behind `-s`. `resume_sandbox_args(role)` gives `danger-full-access` for a trusted role and `workspace-write` plus network for anything else (workers never resume today; the branch is there so a resume can never be more restricted than the launch).
2. **No `--dangerously-bypass-approvals-and-sandbox`.** It also exists on `resume`, but it drops approvals as well as the sandbox, which the fresh launch never did.

## Tasks

1. `adapters/codex.py`; `tests/test_adapter_codex.py`.
2. Spec section 17, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green; after the codex supervisor is restarted on linkcheck, its next resumed launch should run `meld status` successfully (the log shows no "error connecting").

## Carried

- I'm assuming `-c sandbox_mode=...` is honoured by `exec resume` the way the fresh launch honours `-s`; the first resumed launch after the restart is the check. If it is not, the fallback is to start afresh (no resume) for codex.
