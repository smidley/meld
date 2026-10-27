"""Plan 31: the page's usage and limits view, read from $MELD_HOME alone."""
import json
from pathlib import Path

from meld.limits import freeze_view, usage_totals
from meld.runners import freeze_deadline, freeze_path
from meld.transcript import Event, cap
from tests.factories import CFG2

NOW = "2026-10-26T23:50:00Z"


class UsageAdapter:
    """Parses the little JSON lines this test writes: {"usage": {...}} lines and {"done": cost} lines."""
    name = "fake"

    def parse_events(self, line, now):
        try:
            d = json.loads(line)
        except ValueError:
            return []
        if "usage" in d:
            return [Event(seq=0, at=d.get("at", now), kind="usage", text="", meta={"usage": d["usage"]})]
        if "done" in d:
            return [Event(seq=0, at=d.get("at", now), kind="run_finished", ok=True, text="ok", meta={"cost_usd": d["done"], "usage": d.get("u", {})})]
        return []


def test_freeze_deadline_is_the_named_instant_capped_or_the_retry():
    assert freeze_deadline({"at": "2026-10-26T23:39:50Z"}, 30) == "2026-10-27T00:09:50Z"
    assert freeze_deadline({"at": "2026-10-26T23:39:50Z", "resets_at": "2026-10-27T01:00:00Z"}, 30) == "2026-10-27T01:00:00Z"
    assert freeze_deadline({"at": "2026-10-26T23:39:50Z", "resets_at": "2026-11-03T00:00:00Z"}, 30) == "2026-11-03T00:00:00Z"   # a week is believed
    assert freeze_deadline({"at": "2026-10-26T23:39:50Z", "resets_at": "2026-12-25T00:00:00Z"}, 30) == "2026-11-03T23:39:50Z"   # eight days at most
    assert freeze_deadline({"at": "2026-10-26T23:39:50Z", "retry_minutes": 120}, 30) == "2026-10-27T01:39:50Z"              # the record's own interval wins
    assert freeze_deadline({"at": "junk"}, 30) == "" and freeze_deadline({}, 30) == ""


def test_freeze_view_reports_each_frozen_role_with_the_providers_words(tmp_path: Path):
    home = tmp_path / "home"
    p = freeze_path(home, "codex", "pm")
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"at": "2026-10-26T23:39:52Z", "posted": True, "announce_attempted_at": "2026-10-26T23:39:52Z",
                             "text": "You've reached your free usage limit for now."}))
    q = freeze_path(home, "claude", "orchestrator")
    q.parent.mkdir(parents=True)
    q.write_text(json.dumps({"at": "2026-10-26T20:00:00Z", "posted": True}))          # long passed: expired, still listed as such
    view = freeze_view(CFG2, home, NOW)
    assert set(view) == {"codex", "claude"} and list(view["codex"]) == ["pm"]
    f = view["codex"]["pm"]
    assert f["text"].startswith("You've reached") and f["until"] == "2026-10-27T00:09:52Z" and f["expired"] is False and f["resets_at"] is None
    assert view["claude"]["orchestrator"]["expired"] is True
    assert freeze_view(CFG2, tmp_path / "nowhere", NOW) == {}


def test_usage_totals_sum_every_transcript_through_the_groups_adapter_and_cache_by_size(tmp_path: Path):
    home = tmp_path / "home"
    logs = home / "logs" / "claude"
    logs.mkdir(parents=True)
    (logs / "pm-1.log").write_text(json.dumps({"usage": {"input_tokens": 900, "output_tokens": 50, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 100}, "at": "2026-10-26T20:00:00Z"}) + "\n"
                                   + json.dumps({"done": 0.25, "u": {"input_tokens": 10, "output_tokens": 5}, "at": "2026-10-26T20:10:00Z"}) + "\n")
    (logs / "build-11.log").write_text(json.dumps({"done": 1.5, "u": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 60}, "at": "2026-10-26T21:00:00Z"}) + "\n")
    (home / "logs" / "codex").mkdir()
    cache: dict = {}
    calls = []
    def adapter_for(cli):
        calls.append(cli)
        return UsageAdapter()
    out = usage_totals(CFG2, home, adapter_for, cache, NOW)
    assert out["claude"] == {"input_tokens": 1110, "output_tokens": 75, "cached_tokens": 460, "cost_usd": 1.75, "runs": 2, "last_at": "2026-10-26T21:00:00Z", "logs": 2}
    assert out["codex"]["logs"] == 0 and out["codex"]["cost_usd"] == 0.0
    assert len(cache) == 2
    again = usage_totals(CFG2, home, adapter_for, cache, NOW)
    assert again == out                                                     # unchanged files are not re-read: same answer, same cache
    with (logs / "pm-1.log").open("a") as fh:
        fh.write(json.dumps({"usage": {"input_tokens": 5, "output_tokens": 5}}) + "\n")
    grown = usage_totals(CFG2, home, adapter_for, cache, NOW)
    assert grown["claude"]["input_tokens"] == 1115 and grown["claude"]["output_tokens"] == 80
    def no_adapter(cli):
        raise KeyError(cli)
    assert usage_totals(CFG2, home, no_adapter, {}, NOW)["claude"]["unknown"] is True
