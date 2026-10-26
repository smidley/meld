# Plan 12 — `legal` is the rule (2026-10-26)

**Goal.** Plan 11 told the PM that `meld status`'s `legal` list is the only set of verbs to run. That makes two older complaints urgent: `legal` advertised verbs the rules then refused (handoff residual 15), and a verb missing from `legal` never said why (residual 8). Both are closed here from one rule table.

**From the spec.** Section 8's rule bullets for `reopen` and `decide`, the sentence on what `meld status` lists, and the principle that `rules.py` is the only legality authority.

## Decisions (each reversible)

1. **A `reopen` needs a FAIL newer than the issue's last close.** `Issue.closed_at` (GitHub's own field) is compared with the newest qualifying `AUDIT ... FAIL`'s `created_at`; an older FAIL refuses with both instants named. A record with no close time keeps the older behaviour, and a human's reopen never needed an audit. *Not taken:* clearing the FAIL's standing when the issue reopens, which would need state outside the board. *Cost if wrong:* an audit posted in the same second as the close is refused until the next one.
2. **`decide` counts a merged memory PR as recorded.** The board already carries merged memory PRs; only open ones were checked, so the file's presence on origin was found by `memory.decide` alone and `legal` listed `decide` for a ref already recorded. *Cost if wrong:* a hand-committed decision file with no PR is still found only by the git check.
3. **Two of residual 15's four cases were already one rule.** `claim` with an unknown model is refused by `_claim` reading the registry, and `review`/`audit` without a run slot already share `_run_slot` on both paths. Recorded rather than changed.
4. **`refused` is the complement of `legal`, from the same table.** `rules.issue_verb_rules` and `pr_verb_rules` map every verb to its rule or `None`; `legal` is the `None`s and `status`'s new `refused` map is the rest. Agreement refs keep `legal` alone. *Not taken:* a `refused` list without rules, which is what `legal` already implied.
5. **The page shows the rules on hover**, in the row's description tooltip, verbatim. *Not taken:* dim words per refused verb, which would add up to eight per row.

## Tasks

1. `board.Issue.closed_at`, `assemble._issue`; `rules._reopen`; `tests/test_rules_issues.py`.
2. `rules.decide_rule`; `tests/test_rules_agreements.py`.
3. `rules.issue_verb_rules`/`pr_verb_rules`, `status._verbs`; `tests/test_status.py`; `viewer/static/app.js`.
4. Spec section 8 (three sentences), README's `meld status` and `meld reopen` rows, `docs/HANDOFF.md`.

## Verification

`uv run pytest -q` green with no warnings. The status test asserts every verb is in exactly one of `legal` and `refused` for an issue and a PR, and that the review-slot and challenge rules appear there verbatim.

## Carried

- Residual 8's second half: nothing says "a review was requested from you and is unanswered on this head" once an older verdict goes stale.
- `legal_agreement_verbs` has no `refused` counterpart.
- The refusal sites outside `rules.py` (`collect`'s run-record checks, `dispatch`'s roster checks, `tag`'s argument checks) are argument validation, not board legality, and stay where they are.
