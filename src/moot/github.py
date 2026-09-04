"""Thin wrapper over `gh api`. The only module that talks to GitHub."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

from moot.atomic import write_atomic

RunFn = Callable[..., subprocess.CompletedProcess]


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset_at: int | None):
        super().__init__(f"GitHub rate limit exceeded (resets at {reset_at})")
        self.reset_at = reset_at


class GitHub:
    def __init__(self, repo: str, token: str | None = None, run: RunFn = subprocess.run, cache_dir: Path | None = None):
        self.repo = repo
        self.token = token
        self._run = run
        self._cache_dir = cache_dir
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- low level -------------------------------------------------------

    def _cache_path(self, endpoint: str) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / (hashlib.sha1(endpoint.encode()).hexdigest() + ".json")

    def _call(self, method: str, endpoint: str, body: dict | None = None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        cmd = ["gh", "api", "--include", "-X", method]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        if body is not None:
            cmd += ["--input", "-"]
        cmd.append(endpoint)
        env = dict(os.environ)
        if self.token:
            env["GH_TOKEN"] = self.token
        proc = self._run(cmd, input=json.dumps(body) if body is not None else None, capture_output=True, text=True, env=env)
        raw = (proc.stdout or "").replace("\r\n", "\n")
        head, _, text = raw.partition("\n\n")
        lines = head.split("\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError) as e:
            raise GitHubError(f"could not parse gh response for {method} {endpoint}: {proc.stderr.strip() or raw[:200]}") from e
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
            raise GitHubError(f"GET {url} failed with HTTP {status}: {body}")
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
            raise GitHubError(f"{method} {endpoint} failed with HTTP {status}: {resp}")
        return resp

    def post(self, endpoint: str, body: dict) -> Any:
        return self._mutate("POST", endpoint, body)

    def patch(self, endpoint: str, body: dict) -> Any:
        return self._mutate("PATCH", endpoint, body)

    def delete(self, endpoint: str, body: dict | None = None) -> Any:
        return self._mutate("DELETE", endpoint, body)

    # ---- helpers ---------------------------------------------------------

    def _r(self, path: str) -> str:
        return f"repos/{self.repo}/{path}"

    def viewer_login(self) -> str:
        return str(self.get("user")["login"])

    def list_open_milestones(self) -> list[dict]:
        return self.get_paginated(self._r("milestones"), {"state": "open"})

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

    def add_assignees(self, number: int, logins: list[str]) -> dict:
        return self.post(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def remove_assignees(self, number: int, logins: list[str]) -> dict:
        return self.delete(self._r(f"issues/{number}/assignees"), {"assignees": logins})

    def add_labels(self, number: int, labels: list[str]) -> list:
        return self.post(self._r(f"issues/{number}/labels"), {"labels": labels})

    def remove_label(self, number: int, label: str) -> None:
        status, _, resp = self._call("DELETE", self._r(f"issues/{number}/labels/{quote(label, safe='')}"))
        if status >= 400 and status != 404:
            raise GitHubError(f"DELETE label {label} on #{number} failed with HTTP {status}: {resp}")

    def create_comment(self, number: int, body: str) -> dict:
        return self.post(self._r(f"issues/{number}/comments"), {"body": body})
