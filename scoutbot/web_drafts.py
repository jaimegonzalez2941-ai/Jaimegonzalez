"""Website scorecard actions use the same private drafts and rubric validation."""

import json

from .domain import POSITIONS
from .rubric import CATEGORIES, LABELS, OBSERVABLES, WEIGHTS, ANCHORS, calculate


def text_field(name, label=None, required=True, kind="text"):
    return {"name": name, "label": label or name.replace("_", " ").capitalize(),
            "type": kind, "required": required}


def draft_operations():
    identity = [text_field("id", "Private draft ID"), text_field("revision", "Current revision", kind="integer")]
    return [
        {"id": "draft.start", "label": "Start or resume evaluation", "description": "Your own private scorecard; saved history is unchanged until confirmation.",
         "fields": [text_field("candidate", "Player Discord ID", kind="member"),
                    {**text_field("position"), "choices": [{"label": p, "value": p} for p in POSITIONS]}],
         "confirmation": False},
        {"id": "draft.read", "label": "Reopen private draft", "description": "Read your latest saved draft revision.",
         "fields": [text_field("id", "Private draft ID")], "confirmation": False},
        {"id": "draft.correct", "label": "Update private draft", "description": "Save category evidence and context without publishing.",
         "fields": identity + [text_field("payload", "Scorecard", kind="scorecard")], "confirmation": False},
        {"id": "draft.save", "label": "Confirm & save evaluation", "description": "All categories, evidence, context and declared coverage required. Borderline 6.8–7.2 results cannot be published.",
         "fields": identity, "confirmation": True},
        {"id": "draft.discard", "label": "Discard private draft", "description": "Closes this draft; never deletes historical evaluations.",
         "fields": identity, "confirmation": True},
    ]


def present_draft(row):
    row = dict(row)
    row["payload"] = json.loads(row["payload"])
    row["categories"] = [{"key": key, "label": label, "guidance": guide, "weight": weight}
                         for key, label, guide, weight in zip(CATEGORIES, LABELS,
                                                             OBSERVABLES[row["position"]], WEIGHTS[row["position"]])]
    row["anchors"] = ANCHORS
    result = calculate(row["position"], row["payload"]["categories"], row["rubric_version"])
    row["result"] = ({"score": str(result.score), "raw": str(result.raw)} if result else None)
    return row


async def draft_action(bot, interaction, operation, values, confirmed):
    db, guild, actor = bot.db, interaction.guild_id, interaction.user.id
    if operation == "draft.start":
        identifier = str(values.get("candidate", ""))
        if not identifier.isdecimal() or not 15 <= len(identifier) <= 22:
            raise ValueError("Enter the player's exact Discord ID.")
        candidate = await interaction.guild.fetch_member(int(identifier))
        if candidate.bot or values.get("position") not in POSITIONS:
            raise ValueError("Choose a player and supported position.")
        row = await db.start_draft(guild, candidate.id, candidate.display_name, actor,
                                   interaction.user.display_name, values["position"])
    else:
        row = await db.draft(guild, actor, str(values.get("id", "")))
        if operation == "draft.save" and row["status"] == "saved" and confirmed:
            return {"message": "This evaluation is already saved. No duplicate record was created.",
                    "blocks": [], "draft": present_draft(row)}
        if operation != "draft.read":
            revision = values.get("revision")
            if isinstance(revision, bool) or str(revision) != str(row["revision"]):
                raise ValueError("Draft changed. Reopen it before editing or confirming.")
            action = operation.split(".")[1]
            if action in {"save", "discard"} and not confirmed:
                raise ValueError("Explicit confirmation is required.")
            payload = values.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    raise ValueError("The scorecard could not be read. Reopen the draft.") from None
            await db.change_draft(guild, actor, row["id"], row["revision"], payload=payload, action=action)
            row = await db.draft(guild, actor, row["id"])
    return {"message": f"Evaluation draft is {row['status']}. Saved records remain private.",
            "blocks": [], "draft": present_draft(row)}
