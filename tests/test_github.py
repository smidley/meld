import json
import os

import pytest

from moot.github import GitHub, GitHubError, RateLimited
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
    fake.route("POST", "repos/acme/widgets/issues/7/labels", [{"name": "moot:ready"}])
    fake.route("POST", "repos/acme/widgets/issues/7/comments", {"id": 99, "html_url": "u"})
    gh.add_assignees(7, ["b"])
    gh.remove_assignees(7, ["b"])
    gh.add_labels(7, ["moot:ready"])
    assert gh.create_comment(7, "hi")["id"] == 99
    assert fake.calls[0].body == {"assignees": ["b"]}
    assert fake.calls[1].method == "DELETE"
    assert fake.calls[2].body == {"labels": ["moot:ready"]}
    assert fake.calls[3].body == {"body": "hi"}


def test_remove_label_ignores_404(tmp_path):
    fake, gh = make(tmp_path)
    gh.remove_label(7, "moot:ready")  # no route -> 404 -> swallowed


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
