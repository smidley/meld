"""A fake `gh api` for tests: routes (method, path) to canned responses."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit


@dataclass
class Call:
    method: str
    endpoint: str
    query: dict[str, str]
    body: dict | None
    headers: dict[str, str]


@dataclass
class FakeGh:
    routes: dict[tuple[str, str], tuple[int, object, str | None]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    rate_limit_next: bool = False

    def route(self, method: str, endpoint: str, body: object, status: int = 200, etag: str | None = None) -> None:
        self.routes[(method.upper(), endpoint)] = (status, body, etag)

    def __call__(self, cmd, input=None, capture_output=True, text=True, env=None, **_):
        assert cmd[:3] == ["gh", "api", "--include"], cmd
        method, endpoint, headers = "GET", None, {}
        it = iter(cmd[3:])
        for tok in it:
            if tok == "-X":
                method = next(it).upper()
            elif tok == "-H":
                k, _, v = next(it).partition(": ")
                headers[k.lower()] = v
            elif tok == "--input":
                next(it)
            elif tok.startswith("-"):
                continue
            else:
                endpoint = tok
        assert endpoint is not None, cmd
        parts = urlsplit(endpoint)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        body = json.loads(input) if input else None
        self.calls.append(Call(method, parts.path, query, body, headers))

        if self.rate_limit_next:
            self.rate_limit_next = False
            head = "HTTP/2.0 403 Forbidden\r\nX-Ratelimit-Remaining: 0\r\nX-Ratelimit-Reset: 1700000000\r\n\r\n"
            return subprocess.CompletedProcess(cmd, 1, stdout=head + '{"message":"API rate limit exceeded"}', stderr="")

        if (method, parts.path) not in self.routes:
            head = "HTTP/2.0 404 Not Found\r\n\r\n"
            return subprocess.CompletedProcess(cmd, 1, stdout=head + '{"message":"Not Found"}', stderr="")
        status, resp, etag = self.routes[(method, parts.path)]
        if etag and headers.get("if-none-match") == etag:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"HTTP/2.0 304 Not Modified\r\nETag: {etag}\r\n\r\n", stderr="")
        head = f"HTTP/2.0 {status} OK\r\n" + (f"ETag: {etag}\r\n" if etag else "") + "\r\n"
        out = head + (json.dumps(resp) if resp is not None else "")
        return subprocess.CompletedProcess(cmd, 0 if status < 400 else 1, stdout=out, stderr="")
