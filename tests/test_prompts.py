from importlib import resources

from meld.brief import worker_contract
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


def test_worker_contract_and_preamble_carry_the_quality_bar():
    contract = worker_contract()
    assert "Meet the product standards" in contract and "Skills available" in contract
    reviewer = next(w for w in builtin_workers() if w.name == "reviewer").text
    assert "correctness, security, performance, visual design and accessibility" in reviewer and "primary concern" in reviewer
    preamble = resources.files("meld").joinpath("prompts", "_preamble.md").read_text()
    assert "Ship for real users" in preamble and "meld challenge" in preamble
