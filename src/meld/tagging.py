"""meld tag: the orchestrator (or a human) sets worker, model, effort, and size labels on an issue."""
from __future__ import annotations

from meld.actions import refuse
from meld.models import SIZES
from meld.rules import Refusal, Verb
from meld.status import build_status

PREFIXES = ("worker", "model", "effort", "size")


def tag(ctx, number: int, *, worker: str | None = None, model: str | None = None, effort: str | None = None, size: str | None = None) -> dict:
    _, board = build_status(ctx)
    actor = ctx.actor()
    target = f"#{number}"
    if not (actor.is_human or actor.party.kind == "group"):
        refuse(ctx, board, Refusal(Verb.TAG, target, "only the orchestrator or a human may tag"))
    issue = board.issues.get(number)
    if issue is None:
        refuse(ctx, board, Refusal(Verb.TAG, target, "issue is not in the active milestone"))
    wanted = {"worker": worker, "model": model, "effort": effort, "size": size}
    if not any(wanted.values()):
        refuse(ctx, board, Refusal(Verb.TAG, target, "provide at least one of --worker, --model, --effort, --size"))
    if size is not None and size not in SIZES:
        refuse(ctx, board, Refusal(Verb.TAG, target, f"size '{size}' is not one of {', '.join(SIZES)}"))
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
    to_add = [f"{prefix}:{value}" for prefix, value in wanted.items() if value]
    for prefix, value in wanted.items():
        if value:
            for label in sorted(issue.labels):
                if label.startswith(f"{prefix}:") and label != f"{prefix}:{value}":
                    ctx.gh.remove_label(number, label)
    ctx.gh.add_labels(number, to_add)
    final = {prefix: (wanted[prefix] or getattr(issue, prefix)) for prefix in PREFIXES}
    return {"tagged": number, "labels": final}
