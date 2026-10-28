# Plan 47 — an agreement ref's `legal` names the verbs of the round (2026-10-28)

**Goal.** A role reading `meld status` sees what it may post on an open agreement, so an empty list never means "wait for a human".

**From the spec.** Section 8 (agreement rounds and `legal`), the preamble's rule 1 (act only on listed verbs).

**What happened.** On the first real run, claude amended codex's plan proposal. `coordination.agreements["plan-1"].legal` listed only `decide`/`plan-*` actions, none of which applied, so codex read `[]` as a prohibition, posted `NEEDS-HUMAN planning-legality` and stopped.

## Decisions (each reversible)

1. **The post verbs join `legal`.** `rules.agreement_post_verbs` mirrors `posting.post`'s conditions: `propose` while there is no text, `amend` under the cap, `agree`/`dissent` on text the caller has not agreed to, `joint` for the proposer once agreed. A PM gets none (it posts no agreement verb). *Not taken:* changing the prompt to say "an empty list means post anyway", which would contradict rule 1.
2. **Lowercase, like the actions.** `agree` beside `decide`, so the list reads as one vocabulary.

## Tasks

1. `rules.py` (`agreement_post_verbs`, `legal_agreement_verbs`); `tests/test_status.py`.
2. Spec section 8, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings.

## Carried

- `propose` is listed for every ref with no text, which is every ref a role could invent; the list is only meaningful for refs the status already shows.
