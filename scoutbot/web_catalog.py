"""Explicit web capability allowlist; fields come from existing command contracts."""

import discord

SCREENS = {
    "setup": ("scout setup", "scout status", "scout stage_role", "promotion policy", "schedule notifications"),
    "schedule": ("schedule create", "schedule edit", "schedule cancel", "schedule assign",
                 "schedule roster", "schedule show"),
    "availability": ("availability roster", "availability view", "availability leaves",
                     "availability review_leave"),
    "attendance": ("attendance mark", "attendance report", "promotion review"),
    "scouting": ("scout player", "scout history", "scout stats", "scout compare", "scout link",
                 "scout unlink", "scout ingest"),
    "recruitment": ("scout shortlist add", "scout shortlist list", "scout shortlist remove",
                    "promotion review", "scout assign"),
    "goals": ("development goal", "development edit", "development review", "development complete"),
    "tasks": ("assistant task add", "assistant task edit", "assistant task list", "assistant task complete"),
    "briefing": ("assistant brief",),
}
MANAGER = {"scout setup", "scout stage_role", "promotion policy"}
CONFIRM = {"schedule cancel", "scout unlink", "scout shortlist remove", "scout assign",
           "availability review_leave", "schedule edit", "development complete", "assistant task complete"}
READS = {"scout status", "schedule roster", "schedule show", "availability roster",
         "availability view", "availability leaves", "attendance report", "promotion review",
         "scout player", "scout history", "scout stats", "scout compare", "scout shortlist list",
         "development review", "assistant task list", "assistant brief"}
LABELS = {
    "scout setup": "Configure staff access & recap source", "scout status": "Verify setup & recap diagnostics",
    "scout stage_role": "Map recruitment tier", "promotion policy": "Adjust attendance standard",
    "schedule notifications": "Configure session notifications", "schedule create": "Plan a session",
    "schedule edit": "Edit session & review commitments", "schedule cancel": "Cancel session",
    "schedule assign": "Assign or release a player", "schedule roster": "Review roster & leave conflicts",
    "schedule show": "Read session details", "availability review_leave": "Review leave decision",
    "attendance mark": "Record or correct attendance", "attendance report": "Read attendance report",
    "promotion review": "Review promotion evidence", "scout player": "Open player dossier",
    "scout history": "Read saved evaluations", "scout stats": "Read trusted match evidence",
    "scout compare": "Compare position evidence", "scout link": "Link EA handle",
    "scout unlink": "Unlink handle (keep statistics)", "scout ingest": "Import trusted text recap",
    "scout shortlist add": "Save recruitment follow-up", "scout shortlist remove": "Archive follow-up",
    "development goal": "Set observable development goal", "development edit": "Update goal owner or deadline",
    "development complete": "Record goal outcome", "assistant task add": "Assign staff task",
    "assistant task edit": "Update task owner or deadline", "assistant task complete": "Complete staff task",
    "assistant brief": "Generate on-demand briefing",
}


def command_for(bot, name):
    current = bot.tree.get_command(name.split()[0])
    for part in name.split()[1:]:
        current = current.get_command(part) if current else None
    if not isinstance(current, discord.app_commands.Command):
        raise ValueError("This operation is unavailable in the running bot.")
    return current


def field(parameter):
    kind = {
        discord.AppCommandOptionType.integer: "integer",
        discord.AppCommandOptionType.number: "number",
        discord.AppCommandOptionType.boolean: "boolean",
        discord.AppCommandOptionType.user: "member",
        discord.AppCommandOptionType.role: "role",
        discord.AppCommandOptionType.channel: "channel",
    }.get(parameter.type, "text")
    result = {"name": parameter.name, "label": parameter.name.replace("_", " ").capitalize(),
              "type": kind, "required": parameter.required,
              "choices": [{"label": c.name, "value": str(c.value)} for c in parameter.choices]}
    for key, value in (("minimum", parameter.min_value), ("maximum", parameter.max_value)):
        if value is not None:
            result[key] = value
    if kind in {"member", "role", "channel"}:
        result["label"] += " Discord ID"
    return result


def operations(bot, screen, manager):
    return [{"id": name, "label": LABELS.get(name, name.replace("_", " ").title()),
             "description": command_for(bot, name).description,
             "fields": [field(p) for p in command_for(bot, name).parameters if p.name != "confirm"],
             "confirmation": name in CONFIRM}
            for name in SCREENS.get(screen, ()) if manager or name not in MANAGER]
