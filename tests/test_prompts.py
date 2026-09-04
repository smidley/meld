from meld.roster import builtin_workers

REQUIRED = ("## Focus", "## Method", "## Definition of done", "## Evidence you must record")


def test_every_builtin_worker_has_the_full_brief_sections():
    workers = {w.name: w for w in builtin_workers()}
    assert set(workers) == {"auditor", "backend", "docs", "frontend", "qa", "reviewer", "security"}
    for w in workers.values():
        for section in REQUIRED:
            assert section in w.text, (w.name, section)
        assert "verified_by" in w.text or "verified-by" in w.text, w.name


def test_reviewer_and_auditor_prompts_carry_the_verification_rules():
    workers = {w.name: w for w in builtin_workers()}
    reviewer, auditor = workers["reviewer"].text, workers["auditor"].text
    for needle in ("open the file", "request-changes", "push-fix", "approve", "no style-only", "correctness", "security"):
        assert needle in reviewer, needle
    for needle in ("acceptance criteria", "pass", "fail", "re-run", "verified_by"):
        assert needle in auditor, needle
