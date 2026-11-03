"""Plan 74: the viewer's link is on GitHub -- the repository's homepage and a `## Dashboard` section on the coordination issue."""
from meld.dashboard import publish, section, strip_section, try_publish, with_section
from tests.conftest import load_board1_routes
from tests.fakegh import FakeGh
from meld.github import GitHub

R = "repos/acme/widgets/"
LINK = "http://127.0.0.1:8769/?t=abc"


def test_the_section_is_appended_once_replaced_on_change_and_stripped_for_the_agents():
    body = "# Coordination: M1\n\n## Goal\n\nBuild it.\n\n## Context\n\nStart empty.\n"
    first = with_section(body, LINK, "mac.local")
    assert first.startswith(body.rstrip("\n")) and first.count("## Dashboard") == 1 and LINK in first and "`mac.local`" in first
    again = with_section(first, "http://127.0.0.1:1/?t=new", "mac.local")
    assert again.count("## Dashboard") == 1 and "?t=new" in again and LINK not in again
    assert strip_section(again) == body.rstrip("\n") and strip_section(body) == body.rstrip("\n")
    assert with_section("", LINK, "h") == section(LINK, "h") + "\n"


def test_publish_updates_the_coordination_issue_and_the_homepage(tmp_path):
    fake = FakeGh()
    load_board1_routes(fake)
    fake.route("GET", R + "issues/10", {"number": 10, "body": "# Coordination: M1\n\n## Goal\n\nBuild it.\n"})
    fake.route("PATCH", R + "issues/10", {"number": 10})
    fake.route("PATCH", "repos/acme/widgets", {"homepage": LINK})
    gh = GitHub("acme/widgets", token="t", run=fake, cache_dir=tmp_path / "cache")
    out = publish(gh, LINK, host="mac.local")
    assert out == {"issue": 10, "homepage": LINK, "issue_updated": True}
    patches = [c for c in fake.calls if c.method == "PATCH"]
    assert [c.endpoint for c in patches] == [R + "issues/10", "repos/acme/widgets"]
    assert "## Dashboard" in patches[0].body["body"] and LINK in patches[0].body["body"] and patches[1].body == {"homepage": LINK}
    fake.route("GET", R + "issues/10", {"number": 10, "body": patches[0].body["body"]})
    assert publish(gh, LINK, host="mac.local")["issue_updated"] is False   # the same link again rewrites nothing
    assert [c.endpoint for c in fake.calls if c.method == "PATCH"][-1] == "repos/acme/widgets"


def test_try_publish_turns_a_failure_into_a_note(tmp_path):
    fake = FakeGh()   # no milestones routed: the board cannot be located
    gh = GitHub("acme/widgets", token="t", run=fake, cache_dir=tmp_path / "cache")
    note = try_publish(gh, LINK, host="h")
    assert note is not None and note.startswith("could not put the dashboard link on GitHub")
