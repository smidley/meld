"""The one write the viewer can make: a human verb, dispatched to the function the CLI calls (spec section 22).

The viewer holds no legality logic. Every entry below coerces the request's JSON into the arguments its verb's
own module already takes and calls it; `rules.py` decides whether the call is allowed, and the refusal that
comes back is rendered verbatim, rule included. Nothing here decides anything a `meld` command would decide
differently, and nothing here posts as any party but the human whose context the server holds.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from meld.actions import claim, release
from meld.challenge import challenge
from meld.memory import decide, remember
from meld.plan import plan_ack, plan_approve
from meld.posting import post
from meld.review import merge
from meld.tagging import tag


class BadRequest(ValueError):
    """The request named arguments its verb cannot use. Answered 400; the verb is never called."""


def _number(body: Mapping, verb: str) -> int:
    value = body.get("number")
    # bool is an int subclass, and `True` would silently claim issue #1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{verb} needs an integer 'number' (the issue or pull request), got {value!r}")
    return value


def _string(body: Mapping, key: str, verb: str, *, required: bool = False) -> str:
    value = body.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise BadRequest(f"{verb} needs a string '{key}', got {value!r}")
    if required and not value.strip():
        raise BadRequest(f"{verb} needs a non-empty '{key}'")
    return value


def _optional(body: Mapping, key: str, verb: str) -> str | None:
    value = body.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{verb} needs a string '{key}' or nothing at all, got {value!r}")
    return value


def _strings(body: Mapping, key: str, verb: str) -> tuple[str, ...] | None:
    value = body.get(key) or []
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise BadRequest(f"{verb} needs a list of strings for '{key}', got {value!r}")
    return tuple(value) or None


def _flag(body: Mapping, key: str, verb: str) -> bool:
    if key not in body:
        return False
    value = body[key]
    # Unlike every string field, a JSON body has a real boolean type -- so, unlike `_string`/`_optional`, this
    # does not coerce: `"force": "no"` and `"force": "false"` are not falsy in Python (`bool("no") is True`),
    # so accepting any truthy/falsy value here would silently force-release on a typo'd string.
    if not isinstance(value, bool):
        raise BadRequest(f"{verb} needs a boolean '{key}', got {value!r}")
    return value


#: Verb name (as the CLI spells it) to the call that runs it. Exactly the ten verbs section 22 grants the page:
#: every other legal verb either dispatches a worker, acts as a reviewer, or belongs to a group.
VERBS: dict[str, Callable[..., dict]] = {
    "claim": lambda ctx, b: claim(ctx, _number(b, "claim")),
    "release": lambda ctx, b: release(ctx, _number(b, "release"), force=_flag(b, "force", "release")),
    # No `joint=`: [joint] is a party, and the page posts as the human and nothing else.
    # The header verb (ASK, HALT, ACK, ...) is carried in its own field, "action" -- not "verb" -- because
    # "verb" at the top level is already this very dispatch table's own key ("post"); reusing it here would
    # mean the same JSON field would have to hold "post" (for dispatch) and, say, "ASK" (for the header) at
    # once. "action" mirrors the same-shaped choice `challenge` already makes below.
    "post": lambda ctx, b: post(ctx, _string(b, "action", "post", required=True).upper(),
                                ref=_optional(b, "ref", "post"), qualifier=_optional(b, "qualifier", "post"),
                                text=_string(b, "text", "post")),
    "tag": lambda ctx, b: tag(ctx, _number(b, "tag"), worker=_optional(b, "worker", "tag"),
                              model=_optional(b, "model", "tag"), effort=_optional(b, "effort", "tag"),
                              size=_optional(b, "size", "tag"), skills=_strings(b, "skills", "tag"),
                              scope=_strings(b, "scope", "tag"), may_delete=_strings(b, "may_delete", "tag"),
                              held=_strings(b, "held", "tag")),
    "challenge": lambda ctx, b: challenge(ctx, _string(b, "action", "challenge", required=True),
                                          _string(b, "ref", "challenge", required=True),
                                          _string(b, "text", "challenge")),
    "decide": lambda ctx, b: decide(ctx, _string(b, "ref", "decide", required=True),
                                    supersedes=_optional(b, "supersedes", "decide")),
    "remember": lambda ctx, b: remember(ctx, _string(b, "ref", "remember", required=True),
                                        _string(b, "text", "remember", required=True),
                                        supersedes=_optional(b, "supersedes", "remember")),
    "plan-approve": lambda ctx, b: plan_approve(ctx),
    "plan-ack": lambda ctx, b: plan_ack(ctx, _string(b, "ref", "plan-ack", required=True)),
    "merge": lambda ctx, b: merge(ctx, _number(b, "merge")),
}
