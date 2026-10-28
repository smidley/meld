"""A stateful stand-in for GitHub, for the dress rehearsal (plan 48).

One repository, served over loopback HTTP, backed by a bare git repository on disk so that pull requests, files,
commits and merges are the real thing: a PR's head is the branch in the bare repo, its files are a real diff, and
`PUT pulls/N/merge` performs a real squash onto the default branch. Everything else (labels, milestones, issues,
comments, events, reviews, statuses, check runs, Apps, installations, tokens) is kept in memory in the shapes meld
reads. Identity is the token: the person's token, a PAT per bot, or an installation token minted for an App.

Nothing here decides anything about meld; the rehearsal exists so the real wizard, viewer, supervisors, roles and
workers can run end to end on one machine with no network and no money. Spec section 19.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

CLOSES_RE = re.compile(r"(?i)\b(?:closes|fixes|resolves)\s+#(\d+)")
Handler = Callable[[dict, dict, dict], tuple[int, Any]]   # (path params, query, body) -> (status, json)


def _iso(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    def __init__(self, repo: str, bare: Path, *, human: str, human_token: str, free_plan: bool = True,
                 clock: Callable[[], datetime] | None = None):
        self.repo = repo
        self.owner, self.name = repo.split("/", 1)
        self.bare = bare                              # the repository's git data; created when the repo is
        self.work = bare.parent / (bare.name + ".work")   # a scratch clone for merges
        self.human = human
        self.free_plan = free_plan
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.tokens: dict[str, str] = {human_token: human}
        self.users: dict[str, dict] = {human: {"login": human, "id": 1, "type": "User"}}
        self.exists = False
        self.labels: dict[str, dict] = {}
        self.milestones: dict[int, dict] = {}
        self.items: dict[int, dict] = {}             # issues and pull requests share one numbering, as on GitHub
        self.comments: dict[int, dict] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.statuses: dict[str, list[dict]] = {}
        self.check_runs: dict[str, list[dict]] = {}
        self.apps: dict[int, dict] = {}
        self.installations: dict[int, int] = {}      # installation id -> app id
        self.manifests: dict[str, dict] = {}         # one-time code -> manifest
        self.calls: list[tuple[str, str, str | None]] = []
        self.lock = threading.RLock()
        self._number = 0
        self._comment_id = 1000
        self._review_id = 1
        self._event_id = 1
        self._user_id = 100
        self._app_id = 5000
        self._installation_id = 9000
        self._server: ThreadingHTTPServer | None = None
        self.url = ""

    # ---- serving -----------------------------------------------------------------------------------------

    def start(self) -> str:
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _do(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                status, payload = fake.handle(self.command, self.path, dict(self.headers), raw)
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = _do

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    # ---- identity and Apps -------------------------------------------------------------------------------

    def add_bot(self, login: str, token: str) -> dict:
        """A machine account with a PAT (the PAT form of a group)."""
        self._user_id += 1
        self.users[login] = {"login": login, "id": self._user_id, "type": "User"}
        self.tokens[token] = login
        return self.users[login]

    def manifest_code(self, manifest: dict) -> str:
        """What GitHub's App-creation form does: takes the manifest and redirects back with a one-time code."""
        code = secrets.token_hex(8)
        self.manifests[code] = manifest
        return code

    def install(self, app_id: int) -> int:
        """Install an App on the repository, as a person does with the second click."""
        self._installation_id += 1
        self.installations[self._installation_id] = app_id
        self.apps[app_id]["installation_id"] = self._installation_id
        return self._installation_id

    def login_for(self, headers: dict) -> str | None:
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        if auth.lower().startswith("token "):
            return self.tokens.get(auth[6:].strip())
        if auth.lower().startswith("bearer "):
            return self.tokens.get(auth[7:].strip())
        return None

    def app_for_jwt(self, headers: dict) -> dict | None:
        auth = headers.get("Authorization") or headers.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            return None
        try:
            payload = auth[7:].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return self.apps.get(int(claims.get("iss", 0)))
        except (ValueError, IndexError, TypeError):
            return None

    # ---- git -----------------------------------------------------------------------------------------------

    def git(self, args: list[str], cwd: Path | None = None, check: bool = True) -> str:
        env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
               "HOME": str(self.bare.parent)}
        proc = subprocess.run(["git", *args], cwd=str(cwd or self.bare), capture_output=True, text=True, env=env)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def sha_of(self, ref: str) -> str | None:
        if not self.exists or not self.bare.exists():
            return None
        proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"], cwd=str(self.bare), capture_output=True, text=True)
        return proc.stdout.strip() or None

    def branches(self) -> list[str]:
        if not self.exists or not self.bare.exists():
            return []
        out = self.git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], check=False)
        return [b for b in out.splitlines() if b]

    def _login_of(self, name: str) -> dict | None:
        """A commit's author or committer name as a GitHub account: the bots commit under their own login as the name
        (dispatch.git_identity), a person under theirs."""
        return {"login": name} if name in self.users else None

    # ---- the request --------------------------------------------------------------------------------------

    def handle(self, method: str, target: str, headers: dict, raw: bytes) -> tuple[int, Any]:
        parts = urlsplit(target)
        path = unquote(parts.path).rstrip("/")
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        try:
            body = json.loads(raw.decode()) if raw else {}
        except ValueError:
            return 400, {"message": "body is not JSON"}
        with self.lock:
            login = self.login_for(headers)
            self.calls.append((method, path, login))
            for pattern, verb, fn in self._routes():
                if verb != method:
                    continue
                m = re.fullmatch(pattern, path)
                if m:
                    return fn(m.groupdict(), query, body, headers, login)
            return 404, {"message": "Not Found", "path": path}

    def _routes(self):
        r = re.escape(f"/repos/{self.repo}")
        return [
            (r"/user", "GET", self._user),
            (r"/users/(?P<login>[^/]+)", "GET", self._users),
            (r"/user/repos", "POST", self._create_repo),
            (r"/orgs/(?P<org>[^/]+)/repos", "POST", self._create_repo),
            (r"/app-manifests/(?P<code>[^/]+)/conversions", "POST", self._convert),
            (r"/app", "GET", self._app),
            (r"/app/installations", "GET", self._app_installations),
            (r"/app/installations/(?P<id>\d+)/access_tokens", "POST", self._access_token),
            (r"/search/issues", "GET", self._search),
            (r + r"", "GET", self._repo),
            (r + r"/branches", "GET", self._branches),
            (r + r"/branches/(?P<branch>[^/]+)/protection", "PUT", self._protect),
            (r + r"/labels", "GET", lambda p, q, b, h, l: (200, list(self.labels.values()))),
            (r + r"/labels", "POST", self._create_label),
            (r + r"/milestones", "GET", self._milestones),
            (r + r"/milestones", "POST", self._create_milestone),
            (r + r"/milestones/(?P<n>\d+)", "GET", lambda p, q, b, h, l: self._one(self.milestones, int(p["n"]))),
            (r + r"/milestones/(?P<n>\d+)", "PATCH", self._patch_milestone),
            (r + r"/issues", "GET", self._issues),
            (r + r"/issues", "POST", self._create_issue),
            (r + r"/issues/comments/(?P<id>\d+)", "PATCH", self._patch_comment),
            (r + r"/issues/(?P<n>\d+)", "GET", self._issue),
            (r + r"/issues/(?P<n>\d+)", "PATCH", self._patch_issue),
            (r + r"/issues/(?P<n>\d+)/comments", "GET", self._comments),
            (r + r"/issues/(?P<n>\d+)/comments", "POST", self._create_comment),
            (r + r"/issues/(?P<n>\d+)/labels", "POST", self._add_labels),
            (r + r"/issues/(?P<n>\d+)/labels/(?P<label>.+)", "DELETE", self._remove_label),
            (r + r"/issues/(?P<n>\d+)/events", "GET", lambda p, q, b, h, l: (200, list(self._item(int(p["n"])).get("events", [])))),
            (r + r"/pulls", "GET", self._pulls),
            (r + r"/pulls", "POST", self._create_pull),
            (r + r"/pulls/(?P<n>\d+)", "GET", lambda p, q, b, h, l: (200, self._pull_json(self._item(int(p["n"]))))),
            (r + r"/pulls/(?P<n>\d+)", "PATCH", self._patch_pull),
            (r + r"/pulls/(?P<n>\d+)/reviews", "GET", lambda p, q, b, h, l: (200, list(self.reviews.get(int(p["n"]), [])))),
            (r + r"/pulls/(?P<n>\d+)/reviews", "POST", self._create_review),
            (r + r"/pulls/(?P<n>\d+)/commits", "GET", self._pull_commits),
            (r + r"/pulls/(?P<n>\d+)/files", "GET", self._pull_files),
            (r + r"/pulls/(?P<n>\d+)/merge", "PUT", self._merge),
            (r + r"/commits/(?P<sha>[0-9a-f]+)/check-runs", "GET", lambda p, q, b, h, l: (200, {"total_count": len(self.check_runs.get(p["sha"], [])), "check_runs": list(self.check_runs.get(p["sha"], []))})),
            (r + r"/commits/(?P<sha>[0-9a-f]+)/status", "GET", self._combined_status),
            (r + r"/statuses/(?P<sha>[0-9a-f]+)", "POST", self._create_status),
            (r + r"/check-runs", "POST", self._create_check_run),
            (r + r"/contents/(?P<path>.+)", "GET", self._contents),
            (r + r"/git/blobs/(?P<sha>[0-9a-f]+)", "GET", self._blob),
        ]

    # ---- users and repository ---------------------------------------------------------------------------

    def _user(self, p, q, b, h, login):
        if login is None:
            return 401, {"message": "Requires authentication"}
        return 200, self.users[login]

    def _users(self, p, q, b, h, login):
        user = self.users.get(p["login"])
        return (200, user) if user else (404, {"message": "Not Found"})

    def _create_repo(self, p, q, b, h, login):
        if login is None:
            return 401, {"message": "Requires authentication"}
        if "org" in p and p["org"] != self.owner:
            return 404, {"message": "Not Found"}
        if b.get("name") != self.name or ("org" not in p and login != self.owner):
            return 404, {"message": "Not Found"}
        if self.exists:
            return 422, {"message": "name already exists on this account"}
        self.exists = True
        self.bare.parent.mkdir(parents=True, exist_ok=True)
        self.git(["init", "-q", "--bare", "-b", "main", str(self.bare)], cwd=self.bare.parent)
        return 201, self._repo_json()

    def _repo_json(self) -> dict:
        return {"full_name": self.repo, "name": self.name, "owner": {"login": self.owner}, "private": True, "default_branch": "main",
                "html_url": f"https://github.com/{self.repo}"}

    def _repo(self, p, q, b, h, login):
        return (200, self._repo_json()) if self.exists else (404, {"message": "Not Found"})

    def _branches(self, p, q, b, h, login):
        return 200, [{"name": n, "commit": {"sha": self.sha_of(n)}} for n in self.branches()]

    def _protect(self, p, q, b, h, login):
        if self.free_plan:
            return 403, {"message": "Upgrade to GitHub Pro or make this repository public to enable this feature."}
        return 200, {"url": f"https://api.github.com/repos/{self.repo}/branches/{p['branch']}/protection"}

    # ---- Apps -------------------------------------------------------------------------------------------

    def _convert(self, p, q, b, h, login):
        manifest = self.manifests.pop(p["code"], None)
        if manifest is None:
            return 404, {"message": "Not Found"}
        self._app_id += 1
        slug = re.sub(r"[^a-z0-9-]+", "-", str(manifest.get("name", "app")).lower()).strip("-")
        if any(a["slug"] == slug for a in self.apps.values()):
            return 422, {"message": "Name has already been taken"}
        pem = _rsa_pem()
        self.apps[self._app_id] = {"id": self._app_id, "slug": slug, "name": manifest.get("name", slug), "pem": pem,
                                   "installation_id": None, "bot": f"{slug}[bot]", "permissions": manifest.get("default_permissions", {})}
        self._user_id += 1
        self.users[f"{slug}[bot]"] = {"login": f"{slug}[bot]", "id": self._user_id, "type": "Bot"}
        return 201, {"id": self._app_id, "slug": slug, "name": manifest.get("name", slug), "pem": pem,
                     "html_url": f"https://github.com/apps/{slug}", "client_id": "Iv1.rehearsal", "webhook_secret": None}

    def _app(self, p, q, b, h, login):
        app = self.app_for_jwt(h)
        if app is None:
            return 401, {"message": "A JSON web token could not be decoded"}
        return 200, {"id": app["id"], "slug": app["slug"], "name": app["name"], "owner": {"login": self.owner}}

    def _app_installations(self, p, q, b, h, login):
        app = self.app_for_jwt(h)
        if app is None:
            return 401, {"message": "A JSON web token could not be decoded"}
        return 200, [{"id": i, "app_id": a, "account": {"login": self.owner}, "permissions": app["permissions"]}
                     for i, a in self.installations.items() if a == app["id"]]

    def _access_token(self, p, q, b, h, login):
        app = self.app_for_jwt(h)
        inst = int(p["id"])
        if app is None or self.installations.get(inst) != app["id"]:
            return 404, {"message": "Not Found"}
        token = "ghs_" + secrets.token_hex(12)
        self.tokens[token] = app["bot"]
        perms = dict(app["permissions"]) or {"contents": "write", "issues": "write", "pull_requests": "write", "statuses": "write", "checks": "write", "metadata": "read"}
        return 201, {"token": token, "expires_at": _iso(self.clock() + timedelta(hours=1)), "permissions": perms,
                     "repositories": [{"full_name": self.repo, "name": self.name}]}

    # ---- labels and milestones ---------------------------------------------------------------------------

    def _create_label(self, p, q, b, h, login):
        name = str(b.get("name", ""))
        if name in self.labels:
            return 422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]}
        self.labels[name] = {"name": name, "color": b.get("color", "ededed"), "description": b.get("description", "")}
        return 201, self.labels[name]

    def _milestones(self, p, q, b, h, login):
        state = q.get("state", "open")
        return 200, [m for m in self.milestones.values() if state == "all" or m["state"] == state]

    def _create_milestone(self, p, q, b, h, login):
        n = len(self.milestones) + 1
        self.milestones[n] = {"number": n, "title": b.get("title", ""), "description": b.get("description", ""), "state": "open"}
        return 201, self.milestones[n]

    def _patch_milestone(self, p, q, b, h, login):
        m = self.milestones.get(int(p["n"]))
        if m is None:
            return 404, {"message": "Not Found"}
        m.update({k: v for k, v in b.items() if k in ("title", "description", "state")})
        return 200, m

    @staticmethod
    def _one(table: dict, key: int) -> tuple[int, Any]:
        return (200, table[key]) if key in table else (404, {"message": "Not Found"})

    # ---- issues -----------------------------------------------------------------------------------------

    def _item(self, n: int) -> dict:
        return self.items.get(n) or {}

    def _issue_json(self, it: dict) -> dict:
        out = {"number": it["number"], "title": it["title"], "body": it["body"], "state": it["state"],
               "labels": [{"name": l} for l in it["labels"]], "milestone": {"number": it["milestone"]} if it.get("milestone") else None,
               "user": {"login": it["user"]}, "created_at": it["created_at"], "updated_at": it["updated_at"], "closed_at": it.get("closed_at"),
               "html_url": f"https://github.com/{self.repo}/issues/{it['number']}", "comments": sum(1 for c in self.comments.values() if c["issue"] == it["number"])}
        if it.get("pull"):
            out["pull_request"] = {"url": f"https://api.github.com/repos/{self.repo}/pulls/{it['number']}"}
        return out

    def _issues(self, p, q, b, h, login):
        state = q.get("state", "open")
        wanted = q.get("milestone")
        out = []
        for it in self.items.values():
            if state != "all" and it["state"] != state:
                continue
            if wanted not in (None, "*") and str(it.get("milestone") or "none") != wanted:
                continue
            out.append(self._issue_json(it))
        return 200, out

    def _new_item(self, *, title: str, body: str, user: str, labels: list[str] | None = None, milestone: int | None = None, pull: dict | None = None) -> dict:
        self._number += 1
        now = _iso(self.clock())
        it = {"number": self._number, "title": title, "body": body or "", "state": "open", "labels": list(labels or []),
              "milestone": milestone, "user": user, "created_at": now, "updated_at": now, "closed_at": None, "events": [], "pull": pull}
        self.items[it["number"]] = it
        return it

    def _create_issue(self, p, q, b, h, login):
        if login is None:
            return 401, {"message": "Requires authentication"}
        it = self._new_item(title=str(b.get("title", "")), body=str(b.get("body") or ""), user=login,
                            labels=[str(l) for l in b.get("labels") or []], milestone=b.get("milestone"))
        return 201, self._issue_json(it)

    def _issue(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        return (200, self._issue_json(it)) if it else (404, {"message": "Not Found"})

    def _patch_issue(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it:
            return 404, {"message": "Not Found"}
        for k in ("title", "body"):
            if k in b:
                it[k] = str(b[k] or "")
        if "milestone" in b:
            it["milestone"] = b["milestone"]
        if "labels" in b:
            it["labels"] = [str(l) for l in b["labels"]]
        if b.get("state") in ("open", "closed"):
            self._set_state(it, b["state"], login)
        it["updated_at"] = _iso(self.clock())
        return 200, self._issue_json(it)

    def _set_state(self, it: dict, state: str, login: str | None) -> None:
        if it["state"] != state:
            it["state"] = state
            it["closed_at"] = _iso(self.clock()) if state == "closed" else None
            self._event(it, "closed" if state == "closed" else "reopened", login)

    def _event(self, it: dict, kind: str, login: str | None, label: str | None = None) -> None:
        self._event_id += 1
        ev = {"id": self._event_id, "event": kind, "actor": {"login": login or self.human}, "created_at": _iso(self.clock())}
        if label is not None:
            ev["label"] = {"name": label}
        it.setdefault("events", []).append(ev)

    def _comments(self, p, q, b, h, login):
        n = int(p["n"])
        since = q.get("since")
        out = [c for c in self.comments.values() if c["issue"] == n and (since is None or c["created_at"] >= since)]
        return 200, [{k: v for k, v in c.items() if k != "issue"} for c in sorted(out, key=lambda c: c["id"])]

    def _create_comment(self, p, q, b, h, login):
        if login is None:
            return 401, {"message": "Requires authentication"}
        n = int(p["n"])
        if n not in self.items:
            return 404, {"message": "Not Found"}
        self._comment_id += 1
        c = {"id": self._comment_id, "issue": n, "user": {"login": login}, "created_at": _iso(self.clock()), "updated_at": _iso(self.clock()),
             "body": str(b.get("body", "")), "html_url": f"https://github.com/{self.repo}/issues/{n}#issuecomment-{self._comment_id}"}
        self.comments[c["id"]] = c
        self.items[n]["updated_at"] = c["created_at"]
        return 201, {k: v for k, v in c.items() if k != "issue"}

    def _patch_comment(self, p, q, b, h, login):
        c = self.comments.get(int(p["id"]))
        if c is None:
            return 404, {"message": "Not Found"}
        c["body"] = str(b.get("body", c["body"]))
        c["updated_at"] = _iso(self.clock())
        return 200, {k: v for k, v in c.items() if k != "issue"}

    def _add_labels(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it:
            return 404, {"message": "Not Found"}
        for name in b.get("labels") or []:
            if name not in it["labels"]:
                it["labels"].append(str(name))
                self._event(it, "labeled", login, str(name))
        it["updated_at"] = _iso(self.clock())
        return 200, [{"name": l} for l in it["labels"]]

    def _remove_label(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        name = p["label"]
        if not it or name not in it["labels"]:
            return 404, {"message": "Label does not exist"}
        it["labels"].remove(name)
        self._event(it, "unlabeled", login, name)
        it["updated_at"] = _iso(self.clock())
        return 200, [{"name": l} for l in it["labels"]]

    def _search(self, p, q, b, h, login):
        words = [w for w in q.get("q", "").split() if ":" not in w]
        hits = [self._issue_json(it) for it in self.items.values()
                if all(w.lower() in (it["title"] + " " + it["body"]).lower() for w in words)]
        return 200, {"total_count": len(hits), "incomplete_results": False, "items": hits}

    # ---- pull requests -----------------------------------------------------------------------------------

    def _pull_json(self, it: dict) -> dict:
        pr = it["pull"]
        head = self.sha_of(pr["head"]) or pr.get("head_sha") or ""
        pr["head_sha"] = head
        return {"number": it["number"], "title": it["title"], "body": it["body"], "state": it["state"], "merged_at": pr.get("merged_at"),
                "merged": pr.get("merged_at") is not None, "merge_commit_sha": pr.get("merge_commit_sha"), "user": {"login": it["user"]},
                "head": {"ref": pr["head"], "sha": head}, "base": {"ref": pr["base"]}, "labels": [{"name": l} for l in it["labels"]],
                "html_url": f"https://github.com/{self.repo}/pull/{it['number']}", "mergeable": self._mergeable(pr),
                "mergeable_state": "clean" if self._mergeable(pr) else "dirty", "created_at": it["created_at"], "updated_at": it["updated_at"]}

    def _mergeable(self, pr: dict) -> bool:
        head, base = self.sha_of(pr["head"]), self.sha_of(pr["base"])
        if not head or not base:
            return False
        proc = subprocess.run(["git", "merge-tree", "--write-tree", f"{base}", f"{head}"], cwd=str(self.bare), capture_output=True, text=True)
        return proc.returncode == 0

    def _pulls(self, p, q, b, h, login):
        state = q.get("state", "open")
        return 200, [self._pull_json(it) for it in self.items.values() if it.get("pull") and (state == "all" or it["state"] == state)]

    def _create_pull(self, p, q, b, h, login):
        if login is None:
            return 401, {"message": "Requires authentication"}
        head, base = str(b.get("head", "")), str(b.get("base", "main"))
        if self.sha_of(head) is None:
            return 422, {"message": "Validation Failed", "errors": [{"message": f"head branch {head} does not exist"}]}
        if any(it.get("pull") and it["state"] == "open" and it["pull"]["head"] == head for it in self.items.values()):
            return 422, {"message": "Validation Failed", "errors": [{"message": f"A pull request already exists for {head}."}]}
        it = self._new_item(title=str(b.get("title", "")), body=str(b.get("body") or ""), user=login, pull={"head": head, "base": base, "merged_at": None})
        return 201, self._pull_json(it)

    def _patch_pull(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it or not it.get("pull"):
            return 404, {"message": "Not Found"}
        if "body" in b:
            it["body"] = str(b["body"] or "")
        if "title" in b:
            it["title"] = str(b["title"])
        if b.get("state") in ("open", "closed"):
            self._set_state(it, b["state"], login)
        it["updated_at"] = _iso(self.clock())
        return 200, self._pull_json(it)

    def _create_review(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it or not it.get("pull") or login is None:
            return 404, {"message": "Not Found"}
        event = str(b.get("event", "COMMENT"))
        if event in ("APPROVE", "REQUEST_CHANGES") and login == it["user"]:
            return 422, {"message": "Validation Failed", "errors": ["Can not approve your own pull request"]}
        self._review_id += 1
        state = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}.get(event, "COMMENTED")
        rv = {"id": self._review_id, "user": {"login": login}, "state": state, "commit_id": str(b.get("commit_id") or self.sha_of(it["pull"]["head"]) or ""),
              "submitted_at": _iso(self.clock()), "body": str(b.get("body", ""))}
        self.reviews.setdefault(it["number"], []).append(rv)
        return 200, rv

    def _range(self, pr: dict) -> tuple[str, str] | None:
        head, base = self.sha_of(pr["head"]), self.sha_of(pr["base"])
        if not head or not base:
            return None
        mb = self.git(["merge-base", base, head], check=False) or base
        return mb, head

    def _pull_commits(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it or not it.get("pull"):
            return 404, {"message": "Not Found"}
        rng = self._range(it["pull"])
        if rng is None:
            return 200, []
        out = []
        for line in self.git(["log", "--reverse", "--format=%H%x00%an%x00%cn%x00%s", f"{rng[0]}..{rng[1]}"]).splitlines():
            sha, an, cn, subject = line.split("\x00")
            out.append({"sha": sha, "author": self._login_of(an), "committer": self._login_of(cn), "commit": {"message": subject, "author": {"name": an}, "committer": {"name": cn}}})
        return 200, out

    def _pull_files(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it or not it.get("pull"):
            return 404, {"message": "Not Found"}
        rng = self._range(it["pull"])
        if rng is None:
            return 200, []
        out = []
        for line in self.git(["diff", "--name-status", "-M", rng[0], rng[1]]).splitlines():
            parts = line.split("\t")
            code = parts[0][0]
            status = {"A": "added", "M": "modified", "D": "removed", "R": "renamed"}.get(code, "modified")
            entry = {"filename": parts[-1], "status": status}
            if code == "R":
                entry["previous_filename"] = parts[1]
            out.append(entry)
        return 200, out

    def _merge(self, p, q, b, h, login):
        it = self._item(int(p["n"]))
        if not it or not it.get("pull") or login is None:
            return 404, {"message": "Not Found"}
        pr = it["pull"]
        if pr.get("merged_at"):
            return 405, {"message": "Pull Request is not mergeable"}
        head = self.sha_of(pr["head"])
        if b.get("sha") and b["sha"] != head:
            return 409, {"message": "Head branch was modified. Review and try the merge again."}
        if not self._mergeable(pr):
            return 405, {"message": "Pull Request is not mergeable"}
        if not self.work.exists():
            self.git(["clone", "-q", str(self.bare), str(self.work)], cwd=self.bare.parent)
        w = self.work
        self.git(["fetch", "-q", "origin"], cwd=w)
        self.git(["checkout", "-q", "-B", pr["base"], f"origin/{pr['base']}"], cwd=w)
        self.git(["merge", "-q", "--squash", f"origin/{pr['head']}"], cwd=w)
        message = str(b.get("commit_title") or it["title"]) + ("\n\n" + str(b["commit_message"]) if b.get("commit_message") else "")
        self.git(["-c", f"user.name={login}", "-c", f"user.email={login}@users.noreply.github.com", "commit", "-q", "-m", message], cwd=w)
        self.git(["push", "-q", "origin", f"HEAD:refs/heads/{pr['base']}"], cwd=w)
        sha = self.git(["rev-parse", "HEAD"], cwd=w)
        pr["merged_at"], pr["merge_commit_sha"] = _iso(self.clock()), sha
        self._set_state(it, "closed", login)
        for n in CLOSES_RE.findall(it["body"]):
            target = self.items.get(int(n))
            if target and not target.get("pull"):
                self._set_state(target, "closed", login)
        return 200, {"sha": sha, "merged": True, "message": "Pull Request successfully merged"}

    # ---- statuses, checks, contents ----------------------------------------------------------------------

    def _combined_status(self, p, q, b, h, login):
        rows = self.statuses.get(p["sha"], [])
        latest: dict[str, dict] = {}
        for s in rows:
            latest[s["context"]] = s
        states = {s["state"] for s in latest.values()}
        state = "failure" if "failure" in states or "error" in states else "pending" if "pending" in states or not states else "success"
        return 200, {"state": state, "sha": p["sha"], "statuses": list(latest.values())}

    def _create_status(self, p, q, b, h, login):
        s = {"state": str(b.get("state", "pending")), "context": str(b.get("context", "default")), "description": str(b.get("description", "")),
             "creator": {"login": login}, "created_at": _iso(self.clock())}
        self.statuses.setdefault(p["sha"], []).append(s)
        return 201, s

    def _create_check_run(self, p, q, b, h, login):
        run = {"id": len(self.check_runs) + 1, "name": str(b.get("name", "")), "head_sha": str(b.get("head_sha", "")), "status": str(b.get("status", "completed")),
               "conclusion": b.get("conclusion"), "output": b.get("output")}
        self.check_runs.setdefault(run["head_sha"], []).append(run)
        return 201, run

    def _contents(self, p, q, b, h, login):
        ref = q.get("ref", "main")
        sha = self.sha_of(ref) or ref
        proc = subprocess.run(["git", "show", f"{sha}:{p['path']}"], cwd=str(self.bare), capture_output=True)
        if proc.returncode != 0:
            return 404, {"message": "Not Found"}
        blob = self.git(["rev-parse", f"{sha}:{p['path']}"])
        return 200, {"type": "file", "path": p["path"], "sha": blob, "size": len(proc.stdout), "encoding": "base64",
                     "content": base64.b64encode(proc.stdout).decode()}

    def _blob(self, p, q, b, h, login):
        proc = subprocess.run(["git", "cat-file", "blob", p["sha"]], cwd=str(self.bare), capture_output=True)
        if proc.returncode != 0:
            return 404, {"message": "Not Found"}
        return 200, {"sha": p["sha"], "size": len(proc.stdout), "encoding": "base64", "content": base64.b64encode(proc.stdout).decode()}


def _rsa_pem() -> str:
    """A fresh RSA private key, as GitHub hands one back with a new App: `openssl` is what meld signs App JWTs with too."""
    proc = subprocess.run(["openssl", "genrsa", "2048"], capture_output=True, text=True, check=True)
    return proc.stdout
