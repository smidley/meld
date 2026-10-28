"""The `gh` the rehearsal puts on PATH: the subset meld uses, answered by the stand-in GitHub over loopback HTTP.

`gh api --include -X METHOD [-H k: v]... [--input -] ENDPOINT` becomes one HTTP request to `$MELD_REHEARSAL_GITHUB`
and prints what the real `gh --include` prints (a status line, headers, a blank line, the body), so `github.GitHub`
reads it unchanged. `gh auth token [--user X]` prints the person's token (`$MELD_REHEARSAL_HUMAN_TOKEN`, or the one
`$MELD_REHEARSAL_TOKEN_<X>` names), `gh auth status` says the person is logged in, `gh --version` says what this is.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT = "#!{python}\nfrom meld.rehearsal.ghshim import main\nmain()\n"


def install(bin_dir: Path, python: str | None = None) -> Path:
    """Write the `gh` executable into `bin_dir` (prepend it to PATH) and return its path."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "gh"
    path.write_text(SCRIPT.format(python=python or sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def human_token(user: str | None = None) -> str:
    if user:
        named = os.environ.get(f"MELD_REHEARSAL_TOKEN_{user.upper().replace('-', '_')}")
        if named:
            return named
    return os.environ.get("MELD_REHEARSAL_HUMAN_TOKEN", "")


def api(argv: list[str], stdin: str) -> int:
    base = os.environ.get("MELD_REHEARSAL_GITHUB", "")
    if not base:
        sys.stderr.write("gh (rehearsal): MELD_REHEARSAL_GITHUB is not set\n")
        return 1
    method, endpoint, headers, want_input = "GET", None, {}, False
    it = iter(argv)
    for tok in it:
        if tok == "-X":
            method = next(it).upper()
        elif tok == "-H":
            k, _, v = next(it).partition(": ")
            headers[k] = v
        elif tok == "--input":
            next(it)
            want_input = True
        elif tok.startswith("-"):
            continue
        else:
            endpoint = tok
    if endpoint is None:
        sys.stderr.write("gh (rehearsal): no endpoint\n")
        return 1
    token = os.environ.get("GH_TOKEN") or human_token()
    if token:
        headers["Authorization"] = f"token {token}"
    headers.setdefault("Accept", "application/vnd.github+json")
    body = stdin.encode() if want_input else None
    req = urllib.request.Request(f"{base}/{endpoint.lstrip('/')}", data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, resp_headers, text = resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as e:
        status, resp_headers, text = e.code, dict(e.headers), e.read().decode()
    reason = {200: "OK", 201: "Created", 204: "No Content", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
              404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 422: "Unprocessable Entity"}.get(status, "")
    out = f"HTTP/2.0 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in resp_headers.items() if k.lower() in ("content-type", "etag", "x-ratelimit-remaining", "x-ratelimit-reset")) + "\r\n" + text
    sys.stdout.write(out)
    return 0 if status < 400 else 1


def main() -> None:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("gh version 2.0.0-rehearsal (meld)")
        sys.exit(0)
    if argv[:2] == ["auth", "token"]:
        user = argv[argv.index("--user") + 1] if "--user" in argv and argv.index("--user") + 1 < len(argv) else None
        token = human_token(user)
        if not token:
            sys.stderr.write("no oauth token\n")
            sys.exit(1)
        print(token)
        sys.exit(0)
    if argv[:2] == ["auth", "status"]:
        print("github.com\n  ✓ Logged in to github.com account rehearsal (keyring)")
        sys.exit(0)
    if argv[:1] == ["api"]:
        stdin = sys.stdin.read() if "--input" in argv else ""
        sys.exit(api(argv[1:], stdin))
    sys.stderr.write(f"gh (rehearsal): unsupported command {' '.join(argv)}\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
