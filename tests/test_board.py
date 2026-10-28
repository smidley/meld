from meld.board import (
    Issue,
    Lease,
    derive_halted,
    derive_leases,
    derive_open_challenges,
    derive_phase,
    parse_lease_until,
    utc_now_iso,
)
from tests.factories import CFG2, mk_board, mk_comment, mk_issue, mk_lease, mk_pr, mk_run


def test_issue_status_precedence_and_labels():
    i = mk_issue(1, status="ready")
    assert i.status == "ready"
    assert i.worker == "backend" and i.model == "claude-sonnet-5" and i.effort == "medium" and i.size == "S"
    both = Issue(2, "t", "", frozenset({"meld:blocked", "meld:needs-human", "meld:in-progress"}), "open", 3)
    assert both.status == "needs_human"
    assert Issue(3, "t", "", frozenset(), "open", 3).status == "unlabeled"
    assert Issue(4, "t", "", frozenset({"meld:ready"}), "closed", 3).status == "closed"
    assert mk_issue(5, extra_labels=("meld:challenged",)).challenged
    assert mk_issue(6, worker=None).worker is None


def test_issue_has_no_assignees_field():
    from dataclasses import fields
    assert "assignees" not in {f.name for f in fields(Issue)}


def test_pr_review_math_uses_latest_review_per_author_on_head():
    pr = mk_pr(20, 13, committer="meld-codex-bot", approvals=("meld-claude-bot",), stale_approvals=("empowerScott",))
    assert pr.approvals_on_head() == {"meld-claude-bot"}
    assert pr.changes_requested_on_head() == frozenset()
    pr2 = mk_pr(21, 14, changes_requested=("meld-claude-bot",), extra_changes_requested_history=2)
    assert pr2.changes_requested_on_head() == {"meld-claude-bot"}
    assert pr2.changes_requested_total() == 3
    assert pr2.latest_reviews()["meld-claude-bot"].state == "CHANGES_REQUESTED"
    # the cap counts distinct (reviewer, head) pairs: a repeated verdict on one head counts once, a dismissed one never
    from dataclasses import replace
    from meld.board import Review
    dup = replace(pr2, reviews=pr2.reviews + (Review("meld-claude-bot", "CHANGES_REQUESTED", "abc123", "2026-09-03T12:00:00Z"),
                                              Review("meld-codex-bot", "DISMISSED", "abc123", "2026-09-03T12:30:00Z")))
    assert dup.changes_requested_total() == 3
    # two old rounds whose commits GitHub has forgotten (commit_id read back as "") are still two rounds
    gone = replace(pr2, reviews=(Review("meld-codex-bot", "CHANGES_REQUESTED", "", "2026-09-03T08:00:00Z"),
                                 Review("meld-codex-bot", "CHANGES_REQUESTED", "", "2026-09-03T08:30:00Z")))
    assert gone.changes_requested_total() == 2


def test_derive_phase_and_halted():
    assert derive_phase(frozenset({"meld:coordination"})) == "planning"
    assert derive_phase(frozenset({"meld:coordination", "meld:plan-approved"})) == "executing"
    cs = [mk_comment(CFG2, 1, "empowerScott", "[human] HALT\n\nstop"),
          mk_comment(CFG2, 2, "randouser", "[human] RESUME\n\ngo")]
    assert derive_halted(cs) is True  # imposter RESUME ignored
    cs.append(mk_comment(CFG2, 3, "empowerScott", "[human] RESUME\n\ngo"))
    assert derive_halted(cs) is False


def test_derive_open_challenges_closes_on_verdict_or_retract():
    cs = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex] CHALLENGE #12\n\ndoubt"),
          mk_comment(CFG2, 2, "meld-codex-bot", "[codex] CHALLENGE #20\n\ndoubt"),
          mk_comment(CFG2, 3, "meld-claude-bot", "[claude] RETRACT #20\n\nfair"),
          mk_comment(CFG2, 4, "meld-codex-bot", "[codex] CHALLENGE plan-1\n\ndoubt"),
          mk_comment(CFG2, 5, "empowerScott", "[human] DISMISSED plan-1\n\nfine")]
    assert derive_open_challenges(cs) == {"#12"}


def test_board_helpers():
    b = mk_board(CFG2,
                 issues=[mk_issue(11), mk_issue(12, status="in_progress", claimed_by="claude"),
                         mk_issue(13, status="in_review")],
                 pulls=[mk_pr(20, 13)],
                 runs={"claude": {12: mk_run("claude", 12)}, "codex": {}},
                 open_challenges=frozenset({"#12"}))
    assert b.in_progress_count("claude") == 1
    assert b.pr_for_issue(13).number == 20 and b.pr_for_issue(11) is None
    assert b.run_for("claude", 12).alive and b.run_for("codex", 12) is None
    assert b.is_challenged("#12") and not b.is_challenged("#11")
    assert not mk_run("claude", 1, alive=True).finished
    assert mk_run("claude", 1, alive=False).finished
    assert not mk_run("claude", 1, alive=False, collected=True).finished


def test_parse_lease_until_and_derive_leases():
    assert parse_lease_until("until: 2026-09-03T13:00:00Z\nrun: claude/#12") == "2026-09-03T13:00:00Z"
    assert parse_lease_until("no lease here") is None
    cs = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:00:00Z"),
          mk_comment(CFG2, 2, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: 2026-09-03T13:30:00Z"),
          mk_comment(CFG2, 3, "meld-codex-bot", "[codex:pm] LEASE #13\n\nuntil: 2026-09-03T14:00:00Z"),
          mk_comment(CFG2, 4, "randouser", "[codex:pm] LEASE #14\n\nuntil: 2099-01-01T00:00:00Z"),
          mk_comment(CFG2, 5, "meld-claude-bot", "[claude:pm] EXPIRED #13\n\nlease expired")]
    leases = derive_leases(cs)
    assert leases == {12: Lease(12, "claude", "2026-09-03T13:30:00Z", 2)}
    assert len(utc_now_iso()) == 20 and utc_now_iso().endswith("Z")
    b = mk_board(CFG2, issues=[mk_issue(12, status="in_progress")],
                 leases={12: mk_lease(12, until="2026-09-03T11:00:00Z")}, now="2026-09-03T12:00:00Z")
    assert b.lease_expired(12) and not b.lease_expired(99)


def test_malformed_lease_until_is_dropped():
    assert parse_lease_until("until: 2026-09-03T13:00:00+00:00") is None
    assert parse_lease_until("until: notadate") is None
    assert parse_lease_until("until: 2026-09-03T13:00:00Z") == "2026-09-03T13:00:00Z"
    cs = [mk_comment(CFG2, 1, "meld-claude-bot", "[claude:pm] LEASE #12\n\nuntil: soon")]
    assert derive_leases(cs) == {}


from dataclasses import replace

from meld.board import Challenge, derive_audits, derive_challenges
from tests.factories import CFG2, mk_comment, mk_pr


def test_derive_challenges_records_challenger_and_closers():
    comments = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex] CHALLENGE #12\n\nthe test claim is false"),
                mk_comment(CFG2, 2, "meld-claude-bot", "[claude] CHALLENGE plan-1\n\nscope moved"),
                mk_comment(CFG2, 3, "empowerScott", "[human] DISMISSED #12\n\nverified myself")]
    ch = derive_challenges(comments)
    assert set(ch) == {"plan-1"} and ch["plan-1"] == Challenge("plan-1", "claude", 2, comments[1].created_at)
    human = derive_challenges([mk_comment(CFG2, 4, "empowerScott", "[human] CHALLENGE #13\n\nno evidence")])
    assert human["#13"].challenger == "human:empowerScott"
    reopened = derive_challenges(comments + [mk_comment(CFG2, 5, "meld-claude-bot", "[claude] CHALLENGE #12\n\nagain")])
    assert reopened["#12"].challenger == "claude" and reopened["#12"].comment_id == 5


def test_derive_audits_keeps_the_latest_verdict_per_group():
    comments = [mk_comment(CFG2, 1, "meld-codex-bot", "[codex:pm] AUDIT #19 FAIL\n\ncriterion 2 unmet"),
                mk_comment(CFG2, 2, "meld-codex-bot", "[codex:pm] AUDIT #19 PASS\n\nre-checked after the fix"),
                mk_comment(CFG2, 3, "meld-claude-bot", "[claude] AUDIT #19 PASS\n\nok"),
                mk_comment(CFG2, 4, "meld-claude-bot", "[claude] AUDIT plan-1 PASS\n\nnot a PR ref; ignored")]
    assert derive_audits(comments) == {19: {"codex": "pass", "claude": "pass"}}


def test_pull_request_touches_security_paths():
    pr = replace(mk_pr(20, 13), files=("src/auth/login.py", "README.md"))
    assert pr.touches(("src/auth/*",)) and pr.touches(("*.md",)) and not pr.touches(("db/*",)) and not pr.touches(())


def test_pr_for_issue_prefers_the_merged_pr_once_the_issue_is_closed():
    open_pr = mk_pr(22, 11, state="open")
    merged = mk_pr(21, 11, state="closed", merged=True)
    assert mk_board(CFG2, issues=[mk_issue(11)], pulls=[open_pr, merged]).pr_for_issue(11).number == 22
    closed = mk_issue(11, state="closed")
    assert mk_board(CFG2, issues=[closed], pulls=[open_pr, merged]).pr_for_issue(11).number == 21


def test_released_ends_a_lease_and_a_reclaim_does_not_revive_it():
    from tests.factories import mk_comment
    from meld.board import derive_leases
    comments = [
        mk_comment(1, "meld-claude-bot", "[claude:pm] LEASE #11\n\nuntil: 2026-09-03T12:00:00Z\nrun: claude/#11\nkind: build", "2026-09-03T10:00:00Z"),
        mk_comment(2, "meld-claude-bot", "[claude] RELEASED #11\n\nReleased #11.", "2026-09-03T10:30:00Z"),
    ]
    assert derive_leases(comments) == {}


def test_pr_for_issue_prefers_the_current_holders_pr_among_open_ones():
    from tests.factories import mk_board, mk_issue, mk_pr
    board = mk_board(issues={11: mk_issue(11, "in_progress", claimed_by="codex")},
                     pulls={30: mk_pr(30, 11, author="meld-claude-bot"), 31: mk_pr(31, 11, author="meld-codex-bot"), 32: mk_pr(32, 11, author="meld-claude-bot")})
    assert board.pr_for_issue(11).number == 31


def test_issue_derives_its_claim_from_the_claimed_by_labels():
    from meld.board import CLAIM_LABEL_PREFIX, claim_group, claim_label
    from tests.factories import mk_issue
    assert claim_label("codex") == "meld:claimed-by:codex" and CLAIM_LABEL_PREFIX == "meld:claimed-by:"
    assert claim_group("meld:claimed-by:codex") == "codex" and claim_group("meld:in-progress") is None and claim_group("meld:claimed-by:") is None
    free = mk_issue(11)
    assert free.claimants == () and free.claimed_by is None
    held = mk_issue(12, status="in_progress", claimed_by="claude")
    assert held.claimants == ("claude",) and held.claimed_by == "claude"
    contested = mk_issue(13, status="in_progress", extra_labels=("meld:claimed-by:codex", "meld:claimed-by:claude"))
    assert contested.claimants == ("claude", "codex") and contested.claimed_by is None


def test_claim_labels_are_read_case_insensitively():
    """GitHub label names are case-insensitive for uniqueness, so a hand-made `Meld:Claimed-By:Claude` is the same
    label as ours. Group names are lowercase by rule (config.GROUP_NAME_RE), so the group it names is lowercased too."""
    from meld.board import claim_group
    from tests.factories import mk_issue
    assert claim_group("MELD:CLAIMED-BY:Claude") == "claude"
    assert claim_group("Meld:Claimed-By:CODEX") == "codex"
    assert claim_group("MELD:CLAIMED-BY:") is None and claim_group("MELD:IN-PROGRESS") is None
    odd = mk_issue(11, status="in_progress", extra_labels=("meld:Claimed-by:Claude",))
    assert odd.claimants == ("claude",) and odd.claimed_by == "claude"


def test_held_leases_keep_only_the_lease_whose_group_holds_the_label():
    from meld.board import Lease, held_leases
    from tests.factories import mk_issue
    issues = {12: mk_issue(12, status="in_progress", claimed_by="claude"),
              13: mk_issue(13, status="in_progress", claimed_by="codex"),
              14: mk_issue(14, status="in_progress"),
              15: mk_issue(15, status="in_progress", extra_labels=("meld:claimed-by:claude", "meld:claimed-by:codex"))}
    leases = {12: Lease(12, "claude", "2026-09-03T12:00:00Z"), 13: Lease(13, "claude", "2026-09-03T12:00:00Z"),
              14: Lease(14, "codex", "2026-09-03T12:00:00Z"), 15: Lease(15, "codex", "2026-09-03T12:00:00Z"),
              99: Lease(99, "codex", "2026-09-03T12:00:00Z")}
    # 13: codex holds, lease says claude; 14: nobody holds; 15: contested, codex is a claimant so its lease survives; 99: no such issue
    assert set(held_leases(leases, issues)) == {12, 15}


def test_in_progress_count_and_holder_login_use_the_claim_label():
    from tests.factories import CFG2, mk_board, mk_issue, mk_pr
    board = mk_board(issues={12: mk_issue(12, status="in_progress", claimed_by="claude"),
                             13: mk_issue(13, status="in_progress", claimed_by="claude"),
                             14: mk_issue(14, status="in_progress", claimed_by="codex")},
                     pulls={20: mk_pr(20, 12, author="meld-codex-bot"), 21: mk_pr(21, 12, author="meld-claude-bot")})
    assert board.in_progress_count("claude") == 2 and board.in_progress_count("codex") == 1 and board.in_progress_count("human") == 0
    assert board.holder_login(12) == "meld-claude-bot" and board.holder_login(99) is None
    assert board.pr_for_issue(12).number == 21   # the holder's open PR beats a stale one from another group


def test_claimants_dedupe_case_variants_of_one_group():
    """claim_group lowercases, so `meld:claimed-by:Claude` and `meld:claimed-by:claude` are one claimant, not a contested pair."""
    from tests.factories import mk_issue
    issue = mk_issue(12, status="in_progress", extra_labels=("meld:claimed-by:Claude", "meld:claimed-by:claude"))
    assert issue.claimants == ("claude",) and issue.claimed_by == "claude"


def test_issue_declarations_come_from_body_sections_and_held_labels():
    from meld.board import HELD_CATEGORIES, HELD_LABEL_PREFIX, body_section, held_label
    from tests.factories import mk_issue
    body = ("plan-item: p1\n\n## Acceptance criteria\n- works\n\n## Scope\n- src/meld/board.py\n- `tests/test_board*.py`\n\n"
            "## May delete\n- src/meld/legacy.py\n\n## Notes\n- not a scope line\n")
    assert body_section(body, "Scope") == ("src/meld/board.py", "tests/test_board*.py")
    assert body_section(body, "May delete") == ("src/meld/legacy.py",)
    assert body_section(body, "Missing") == () and body_section("", "Scope") == ()
    issue = mk_issue(12, status="in_progress", claimed_by="claude", body=body, extra_labels=("meld:held:delete", "meld:held:public-release"))
    assert issue.scope == ("src/meld/board.py", "tests/test_board*.py") and issue.may_delete == ("src/meld/legacy.py",)
    assert issue.held == ("delete", "public-release")
    assert held_label("delete") == HELD_LABEL_PREFIX + "delete" and "credential-change" in HELD_CATEGORIES and len(HELD_CATEGORIES) == 7
    assert mk_issue(13).scope == () and mk_issue(13).held == ()


def test_board_reports_scope_violations_and_blocked_deletions():
    from tests.factories import mk_board, mk_issue, mk_pr
    body = "## Scope\n- src/meld/*.py\n- tests/**\n\n## May delete\n- src/meld/legacy.py\n"
    scoped = mk_issue(12, status="in_review", claimed_by="claude", body=body)
    free = mk_issue(13, status="in_review", claimed_by="codex")
    pr_ok = mk_pr(20, 12, files=("src/meld/board.py", "tests/test_board.py"))
    pr_out = mk_pr(21, 12, files=("src/meld/board.py", "README.md", "docs/x.md"))
    pr_del = mk_pr(22, 12, files=("src/meld/board.py", "src/meld/legacy.py", "src/meld/old.py"), removed=("src/meld/legacy.py", "src/meld/old.py"))
    pr_free = mk_pr(23, 13, files=("anything/at/all.py", "gone.py"), removed=("gone.py",))
    board = mk_board(issues={12: scoped, 13: free}, pulls={20: pr_ok, 21: pr_out, 22: pr_del, 23: pr_free})
    assert board.scope_violations(pr_ok) == () and board.blocked_deletions(pr_ok) == ()
    assert board.scope_violations(pr_out) == ("README.md", "docs/x.md")
    assert board.blocked_deletions(pr_del) == ("src/meld/old.py",)          # legacy.py is listed, old.py is not
    assert board.scope_violations(pr_free) == ()                              # no scope declared: no scope check
    assert board.blocked_deletions(pr_free) == ("gone.py",)                   # the deletion gate is always on


def test_needs_human_answered_needs_a_listed_humans_answer_after_the_ask():
    from tests.factories import mk_board, mk_comment, mk_issue
    issue = mk_issue(14, status="needs_human", claimed_by="codex")
    ask = mk_comment(50, "meld-codex-bot", "[codex] NEEDS-HUMAN #14\n\nheld: delete", "2026-09-05T10:00:00Z")
    stranger = mk_comment(51, "someone", "[human] RESOLVED #14\n\ndone", "2026-09-05T10:05:00Z")
    early = mk_comment(49, "empowerScott", "[human] RESOLVED #14\n\nold answer", "2026-09-05T09:00:00Z")
    answer = mk_comment(52, "empowerScott", "[human] PROVIDED #14\n\nauthorized", "2026-09-05T10:10:00Z")
    other = mk_comment(53, "empowerScott", "[human] RESOLVED #15\n\nwrong issue", "2026-09-05T10:11:00Z")
    assert mk_board(issues={14: issue}, comments=[ask]).needs_human_answered(14) is False
    assert mk_board(issues={14: issue}, comments=[ask, stranger]).needs_human_answered(14) is False   # not a listed human
    assert mk_board(issues={14: issue}, comments=[early, ask]).needs_human_answered(14) is False      # answered before the ask
    assert mk_board(issues={14: issue}, comments=[ask, answer, other]).needs_human_answered(14) is True
    assert mk_board(issues={14: issue}, comments=[ask, answer]).human_answer(14).id == 52
    # I4: an ask must exist. A `meld:needs-human` label with nothing behind it never auto-unlocks -- otherwise an
    # issue that had ever received a human PROVIDED/RESOLVED was un-gated the moment anyone re-held it.
    assert mk_board(issues={14: issue}, comments=[answer]).needs_human_answered(14) is False
    human_ask = mk_comment(54, "empowerScott", "[human] NEEDS-HUMAN #14\n\nheld: delete", "2026-09-05T10:20:00Z")
    assert mk_board(issues={14: issue}, comments=[ask, answer, human_ask]).needs_human_answered(14) is False   # re-held by a human
    later = mk_comment(55, "empowerScott", "[human] RESOLVED #14\n\nauthorized again", "2026-09-05T10:25:00Z")
    assert mk_board(issues={14: issue}, comments=[ask, answer, human_ask, later]).needs_human_answered(14) is True


def test_held_labels_and_section_headings_match_case_insensitively():
    """Task 1 review: a hand-typed `Meld:Held:Delete` must still engage the dispatch gate (GitHub label names are unique
    case-insensitively), and `## SCOPE` is the same heading as `## Scope`."""
    from meld.board import body_section
    from tests.factories import mk_issue
    assert mk_issue(12, extra_labels=("Meld:Held:Delete", "meld:held:public-release")).held == ("delete", "public-release")
    body = "## SCOPE\n- src/meld/*.py\n"
    assert body_section(body, "Scope") == ("src/meld/*.py",) and body_section(body, "scope") == ("src/meld/*.py",)


def test_body_section_ignores_headings_and_bullets_inside_fenced_code():
    """M9: an issue body routinely quotes a diff or a sample body in a fence. Neither a `## ` line nor a `- ` bullet
    inside one is a declaration -- reading them let a quoted example widen the real scope or the deletion allowlist."""
    from meld.board import body_section
    body = ("Reproduce with:\n\n```\n## Scope\n- everything/*\n```\n\n"
            "## Scope\n- src/meld/*.py\n\n"
            "Example of what NOT to write:\n\n```md\n## May delete\n- src/meld/rules.py\n```\n\n"
            "## May delete\n- src/meld/legacy.py\n")
    assert body_section(body, "Scope") == ("src/meld/*.py",)
    assert body_section(body, "May delete") == ("src/meld/legacy.py",)
    # A fence opened inside the section ends nothing: the bullets after it are still the section's own.
    fenced_inside = "## Scope\n- a.py\n\n```\n- fake.py\n## May delete\n- b.py\n```\n\n- c.py\n"
    assert body_section(fenced_inside, "Scope") == ("a.py", "c.py") and body_section(fenced_inside, "May delete") == ()


def test_replace_section_rewrites_in_place_appends_when_absent_and_removes_tidily():
    from meld.board import body_section, replace_section
    body = "Goal text\n\n## Scope\n- src/a.py\n\n## May delete\n- src/old.py\n\n## Notes\n- keep me\n"
    rewritten = replace_section(body, "scope", ["docs/**", "`src/b.py`"])
    assert rewritten == "Goal text\n\n## Scope\n- docs/**\n- `src/b.py`\n\n## May delete\n- src/old.py\n\n## Notes\n- keep me\n"
    assert body_section(rewritten, "Scope") == ("docs/**", "src/b.py")                    # round-trips through the reader
    assert replace_section(body, "May delete", []) == "Goal text\n\n## Scope\n- src/a.py\n\n## Notes\n- keep me\n"   # a middle section
    assert replace_section(body, "Notes", []) == "Goal text\n\n## Scope\n- src/a.py\n\n## May delete\n- src/old.py\n"  # the last: no stray blank
    assert replace_section("## Scope\n- src/a.py\n", "Scope", []) == ""                     # the only section
    assert replace_section(body, "Missing", []) == body                                       # nothing to remove, nothing changes
    assert replace_section("Goal text", "Scope", ["src/*.py"]) == "Goal text\n\n## Scope\n- src/*.py\n"   # appended, one blank between
    assert replace_section("Goal text\n\n", "Scope", ["src/*.py"]) == "Goal text\n\n## Scope\n- src/*.py\n"
    assert replace_section("", "Scope", ["src/*.py"]) == "## Scope\n- src/*.py\n"
    assert replace_section(None, "Scope", []) == ""
    # Blank lines before the rewritten section are outside it and stay; the ones after it belong to it and become one.
    assert replace_section("Goal\n\n\n## Scope\n- a\n\n\n## Notes\n- n\n", "Scope", ["b"]) == "Goal\n\n\n## Scope\n- b\n\n## Notes\n- n\n"


def test_replace_section_ignores_headings_inside_fenced_code_like_body_section_does():
    from meld.board import body_section, replace_section
    quoted = "Goal\n\n```\n## Scope\n- quoted/*.py\n```\n"
    body = quoted + "\n## Scope\n- real/*.py\n\n## Notes\n- n\n"
    rewritten = replace_section(body, "Scope", ["new/*.py"])
    assert rewritten == quoted + "\n## Scope\n- new/*.py\n\n## Notes\n- n\n"           # the fence is untouched
    assert body_section(rewritten, "Scope") == ("new/*.py",)
    assert replace_section(body, "Scope", []) == quoted + "\n## Notes\n- n\n"
    assert replace_section(quoted, "Scope", ["a"]) == quoted + "\n## Scope\n- a\n"       # only a quoted heading: append a real one
    fenced_end = "## Scope\n- a\n\n```\n## Notes\n```\n- still scope\n"
    assert replace_section(fenced_end, "Scope", []) == ""                                      # a fenced heading does not end the section


def test_an_unterminated_fence_is_inert_so_declarations_never_vanish():
    from meld.board import body_section, fenced_lines, replace_section
    body = "Goal text\n\nRepro:\n\n```python\nprint(1)\n\n## Scope\n- src/meld/*.py\n\n## May delete\n- src/old.py\n"
    assert body_section(body, "Scope") == ("src/meld/*.py",) and body_section(body, "May delete") == ("src/old.py",)
    rewritten = replace_section(body, "Scope", ["src/meld/new.py"])
    assert rewritten.count("## Scope") == 1 and body_section(rewritten, "Scope") == ("src/meld/new.py",)
    assert fenced_lines(["```", "## Scope", "```", "x"]) == frozenset({0, 1, 2}) and fenced_lines(["```", "## Scope"]) == frozenset()


def test_the_group_that_asked_may_withdraw_its_own_ask_but_a_persons_hold_stays(tmp_path):
    """Plan 52: the maze run's codex PM asked the owner to restore a browser service, codex's own orchestrator restored it two
    minutes later, and the ask still waited on the owner. The asking group's `RESOLVED` after its ask closes it; another
    group's does not, and a person's own `NEEDS-HUMAN` is cleared only by a person."""
    from tests.factories import mk_board, mk_comment, mk_issue
    issue = mk_issue(3, status="needs_human", claimed_by="codex")
    ask = mk_comment(60, "meld-codex-bot", "[codex:pm] NEEDS-HUMAN #3\n\nrestore the browser service", "2026-10-28T18:35:00Z")
    withdrawn = mk_comment(61, "meld-codex-bot", "[codex] RESOLVED #3\n\nthe service is back at 127.0.0.1:9222", "2026-10-28T18:37:00Z")
    other = mk_comment(62, "meld-claude-bot", "[claude] RESOLVED #3\n\nlooks fine from here", "2026-10-28T18:38:00Z")
    assert mk_board(issues={3: issue}, comments=[ask]).needs_human_answered(3) is False and mk_board(issues={3: issue}, comments=[ask]).open_ask("#3").id == 60
    assert mk_board(issues={3: issue}, comments=[ask, withdrawn]).needs_human_answered(3) is True and mk_board(issues={3: issue}, comments=[ask, withdrawn]).open_ask("3") is None
    assert mk_board(issues={3: issue}, comments=[ask, other]).needs_human_answered(3) is False                 # not the asking group
    hold = mk_comment(63, "empowerScott", "[human] NEEDS-HUMAN #3\n\nheld: delete", "2026-10-28T18:40:00Z")
    again = mk_comment(64, "meld-codex-bot", "[codex:pm] NEEDS-HUMAN #3\n\nand now a credential", "2026-10-28T18:41:00Z")
    gone = mk_comment(65, "meld-codex-bot", "[codex] RESOLVED #3\n\nnever mind the credential", "2026-10-28T18:42:00Z")
    assert mk_board(issues={3: issue}, comments=[ask, withdrawn, hold, again, gone]).needs_human_answered(3) is False   # the person's hold stands
    cleared = mk_comment(66, "empowerScott", "[human] RESOLVED #3\n\ngo ahead", "2026-10-28T18:43:00Z")
    assert mk_board(issues={3: issue}, comments=[ask, withdrawn, hold, again, gone, cleared]).needs_human_answered(3) is True
