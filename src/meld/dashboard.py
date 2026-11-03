"""The viewer's link on GitHub (plan 74): a person who opens the repository finds the page that watches it.

`meld start` and `meld watch` serve the page on this machine's loopback only, so the link works from this machine
alone; it is still the one place a person looks when they wonder where Meld is. The link goes in two places: the
repository's homepage field, shown at the top of the repository page, and a `## Dashboard` section at the end of the
coordination issue's body, which `assemble` strips from the goal the agents read.
"""
from __future__ import annotations

import re
import socket

from meld.github import GitHub, GitHubError

HEADING = "## Dashboard"
_SECTION_RE = re.compile(r"\n*## Dashboard\n.*\Z", re.S)


def strip_section(body: str) -> str:
    """The body without its `## Dashboard` section (the last section, if present)."""
    return _SECTION_RE.sub("", body or "").rstrip("\n")


def section(link: str, host: str) -> str:
    return (f"{HEADING}\n\nMeld's page for this milestone: {link}\n\nIt is served on `{host}` and reachable only from that machine; "
            "running `meld start` or `meld watch` there again refreshes this line.")


def with_section(body: str, link: str, host: str) -> str:
    base = strip_section(body)
    return (base + "\n\n" if base else "") + section(link, host) + "\n"


def publish(gh: GitHub, link: str, *, host: str | None = None) -> dict:
    """Put `link` on the repository's homepage and in the coordination issue's `## Dashboard` section. Raises
    GitHubError or BoardError; callers that serve a page treat either as a note, never a reason not to serve."""
    from meld.assemble import find_active_milestone
    host = host or socket.gethostname()
    _, coordination = find_active_milestone(gh)
    raw = gh.get(gh._r(f"issues/{coordination}"), use_cache=False)
    old = (raw.get("body") if isinstance(raw, dict) else None) or ""
    new = with_section(old, link, host)
    changed = new != old
    if changed:
        gh.update_issue(coordination, body=new)
    gh.patch(f"repos/{gh.repo}", {"homepage": link})
    return {"issue": coordination, "homepage": link, "issue_updated": changed}


def try_publish(gh: GitHub, link: str, *, host: str | None = None) -> str | None:
    """`publish`, with a failure returned as a one-line note instead of raised."""
    from meld.assemble import BoardError
    try:
        publish(gh, link, host=host)
        return None
    except (GitHubError, BoardError) as e:
        return f"could not put the dashboard link on GitHub: {e}"
