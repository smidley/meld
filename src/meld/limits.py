"""Usage and limits, for the page (plan 31): which roles are frozen on a provider's usage limit, what the provider said,
when the freeze ends and the supervisor tries again, and what each group has spent in tokens and money as far as the
transcripts kept on this machine can say. Read from `$MELD_HOME` alone; nothing here decides anything."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from meld.config import Config
from meld.runners import freeze_deadline, freeze_path, read_record
from meld.transcript import Tail


def freeze_view(cfg: Config, home: Path, now: str) -> dict[str, dict[str, dict]]:
    """Per group, per frozen role: when it froze, what the CLI said, the instant the CLI named (if any) and the instant the
    supervisor will try again -- the same arithmetic `runners._frozen` uses, without its side effects."""
    from meld.prompts import ROLES
    out: dict[str, dict[str, dict]] = {}
    for g in cfg.groups:
        for r in ROLES:
            freeze = read_record(freeze_path(home, g, r))
            if freeze is None:
                continue
            until = freeze_deadline(freeze, cfg.runners_limit_retry_minutes)
            out.setdefault(g, {})[r] = {"at": freeze.get("at", ""), "resets_at": freeze.get("resets_at") or None, "text": str(freeze.get("text") or ""),
                                        "until": until, "posted": bool(freeze.get("posted")), "expired": not until or until <= now}
    return out


def _tally(events) -> dict:
    t = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0, "runs": 0, "last_at": ""}
    for e in events:
        usage = e.meta.get("usage") if isinstance(e.meta.get("usage"), dict) else None
        if e.kind in ("usage", "run_finished") and usage:
            t["input_tokens"] += int(usage.get("input_tokens") or 0)
            t["output_tokens"] += int(usage.get("output_tokens") or 0)
            t["cached_tokens"] += int(usage.get("cache_read_input_tokens") or usage.get("cached_input_tokens") or usage.get("cache_read_tokens") or 0)
        if e.kind == "run_finished":
            t["runs"] += 1
            cost = e.meta.get("cost_usd")
            if isinstance(cost, (int, float)):
                t["cost_usd"] += float(cost)
        if e.at > t["last_at"]:
            t["last_at"] = e.at
    return t


def usage_totals(cfg: Config, home: Path, adapter_for: Callable, cache: dict, now: str) -> dict[str, dict]:
    """Per group: tokens in, out and served from cache, dollars where the CLI reports them, and runs, summed over every
    transcript under `$MELD_HOME/logs/<group>` through that group's own adapter. `cache` maps a log path to
    (size, totals) so an unchanged file is never re-read; a grown file is re-read whole (a role's log grows for hours,
    and this runs off the request path, on the viewer's own refresh cadence)."""
    out: dict[str, dict] = {}
    for g, group in cfg.groups.items():
        total = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "cost_usd": 0.0, "runs": 0, "last_at": "", "logs": 0}
        try:
            adapter = adapter_for(group.cli)
        except Exception:   # an adapter this build lacks: the group's spend is unknown, not an error
            out[g] = {**total, "unknown": True}
            continue
        for path in sorted((home / "logs" / g).glob("*.log")):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            key = str(path)
            hit = cache.get(key)
            if hit is None or hit[0] != size:
                try:
                    tally = _tally(Tail(path).read(adapter.parse_events, now))
                except Exception:   # a log mid-rotation or unreadable: skip it this time
                    continue
                cache[key] = (size, tally)
            tally = cache[key][1]
            for k in ("input_tokens", "output_tokens", "cached_tokens", "runs"):
                total[k] += tally[k]
            total["cost_usd"] += tally["cost_usd"]
            total["last_at"] = max(total["last_at"], tally["last_at"])
            total["logs"] += 1
        total["cost_usd"] = round(total["cost_usd"], 4)
        out[g] = total
    return out
