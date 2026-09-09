import json
import os

import pytest

from meld.appauth import AppAuthError
from meld.github import GitHub, GitHubError, RateLimited
from tests.fakegh import FakeGh


def make(tmp_path, token="tok"):
    fake = FakeGh()
    gh = GitHub("acme/widgets", token=token, run=fake, cache_dir=tmp_path / "cache")
    return fake, gh


def test_get_sets_token_and_parses_json(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "title": "x"})
    assert gh.get_issue(7) == {"number": 7, "title": "x"}
    assert fake.calls[0].method == "GET"
    assert fake.envs[0]["GH_TOKEN"] == "tok"
    assert "tok" not in fake.calls[0].endpoint
    assert "tok" not in json.dumps(fake.calls[0].headers)


def test_etag_cache_returns_cached_body_on_304(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7}, etag='W/"abc"')
    assert gh.get_issue(7) == {"number": 7}
    assert gh.get_issue(7) == {"number": 7}
    assert fake.calls[1].headers["if-none-match"] == 'W/"abc"'


def test_paginated_follows_pages_until_short_page(tmp_path):
    fake, gh = make(tmp_path)
    page = [{"number": i} for i in range(100)]
    fake.route("GET", "repos/acme/widgets/pulls", page)
    # FakeGh serves the same body for every page; a full page followed by another full page
    # would loop forever, so the implementation must stop when a page repeats its first item.
    items = gh.get_paginated("repos/acme/widgets/pulls", {"state": "open"})
    assert len(items) == 100
    assert fake.calls[0].query["per_page"] == "100"
    assert fake.calls[0].query["page"] == "1"


def test_list_issues_filters_out_pull_requests(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues", [{"number": 1}, {"number": 2, "pull_request": {"url": "u"}}])
    assert gh.list_issues(3) == [{"number": 1}]
    assert fake.calls[0].query == {"milestone": "3", "state": "all", "per_page": "100", "page": "1"}


def test_list_comments_passes_since(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/10/comments", [])
    gh.list_comments(10, since="2026-09-03T00:00:00Z")
    assert fake.calls[0].query["since"] == "2026-09-03T00:00:00Z"


def test_mutations_send_json_bodies(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/issues/7/assignees", {"assignees": [{"login": "b"}]})
    fake.route("DELETE", "repos/acme/widgets/issues/7/assignees", {"assignees": []})
    fake.route("POST", "repos/acme/widgets/issues/7/labels", [{"name": "meld:ready"}])
    fake.route("POST", "repos/acme/widgets/issues/7/comments", {"id": 99, "html_url": "u"})
    gh.add_assignees(7, ["b"])
    gh.remove_assignees(7, ["b"])
    gh.add_labels(7, ["meld:ready"])
    assert gh.create_comment(7, "hi")["id"] == 99
    assert fake.calls[0].body == {"assignees": ["b"]}
    assert fake.calls[1].method == "DELETE"
    assert fake.calls[2].body == {"labels": ["meld:ready"]}
    assert fake.calls[3].body == {"body": "hi"}


def test_remove_label_ignores_404(tmp_path):
    fake, gh = make(tmp_path)
    gh.remove_label(7, "meld:ready")  # no route -> 404 -> swallowed


def test_http_error_raises(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"message": "boom"}, status=500)
    with pytest.raises(GitHubError, match="500"):
        gh.get_issue(7)


def test_rate_limit_raises_rate_limited(tmp_path):
    fake, gh = make(tmp_path)
    fake.rate_limit_next = True
    with pytest.raises(RateLimited) as ei:
        gh.get_issue(7)
    assert ei.value.reset_at == 1700000000


def test_viewer_login(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "user", {"login": "empowerScott"})
    assert gh.viewer_login() == "empowerScott"


def test_no_token_leaves_gh_auth_alone(tmp_path):
    fake = FakeGh()
    gh = GitHub("acme/widgets", token=None, run=fake, cache_dir=tmp_path / "cache")
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7})
    gh.get_issue(7)
    gh_token_in_os = "GH_TOKEN" in os.environ
    gh_token_in_env = "GH_TOKEN" in fake.envs[0]
    # If GH_TOKEN is in os.environ, it should pass through; otherwise it should not be set
    if gh_token_in_os:
        assert gh_token_in_env
    else:
        assert not gh_token_in_env


def test_get_issue_fresh_bypasses_etag_cache(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "assignees": []}, etag='W/"v1"')
    assert gh.get_issue(7)["assignees"] == []
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "assignees": [{"login": "b"}]}, etag='W/"v1"')
    assert gh.get_issue(7)["assignees"] == []            # cached 304 path still answers stale
    assert gh.get_issue(7, fresh=True)["assignees"] == [{"login": "b"}]
    assert "if-none-match" not in fake.calls[-1].headers


def test_corrupt_cache_entry_is_ignored_and_rewritten(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "title": "first"}, etag='W/"v1"')
    assert gh.get_issue(7)["title"] == "first"
    cache_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text('{"etag": "W/\\"v1\\"", "bo')  # truncated mid-write
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "title": "second"}, etag='W/"v2"')
    assert gh.get_issue(7)["title"] == "second"
    assert "if-none-match" not in fake.calls[-1].headers
    assert json.loads(cache_files[0].read_text()) == {"etag": 'W/"v2"', "body": {"number": 7, "title": "second"}}


def test_cache_entry_missing_a_key_is_ignored(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7}, etag='W/"v1"')
    gh.get_issue(7)
    cache_file = next((tmp_path / "cache").glob("*.json"))
    cache_file.write_text(json.dumps({"etag": 'W/"v1"'}))  # no "body"
    assert gh.get_issue(7) == {"number": 7}
    assert "if-none-match" not in fake.calls[-1].headers


def test_cache_is_written_through_write_atomic(tmp_path, monkeypatch):
    import meld.github as gh_mod
    from meld.atomic import write_atomic as real_write_atomic
    seen = []

    def spy(path, text):
        seen.append(path)
        real_write_atomic(path, text)

    monkeypatch.setattr(gh_mod, "write_atomic", spy)
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7}, etag='W/"v1"')
    gh.get_issue(7)
    assert seen == [next((tmp_path / "cache").glob("*.json"))]
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_remove_label_quotes_the_label_path_segment(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("DELETE", "repos/acme/widgets/issues/7/labels/needs%20triage%3A%20now", [])
    gh.remove_label(7, "needs triage: now")
    assert fake.calls[-1].endpoint == "repos/acme/widgets/issues/7/labels/needs%20triage%3A%20now"


def test_update_comment_and_get_milestone(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("PATCH", "repos/acme/widgets/issues/comments/110", {"id": 110})
    fake.route("GET", "repos/acme/widgets/milestones/3", {"number": 3, "description": "plan"})
    assert gh.update_comment(110, "new body")["id"] == 110
    assert fake.calls[0].method == "PATCH" and fake.calls[0].body == {"body": "new body"}
    assert gh.get_milestone(3)["description"] == "plan"


def test_create_and_update_pull(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("POST", "repos/acme/widgets/pulls", {"number": 21, "html_url": "u"})
    fake.route("PATCH", "repos/acme/widgets/pulls/21", {"number": 21})
    assert gh.create_pull("t", "meld/11-x", "main", "Closes #11")["number"] == 21
    assert fake.calls[0].body == {"title": "t", "head": "meld/11-x", "base": "main", "body": "Closes #11"}
    gh.update_pull(21, "new body")
    assert fake.calls[1].method == "PATCH" and fake.calls[1].body == {"body": "new body"}


def test_cache_truncated_mid_character_is_ignored(tmp_path):
    fake, gh = make(tmp_path)
    fake.route("GET", "repos/acme/widgets/issues/7", {"number": 7, "title": "café"}, etag='W/"v1"')
    gh.get_issue(7)
    cache_file = next((tmp_path / "cache").glob("*.json"))
    # A partial write can cut a multibyte character in half. read_text then raises
    # UnicodeDecodeError, not JSONDecodeError, and it must still count as "no cache".
    # (json.dumps escapes non-ASCII by default, so the bytes are written explicitly.)
    entry = '{"etag": "W/\\"v1\\"", "body": {"number": 7, "title": "caf\u00e9"}}'.encode()
    cache_file.write_bytes(entry[:-4])
    with pytest.raises(UnicodeDecodeError):
        cache_file.read_text()
    assert gh.get_issue(7) == {"number": 7, "title": "café"}
    assert "if-none-match" not in fake.calls[-1].headers


def test_review_merge_status_and_issue_endpoints(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/pulls/20/files", [{"filename": "ci.yml"}, {"filename": ".github/workflows/ci.yml"}])
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"id": 501, "state": "APPROVED"})
    fake.route("PUT", "repos/acme/widgets/pulls/20/merge", {"sha": "m123", "merged": True})
    fake.route("POST", "repos/acme/widgets/statuses/abc123", {"id": 77})
    fake.route("PATCH", "repos/acme/widgets/issues/13", {"number": 13, "state": "open"})
    fake.route("GET", "repos/acme/widgets/pulls/20", {"number": 20, "merge_commit_sha": "mmm"})
    assert gh.pull_files(20) == ["ci.yml", ".github/workflows/ci.yml"]
    assert gh.create_review(20, "APPROVE", "looks right\n\nverified-by: pytest", "abc123",
                            [{"path": "README.md", "line": 1, "body": "nit"}])["id"] == 501
    assert gh.merge_pull(20, "abc123", "CI pipeline")["sha"] == "m123"
    assert gh.create_status("abc123", "success", "meld/review/claude", "claude review: approve")["id"] == 77
    assert gh.update_issue(13, state="open")["state"] == "open"
    assert gh.get_pull(20)["merge_commit_sha"] == "mmm"
    bodies = {(c.method, c.endpoint): c.body for c in fake.calls if c.method != "GET"}
    assert bodies[("POST", "repos/acme/widgets/pulls/20/reviews")] == {
        "event": "APPROVE", "body": "looks right\n\nverified-by: pytest", "commit_id": "abc123",
        "comments": [{"path": "README.md", "line": 1, "side": "RIGHT", "body": "nit"}]}
    assert bodies[("PUT", "repos/acme/widgets/pulls/20/merge")] == {"sha": "abc123", "merge_method": "squash", "commit_title": "CI pipeline"}
    assert bodies[("POST", "repos/acme/widgets/statuses/abc123")] == {"state": "success", "context": "meld/review/claude",
                                                                      "description": "claude review: approve"}
    assert bodies[("PATCH", "repos/acme/widgets/issues/13")] == {"state": "open"}


def test_combined_status_maps_context_to_state(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/commits/abc123/status",
               {"state": "failure", "sha": "abc123",
                "statuses": [{"context": "meld/review/claude", "state": "success", "description": "claude review: approve"},
                             {"context": "ci/lint", "state": "failure"}]})
    assert gh.combined_status("abc123") == {"meld/review/claude": "success", "ci/lint": "failure"}
    fake.route("GET", "repos/acme/widgets/commits/def456/status", {"state": "pending", "statuses": []})
    assert gh.combined_status("def456") == {}
    assert [c.endpoint for c in fake.calls if c.method == "GET"][-2:] == ["repos/acme/widgets/commits/abc123/status",
                                                                         "repos/acme/widgets/commits/def456/status"]
    assert fake.calls[-1].query == {"per_page": "100"}   # many CI contexts must not push a meld/review/* context off the page


def test_create_review_without_comments_omits_the_key(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"id": 502})
    gh.create_review(20, "REQUEST_CHANGES", "please fix", "abc123")
    body = [c for c in fake.calls if c.method == "POST"][-1].body
    assert "comments" not in body and body["event"] == "REQUEST_CHANGES"


def test_github_error_carries_the_http_status(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/pulls/20/reviews", {"message": "Validation Failed"}, status=422)
    with pytest.raises(GitHubError) as ei:
        gh.create_review(20, "APPROVE", "x", "abc123")
    assert ei.value.status == 422


import base64


def test_issue_milestone_contents_events_labels_and_search_endpoints(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/issues", {"number": 31, "html_url": "https://github.com/acme/widgets/issues/31"})
    fake.route("PATCH", "repos/acme/widgets/milestones/3", {"number": 3, "description": "plan text"})
    fake.route("GET", "repos/acme/widgets/contents/.meld/decisions/0001-x.md",
               {"content": base64.b64encode("# Decision x\n".encode()).decode(), "encoding": "base64"})
    fake.route("GET", "repos/acme/widgets/contents/.meld/missing.md", {"message": "Not Found"}, status=404)
    fake.route("GET", "repos/acme/widgets/issues/13/events", [{"id": 9001, "event": "labeled", "actor": {"login": "randouser"}, "label": {"name": "meld:ready"}}])
    fake.route("GET", "repos/acme/widgets/labels", [{"name": "meld:ready", "color": "0e8a16"}])
    fake.route("POST", "repos/acme/widgets/labels", {"name": "meld:planned", "color": "cccccc"})
    fake.route("GET", "search/issues", {"total_count": 1, "items": [{"number": 10, "title": "Coordination: M1", "html_url": "u10"}]})
    assert gh.create_issue("Login form", "body", ["meld:planned", "worker:frontend"], 3)["number"] == 31
    assert gh.update_milestone(3, description="plan text")["description"] == "plan text"
    assert gh.get_contents(".meld/decisions/0001-x.md", "abc123") == "# Decision x\n"
    assert gh.get_contents(".meld/missing.md", "abc123") is None
    assert gh.issue_events(13)[0]["actor"]["login"] == "randouser"
    assert gh.list_labels()[0]["name"] == "meld:ready"
    assert gh.create_label("meld:planned", "cccccc", "Created by the plan, not yet approved")["name"] == "meld:planned"
    assert gh.search_issues("repo:acme/widgets label:meld:coordination oauth")[0]["number"] == 10
    bodies = {(c.method, c.endpoint): c.body for c in fake.calls if c.method != "GET"}
    assert bodies[("POST", "repos/acme/widgets/issues")] == {"title": "Login form", "body": "body", "labels": ["meld:planned", "worker:frontend"], "milestone": 3}
    assert bodies[("PATCH", "repos/acme/widgets/milestones/3")] == {"description": "plan text"}
    assert bodies[("POST", "repos/acme/widgets/labels")] == {"name": "meld:planned", "color": "cccccc", "description": "Created by the plan, not yet approved"}
    contents_call = [c for c in fake.calls if c.endpoint == "repos/acme/widgets/contents/.meld/decisions/0001-x.md"][0]
    assert contents_call.query.get("ref") == "abc123"
    search_call = [c for c in fake.calls if c.endpoint == "search/issues"][0]
    assert search_call.query.get("q") == "repo:acme/widgets label:meld:coordination oauth"


def test_get_contents_quotes_path_segments(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/contents/.meld/decisions/0001-fix-%2342.md",
               {"content": base64.b64encode("# Fix\n".encode()).decode(), "encoding": "base64"})
    assert gh.get_contents(".meld/decisions/0001-fix-#42.md", "abc123") == "# Fix\n"
    call = [c for c in fake.calls if c.endpoint == "repos/acme/widgets/contents/.meld/decisions/0001-fix-%2342.md"][0]
    assert call.query.get("ref") == "abc123"


def test_search_issues_guards_payload(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "search/issues", "not a dict")
    assert gh.search_issues("query") == []
    fake.route("GET", "search/issues", None)
    assert gh.search_issues("query") == []


def test_create_label_normalises_color(board1_gh):
    fake, gh = board1_gh
    fake.route("POST", "repos/acme/widgets/labels", {"name": "meld:planned", "color": "cccccc"})
    result = gh.create_label("meld:planned", "#cccccc")
    assert result["color"] == "cccccc"
    body = [c for c in fake.calls if c.method == "POST" and "labels" in c.endpoint][-1].body
    assert body == {"name": "meld:planned", "color": "cccccc", "description": ""}


def test_get_contents_falls_back_to_the_blob_endpoint_for_large_files(board1_gh):
    import base64
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/contents/.meld/memory/lessons.md", {"encoding": "none", "sha": "blob1", "size": 2_000_000}, query={"ref": "main"})
    fake.route("GET", "repos/acme/widgets/git/blobs/blob1", {"encoding": "base64", "content": base64.b64encode(b"# Lessons\n").decode()})
    assert gh.get_contents(".meld/memory/lessons.md", "main") == "# Lessons\n"


def test_get_contents_raises_when_the_blob_has_no_content(board1_gh):
    fake, gh = board1_gh
    fake.route("GET", "repos/acme/widgets/contents/.meld/memory/lessons.md", {"encoding": "none", "sha": "blob2", "size": 2_000_000}, query={"ref": "main"})
    fake.route("GET", "repos/acme/widgets/git/blobs/blob2", {"encoding": "base64"})
    with pytest.raises(GitHubError, match="blob has no content"):
        gh.get_contents(".meld/memory/lessons.md", "main")


def test_github_uses_the_token_provider_and_refreshes_once_on_401(tmp_path):
    fake = FakeGh()
    fake.route("GET", "repos/acme/widgets/issues/1", {"number": 1})
    asked = []

    def provider(force=False):
        # Stateful stand-in for AppAuth.token: returns the cached installation token until forced,
        # then the refreshed one -- enough to exercise GitHub's retry-once-on-401 path.
        asked.append(force)
        return "ghs_new" if force else "ghs_old"

    gh = GitHub("acme/widgets", run=fake, token_provider=provider)
    assert gh.token == "ghs_old"                       # first ask, not forced
    fake.route("GET", "repos/acme/widgets/issues/2", {"message": "Bad credentials"}, status=401)
    with pytest.raises(GitHubError, match="401"):
        gh.get("repos/acme/widgets/issues/2", use_cache=False)
    assert True in asked                               # a forced refresh was requested after the 401
    assert fake.envs[-1]["GH_TOKEN"] == "ghs_new"      # and the retry carried the refreshed token


def test_a_token_provider_failure_surfaces_as_a_github_error(tmp_path):
    """Final-review Minor 13: `review` and `housekeeping` contain GitHub trouble by catching GitHubError. A mint that
    fails at refresh time must arrive as one of those, not as an AppAuthError that escapes every containment site."""
    fake = FakeGh()
    fake.route("GET", "repos/acme/widgets/issues/1", {"number": 1})

    def dead(force=False):
        raise AppAuthError("installation token request failed with HTTP 401: Bad credentials")

    gh = GitHub("acme/widgets", run=fake, token_provider=dead)
    with pytest.raises(GitHubError, match="Bad credentials"):
        _ = gh.token
    with pytest.raises(GitHubError, match="Bad credentials"):
        gh.get("repos/acme/widgets/issues/1", use_cache=False)
    with pytest.raises(GitHubError):
        gh.post("repos/acme/widgets/issues/1/comments", {"body": "x"})


def test_a_provider_that_fails_only_on_the_401_refresh_also_surfaces_as_a_github_error(tmp_path):
    fake = FakeGh()
    fake.route("GET", "repos/acme/widgets/issues/2", {"message": "Bad credentials"}, status=401)

    def flaky(force=False):
        if force:
            raise AppAuthError("could not reach api.github.com")
        return "ghs_old"

    gh = GitHub("acme/widgets", run=fake, token_provider=flaky)
    with pytest.raises(GitHubError, match="could not reach"):
        gh.get("repos/acme/widgets/issues/2", use_cache=False)
