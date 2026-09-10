"""meld tag: the orchestrator (or a human) sets worker, model, effort, size, skill, scope, deletion, and held
declarations on an issue."""
from __future__ import annotations

from typing import Sequence

from meld.actions import refuse
from meld.board import HELD_CATEGORIES, body_section, held_label, replace_section
from meld.grammar import Header, compose
from meld.models import SIZES
from meld.rules import Refusal, Verb, issue_refusal
from meld.status import build_status

PREFIXES = ("worker", "model", "effort", "size")
NEEDS_HUMAN = "meld:needs-human"


def tag(ctx, number: int, *, worker: str | None = None, model: str | None = None, effort: str | None = None,
        size: str | None = None, skills: Sequence[str] | None = None, scope: Sequence[str] | None = None,
        may_delete: Sequence[str] | None = None, held: Sequence[str] | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.TAG, target, "issue is not in the active milestone"))
    refusal = issue_refusal(board, ctx.cfg, actor, Verb.TAG, issue, ctx.models)
    if refusal:
        refuse(ctx, board, refusal)
    wanted = {"worker": worker, "model": model, "effort": effort, "size": size}
    if not any(wanted.values()) and not skills and scope is None and may_delete is None and not held:
        refuse(ctx, board, Refusal(Verb.TAG, target,
                                    "provide at least one of --worker, --model, --effort, --size, --skill, --scope, --may-delete, --held"))
    if size is not None and size not in SIZES:
        refuse(ctx, board, Refusal(Verb.TAG, target, f"size '{size}' is not one of {', '.join(SIZES)}"))
    if skills is not None and any(not name for name in skills):
        refuse(ctx, board, Refusal(Verb.TAG, target, "--skill requires a non-empty name"))
    # One declaration is one `- ` bullet. An item carrying a line break would be written into the body as a bullet
    # plus a second line that is not one, so `body_section` reads back something other than what was declared.
    if any(not item.strip() or "\n" in item or "\r" in item for item in (*(scope or ()), *(may_delete or ()))):
        refuse(ctx, board, Refusal(Verb.TAG, target, "--scope and --may-delete require non-empty, single-line paths"))
    if held:
        for c in held:
            if c not in HELD_CATEGORIES:
                refuse(ctx, board, Refusal(Verb.TAG, target, f"unknown held category '{c}'; one of {', '.join(HELD_CATEGORIES)}"))
    if not actor.is_human:
        group = ctx.cfg.groups[actor.group]  # type: ignore[index]
        final_model = model or issue.model
        final_effort = effort or issue.effort
        if effort is not None and final_model is None:
            refuse(ctx, board, Refusal(Verb.TAG, target, f"effort '{effort}' cannot be validated without a model; pass --model too"))
        if (model or effort) and final_model and final_effort:
            errors = ctx.models.validate(group.cli, final_model, final_effort)
            if errors:
                refuse(ctx, board, Refusal(Verb.TAG, target, errors[0]))
        elif model and ctx.models.get(group.cli, model) is None:
            refuse(ctx, board, Refusal(Verb.TAG, target, f"model '{model}' unknown for adapter '{group.cli}'"))
        if worker is not None and worker not in actor.roster:
            refuse(ctx, board, Refusal(Verb.TAG, target, f"worker type '{worker}' is not in {group.name}'s roster"))
        if skills:
            allowed = {w.name for w in ctx.roster_entries() if w.source in ("skill", "product-skill")}
            for name in skills:
                if name not in allowed:
                    refuse(ctx, board, Refusal(Verb.TAG, target, f"skill '{name}' is not an installed or product skill for {group.name}"))
    all_held = sorted(set(issue.held) | set(held or ()))
    new_held = [c for c in dict.fromkeys(held or ()) if c not in issue.held]          # de-duplicated, order kept
    if new_held:
        # Only a category the issue does not carry yet re-gates dispatch (a newer NEEDS-HUMAN than the human's last answer),
        # so re-tagging one it already has posts neither the labels again nor a duplicate ask.
        #
        # The ask is the FIRST write of the whole call and is posted for every actor, human included (the ordering
        # `housekeeping.ci_red_sweep` uses). The labels are the gate and the ask is the stamp `Board.needs_human_answered`
        # compares an answer against: labels without an ask are a hold nothing can lift on purpose and that any earlier
        # answer lifts by accident, so a `GitHubError` here must leave nothing behind for the caller to retry past. A
        # human's own `--held` posts `[human] NEEDS-HUMAN #N` -- the human is the authority, but the hold still has to
        # be stamped, and it is how a human re-gates an issue they have already answered.
        ctx.gh.create_comment(board.coordination_issue, compose(
            Header(actor.party, "NEEDS-HUMAN", target, None),
            f"Held categories on #{number}: {', '.join(all_held)}. A listed human authorizes the worker with `RESOLVED #{number}` "
            f"(or `PROVIDED #{number}`); until then #{number} cannot be dispatched."))
    to_add = [f"{prefix}:{value}" for prefix, value in wanted.items() if value]
    to_add += [f"skill:{name}" for name in (skills or ())]
    for prefix, value in wanted.items():
        if value:
            for label in sorted(issue.labels):
                if label.startswith(f"{prefix}:") and label != f"{prefix}:{value}":
                    ctx.gh.remove_label(number, label)
    if to_add:
        ctx.gh.add_labels(number, to_add)
    if scope is not None or may_delete is not None:
        body = issue.body
        if scope is not None:
            body = replace_section(body, "Scope", scope)
        if may_delete is not None:
            body = replace_section(body, "May delete", may_delete)
        # Belt and braces on the guardrail itself: what was written must read back as the declaration asked for.
        for name, items in (("Scope", scope), ("May delete", may_delete)):
            if items is not None and body_section(body, name) != tuple(i.strip().strip("`").strip() for i in items):
                refuse(ctx, board, Refusal(Verb.TAG, target, f"the ## {name} declaration did not round-trip through the issue body; fix the body by hand"))
        ctx.gh.update_issue(number, body=body)
    if new_held:
        ctx.gh.add_labels(number, [held_label(c) for c in new_held] + [NEEDS_HUMAN])
    final = {prefix: (wanted[prefix] or getattr(issue, prefix)) for prefix in PREFIXES}
    final["skills"] = sorted(set(issue.skills) | set(skills or ()))
    return {"tagged": number, "labels": final,
            "scope": list(scope) if scope is not None else list(issue.scope),
            "may_delete": list(may_delete) if may_delete is not None else list(issue.may_delete),
            "held": all_held}
