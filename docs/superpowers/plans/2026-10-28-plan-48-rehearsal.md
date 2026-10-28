# Plan 48 — the free dress rehearsal (2026-10-28)

**Goal.** The whole of meld runs end to end in the suite, on one machine, with no network and no money, so the next defect in a seam between components is found before a paid run.

**From the spec.** Section 19 (testing), section 20 (the wizard), section 17 (what a supervisor and a collect do with a busy model).

**What the owner said.** "I don't want to keep running into issues like that" (after four defects in the first twenty minutes of the first real run, each in a seam the hermetic tests never crossed).

## Decisions (each reversible)

1. **A stand-in GitHub, stateful and git-backed.** `rehearsal.github.FakeGitHub` serves the subset of the API meld uses over loopback HTTP; pull requests, files, commits and squash merges are the real thing on a bare repository; identity is the token (the person's, a PAT, an installation token minted for an App). *Not taken:* extending the in-process `FakeGh` router, which cannot serve a subprocess.
2. **The real `gh` and CLI entry points are shimmed, not bypassed.** `rehearsal.ghshim` is the `gh` on PATH; `rehearsal.cli` is the `claude` and `codex` on PATH; the real adapters launch them, the real doctor probes them, the real transcripts parse them.
3. **The stand-in AIs are a policy over `legal`.** `rehearsal.actor` does what the board says is legal in a fixed order and nothing else, so a refusal or an ask for a person is a defect in meld, not in the stand-in.
4. **One seam in product code.** `appauth.api_base()` reads `MELD_REHEARSAL_GITHUB`; everything else is PATH, environment and the wizard's own entry point (`cli.setup_viewer`, factored out of `meld start`).
5. **The first worker dies of capacity.** The rehearsal exercises plan 50's retry as part of the normal run.

## Tasks

1. `src/meld/rehearsal/` (`github.py`, `ghshim.py`, `cli.py`, `actor.py`), `appauth.api_base`, `appsetup`, `cli.setup_viewer`; `tests/test_rehearsal_github.py`, `tests/test_rehearsal.py`.
2. Spec section 19, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings; the rehearsal passes in about forty seconds.

## Carried

- The stand-in GitHub serves one repository and no pagination beyond one page; a run that exceeds a hundred comments on the thread would need pages.
- The audit after the merge is not exercised: the stand-in PM ends its session once every issue is closed.
- Two accounts in `gh` (plan 45) are not modelled; `gh auth token --user` answers the person's token for any user.
