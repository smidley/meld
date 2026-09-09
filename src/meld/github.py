"""Thin wrapper over `gh api`. The only module that talks to GitHub."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

from meld.atomic import write_atomic

RunFn = Callable[..., subprocess.CompletedProcess]


class GitHubError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimited(GitHubError):
    def __init__(self, reset_at: int | None):
        super().__init__(f"GitHub rate limit exceeded (resets at {reset_at})", status=None)
        self.reset_at = reset_at


class GitHub:
    def __init__(self, repo: str, token: str | None = None, run: RunFn = subprocess.run, cache_dir: Path | None = None,
                 token_provider: Callable[[bool], str] | None = None):
        self.repo = repo
        self._token = token
        self._provider = token_provider
        self._run = run
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def token(self) -> str | None:
        """The credential every call carries: the provider's current installation token for an App group, else the static PAT."""
        return self._provider(False) if self._provider else self._token

    # ---- low level -------------------------------------------------------

    def _cache_path(self, endpoint: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / (hashlib.sha1(endpoint.encode()).hexdigest() + ".json")

    def _request(self, cmd: list[str], body: dict | None, token: str | None) -> tuple[int, dict[str, str], Any]:
        env = dict(os.environ)
        if token:
            env["GH_TOKEN"] = token
        proc = self._run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, env=env)
        raw = (proc.stdout or "").replace("\r\n", "\n")
        head, _, text = raw.partition("\n\n")
        lines = head.split("\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError) as e:
            raise GitHubError(f"could not parse gh response for {' '.join(cmd)}: {proc.stderr.strip() or raw[:200]}", status=None) from e
        resp_headers = {}
        for line in lines[1:]:
            k, _, v = line.partition(": ")
            if k:
                resp_headers[k.lower()] = v
        if status in (403, 429) and resp_headers.get("x-ratelimit-remaining") == "0":
            reset = resp_headers.get("x-ratelimit-reset")
            raise RateLimited(int(reset) if reset else None)
        parsed: Any = None
        if text.strip():
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        return status, resp_headers, parsed

    def _call(self, method: str, endpoint: str, body: dict | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        cmd = ["gh", "api", "--include", "-X", method]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        if body is not None:
            cmd += ["--input", "-"]
        cmd.append(endpoint)
        status, resp_headers, parsed = self._request(cmd, body, self.token)
        if status == 401 and self._provider is not None:
            # An installation token expired between the provider's freshness check and the call: refresh once and retry.
            status, resp_headers, parsed = self._request(cmd, body, self._provider(True))
        return status, resp_headers, parsed

    def get(self, endpoint: str, params: dict[str, Any] | None = None, *, use_cache: bool = True) -> Any:
        url = endpoint + ("?" + urlencode(params) if params else "")
        cache = self._cache_path(url)
        headers: dict[str, str] = {}
        cached: dict | None = None
        if use_cache and cache is not None and cache.exists():
            # A corrupt or truncated cache entry is treated as no cache at all: send no
            # If-None-Match, fetch normally, and overwrite the bad file on success.
            try:
                entry = json.loads(cache.read_text())
                etag, body_cached = entry["etag"], entry["body"]
            except (json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError, TypeError):
                cached = None
            else:
                cached = {"etag": etag, "body": body_cached}
                headers["If-None-Match"] = etag
        status, resp_headers, body = self._call("GET", url, headers=headers)
        if status == 304 and cached is not None:
            return cached["body"]
        if status >= 400:
            raise GitHubError(f"GET {url} failed with HTTP {status}: {body}", status=status)
        if cache is not None and "etag" in resp_headers:
            write_atomic(cache, json.dumps({"etag": resp_headers["etag"], "body": body}))
        return body

    def get_paginated(self, endpoint: str, params: dict[str, Any] | None = None) -> list:
        items: list = []
        page = 1
        seen_first: Any = object()
        while True:
            q = dict(params or {})
            q.update({"per_page": 100, "page": page})
            batch = self.get(endpoint, q)
            if not isinstance(batch, list) or not batch:
                break
            if batch[0] == seen_first:
                break
            seen_first = batch[0]
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    def _mutate(self, method: str, endpoint: str, body: dict | None) -> Any:
        status, _, resp = self._call(method, endpoint, body=body)
        if status >= 400:
            raise GitHubError(f"{method} {endpoint} failed with HTTP {status}: {resp}", status=status)
        return resp

    def post(self, endpoint: str, body: dict) -> Any:
        return self._mutate("POST", endpoint, body)

    def patch(self, endpoint: str, body: dict) -> Any:
        return self._mutate("PATCH", endpoint, body)

    def delete(self, endpoint: str, body: dict | None = None) -> Any:
        return self._mutate("DELETE", endpoint, body)

    def put(self, endpoint: str, body: dict) -> Any:
        return self._mutate("PUT", endpoint, body)

    # ---- helpers ---------------------------------------------------------

    def _r(self, path: str) -> str:
        return f"repos/{self.repo}/{path}"

    def viewer_login(self) -> str:
        return str(self.get("user")["login"])

    def list_open_milestones(self) -> list[dict]:
        return self.get_paginated(self._r("milestones"), {"state": "open"})

    def list_milestones(self, state: str = "all") -> list[dict]:
        return self.get_paginated(self._r("milestones"), {"state": state})

    def list_issues(self, milestone: int) -> list[dict]:
        raw = self.get_paginated(self._r("issues"), {"milestone": milestone, "state": "all"})
        return [i for i in raw if "pull_request" not in i]

    def get_issue(self, number: int, fresh: bool = False) -> dict:
        return self.get(self._r(f"issues/{number}"), use_cache=not fresh)

    def list_comments(self, issue: int, since: str | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if since:
            params["since"] = since
        return self.get_paginated(self._r(f"issues/{issue}/comments"), params)

    def list_pulls(self, state: str = "open") -> list[dict]:
        return self.get_paginated(self._r("pulls"), {"state": state})

    def pull_reviews(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"pulls/{number}/reviews"))

    def pull_commits(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"pulls/{number}/commits"))

    def check_runs(self, sha: str) -> dict:
        return self.get(self._r(f"commits/{sha}/check-runs"))

    def combined_status(self, sha: str) -> dict[str, str]:
        """Commit statuses on `sha` as {context: state}; GitHub already reports only the latest status per context.
        The page is widened to 100 so a repo with many CI contexts cannot push a `meld/review/*` context off it."""
        resp = self.get(self._r(f"commits/{sha}/status"), {"per_page": 100})
        return {s["context"]: s["state"] for s in (resp or {}).get("statuses", [])}

    def add_assignees(self, number: int, logins: list[str]) -> dict:
        return self.post(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def remove_assignees(self, number: int, logins: list[str]) -> dict:
        return self.delete(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def add_labels(self, number: int, labels: list[str]) -> list:
        return self.post(self._r(f"issues/{number}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        status, _, resp = self._call("DELETE", self._r(f"issues/{number}/labels/{quote(label, safe='')}"))
        if status >= 400 and status != 404:
            raise GitHubError(f"DELETE label {label} on #{number} failed with HTTP {status}: {resp}", status=status)

    def create_comment(self, number: int, body: str) -> dict:
        return self.post(self._r(f"issues/{number}/comments"), {"body": body})

    def get_repo(self) -> dict:
        return self.get(f"repos/{self.repo}", use_cache=False)

    def create_milestone(self, title: str, description: str = "") -> dict:
        return self.post(self._r("milestones"), {"title": title, "description": description})

    def get_milestone(self, number: int) -> dict:
        return self.get(self._r(f"milestones/{number}"))

    def update_comment(self, comment_id: int, body: str) -> dict:
        return self.patch(self._r(f"issues/comments/{comment_id}"), {"body": body})

    def create_pull(self, title: str, head: str, base: str, body: str) -> dict:
        return self.post(self._r("pulls"), {"title": title, "head": head, "base": base, "body": body})

    def update_pull(self, number: int, body: str) -> dict:
        return self.patch(self._r(f"pulls/{number}"), {"body": body})

    def get_pull(self, number: int, fresh: bool = False) -> dict:
        return self.get(self._r(f"pulls/{number}"), use_cache=not fresh)

    def pull_files(self, number: int) -> list[str]:
        return [f["filename"] for f in self.get_paginated(self._r(f"pulls/{number}/files"))]

    def create_review(self, number: int, event: str, body: str, commit_id: str, comments: list[dict] | None = None) -> dict:
        """Submit one PR review. `comments` are diff comments ({path, line, body}); GitHub rejects lines outside the diff with 422."""
        payload: dict[str, Any] = {"event": event, "body": body, "commit_id": commit_id}
        if comments:
            payload["comments"] = [{"path": c["path"], "line": int(c["line"]), "side": "RIGHT", "body": c["body"]} for c in comments]
        return self.post(self._r(f"pulls/{number}/reviews"), payload)

    def merge_pull(self, number: int, sha: str, title: str, method: str = "squash") -> dict:
        """Squash-merge; `sha` makes GitHub refuse (409) if the head moved since we looked."""
        return self.put(self._r(f"pulls/{number}/merge"), {"sha": sha, "merge_method": method, "commit_title": title})

    def create_status(self, sha: str, state: str, context: str, description: str, target_url: str | None = None) -> dict:
        body: dict[str, Any] = {"state": state, "context": context, "description": description[:140]}
        if target_url:
            body["target_url"] = target_url
        return self.post(self._r(f"statuses/{sha}"), body)

    def create_check_run(self, name: str, head_sha: str, conclusion: str, title: str, summary: str) -> dict:
        """Requires App authentication (GitHub refuses check runs from personal tokens)."""
        return self.post(self._r("check-runs"), {"name": name, "head_sha": head_sha, "status": "completed", "conclusion": conclusion,
                                                 "output": {"title": title[:255], "summary": summary[:65535]}})

    def update_issue(self, number: int, **fields: Any) -> dict:
        return self.patch(self._r(f"issues/{number}"), fields)

    def create_issue(self, title: str, body: str, labels: list[str], milestone: int) -> dict:
        return self.post(self._r("issues"), {"title": title, "body": body, "labels": labels, "milestone": milestone})

    def update_milestone(self, number: int, **fields: Any) -> dict:
        return self.patch(self._r(f"milestones/{number}"), fields)

    def get_contents(self, path: str, ref: str) -> str | None:
        """The decoded text of a file at `ref`, or None when GitHub has no such file."""
        quoted = "/".join(quote(seg, safe="") for seg in path.split("/"))
        status, _, resp = self._call("GET", self._r(f"contents/{quoted}") + "?" + urlencode({"ref": ref}))
        if status == 404:
            return None
        if status >= 400:
            raise GitHubError(f"GET contents/{path}@{ref} failed with HTTP {status}: {resp}", status)
        if isinstance(resp, dict) and resp.get("encoding") == "base64":
            return base64.b64decode(resp.get("content", "")).decode("utf-8")
        raise GitHubError(f"GET contents/{path}@{ref}: unexpected payload {type(resp).__name__}")

    def issue_events(self, number: int) -> list[dict]:
        return self.get_paginated(self._r(f"issues/{number}/events"))

    def list_labels(self) -> list[dict]:
        return self.get_paginated(self._r("labels"))

    def create_label(self, name: str, color: str, description: str = "") -> dict:
        return self.post(self._r("labels"), {"name": name, "color": color.lstrip("#"), "description": description})

    def search_issues(self, query: str) -> list[dict]:
        resp = self.get("search/issues", {"q": query}, use_cache=False)
        items = resp.get("items", []) if isinstance(resp, dict) else []
        return list(items)
