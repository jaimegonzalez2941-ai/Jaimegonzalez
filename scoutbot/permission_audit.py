"""Read-only Discord role and channel permission audit."""

import sqlite3

import discord

from .access import AccessDenied, private_channel, safe_recruitment_role
from .domain import STAGES

SENSITIVE = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "manage_webhooks", "manage_messages", "manage_threads", "manage_nicknames",
    "kick_members", "ban_members", "moderate_members", "mention_everyone",
    "view_audit_log", "manage_events",
)

PERMISSION_LABELS = {
    "read_messages": "view_channel",
    "external_emojis": "use_external_emojis",
}


def true_permissions(permissions):
    return sorted({PERMISSION_LABELS.get(name, name) for name, enabled in permissions if enabled})


def setup_snapshot(path, guild_id):
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            conn.row_factory = sqlite3.Row
            settings = conn.execute(
                "SELECT * FROM guild_settings WHERE guild_id=?", (str(guild_id),)
            ).fetchone()
            stages = {
                row["stage"]: row["role_id"]
                for row in conn.execute(
                    "SELECT stage,role_id FROM stage_roles WHERE guild_id=?", (str(guild_id),)
                )
            }
    except sqlite3.Error:
        return None, {}
    return dict(settings) if settings else None, stages


def summarize_overwrites(channel):
    """List every explicit channel overwrite without enumerating guild members."""
    summaries = []
    for target, overwrite in channel.overwrites.items():
        allowed, denied = overwrite.pair()
        if isinstance(target, discord.Role):
            kind, name = "role", target.name
        elif isinstance(target, discord.Member):
            kind, name = "member", target.display_name
        elif isinstance(target, discord.Object):
            kind = "role" if target.type is discord.Role else "uncached-member"
            name = f"{kind} {target.id}"
        else:
            kind, name = "unknown", "unresolved permission target"
        summaries.append({
            "type": kind,
            "name": name,
            "id": str(target.id),
            "allows": true_permissions(allowed),
            "denies": true_permissions(denied),
        })
    return summaries


async def audit_permissions(token, settings, guild_id, actor_id):
    """Read every server role/channel override without enumerating members or writing data."""
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        actor = await guild.fetch_member(actor_id)
        if actor.bot or not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member must currently be a human server manager.")
        me = await guild.fetch_member(client.user.id)
        guild._add_member(me)
        roles = list(guild.roles)
        role_by_id = {role.id: role for role in roles}
        channels = await guild.fetch_channels()
        saved, stage_mappings = setup_snapshot(settings.database, guild.id)
        bot_role = me.top_role

        report = {
            "server": guild.name,
            "requester_verified_manage_server": True,
            "scope": "Live guild role permissions, role hierarchy, and channel overwrites; read-only.",
            "member_list_enumerated": False,
            "bot": {
                "name": me.display_name,
                "roles": [role.name for role in reversed(me.roles)],
                "server_permissions": true_permissions(me.guild_permissions),
                "role_hierarchy": bot_role.name,
                "manage_roles": me.guild_permissions.manage_roles,
                "manage_channels": me.guild_permissions.manage_channels,
            },
            "requester": {
                "name": actor.display_name,
                "roles": [role.name for role in reversed(actor.roles)],
                "server_permissions": true_permissions(actor.guild_permissions),
            },
            "role_count": len(roles),
            "roles": [],
            "sensitive_roles": [],
            "saved_tiers": {},
            "channel_count": len(channels),
            "channels_with_overrides": 0,
            "channels_without_explicit_everyone_deny": 0,
            "private_channels": [],
            "configured_setup": "not configured" if not saved else {},
            "staff_channel_check": "not configured" if not saved else "not checked",
            "limitations": [
                "Discord member rosters are not enumerated. Results cover every role permission and channel overwrite, not every member's combined permissions."
            ],
        }

        for role in sorted(roles, key=lambda item: (item.position, item.id), reverse=True):
            granted = true_permissions(role.permissions)
            sensitive = [name for name in SENSITIVE if getattr(role.permissions, name, False)]
            item = {
                "name": role.name,
                "position": role.position,
                "managed": role.managed,
                "above_bot": role >= bot_role and role.id != bot_role.id,
                "granted_permissions": granted,
                "sensitive_permissions": sensitive,
            }
            report["roles"].append(item)
            if sensitive:
                report["sensitive_roles"].append(item)

        for stage in STAGES:
            role_id = stage_mappings.get(stage)
            role = role_by_id.get(int(role_id)) if role_id and role_id.isdecimal() else None
            if role is None:
                report["saved_tiers"][stage] = {"mapped": False}
                continue
            try:
                safe_recruitment_role(role, actor)
                safe = True
                reason = None
            except AccessDenied as error:
                safe = False
                reason = str(error)
            report["saved_tiers"][stage] = {
                "mapped": True,
                "role": role.name,
                "position": role.position,
                "below_bot": role < bot_role,
                "safe_to_assign": safe,
                "reason": reason,
            }

        if saved:
            staff_role = role_by_id.get(int(saved["staff_role"]))
            staff_channel = next((channel for channel in channels if channel.id == int(saved["staff_channel"])), None)
            recap_channel = next((channel for channel in channels if channel.id == int(saved["recap_channel"])), None)
            source_id = int(saved["source_id"]) if str(saved["source_id"]).isdecimal() else None
            source = None
            source_status = "configured webhook ID"
            if saved["source_kind"] == "bot" and source_id:
                try:
                    source = await guild.fetch_member(source_id)
                    source_status = source.name if source.bot else "configured account is not a bot"
                except discord.NotFound:
                    source_status = "bot is not in this server"
                except discord.HTTPException:
                    source_status = "could not be verified"
            report["configured_setup"] = {
                "staff_role": staff_role.name if staff_role else "MISSING ROLE",
                "staff_channel": staff_channel.name if staff_channel else "MISSING CHANNEL",
                "recap_channel": recap_channel.name if recap_channel else "MISSING CHANNEL",
                "source_kind": saved["source_kind"],
                "source_bot": source_status,
                "bot_can_read_recap_history": bool(
                    recap_channel and
                    recap_channel.permissions_for(me).view_channel and
                    recap_channel.permissions_for(me).read_message_history
                ),
            }
            if staff_role is None or staff_channel is None:
                report["staff_channel_check"] = "BLOCKED: configured staff role or channel is missing."
            else:
                try:
                    await private_channel(staff_channel, staff_role)
                    report["staff_channel_check"] = "PASS: @everyone denied; staff viewers are verifiable; bot can view, send, and embed."
                except AccessDenied as error:
                    report["staff_channel_check"] = "BLOCKED: " + str(error).splitlines()[0]

        for channel in channels:
            if not isinstance(channel, discord.abc.GuildChannel):
                continue
            overrides = channel.overwrites
            if not overrides:
                continue
            report["channels_with_overrides"] += 1
            everyone = channel.overwrites_for(guild.default_role).view_channel
            if everyone is not False:
                report["channels_without_explicit_everyone_deny"] += 1
            private = everyone is False
            entry = {
                "channel": channel.name,
                "type": channel.type.name,
                "category": channel.category.name if getattr(channel, "category", None) else None,
                "everyone_view": "deny" if everyone is False else
                    "allow" if everyone is True else "inherit",
                "permission_targets": len(overrides),
                "overrides": summarize_overwrites(channel),
            }
            if private:
                entry["viewer_grants"] = [
                    {"type": override["type"], "name": override["name"]}
                    for override in entry["overrides"]
                    if "view_channel" in override["allows"]
                ]
                if channel.name.casefold() == "staff":
                    entry["configured_staff_check"] = report["staff_channel_check"]
                report["private_channels"].append(entry)
            report.setdefault("channel_override_inventory", []).append(entry)

        report["admin_access_notices"] = [
            "Sensitive role permissions are listed as findings, not assumed errors; the server owner decides whether each grant is intentional.",
            "Private channel viewer lists expose names only for explicit permission entries; no member roster or chat messages are read.",
            "Webhook existence and delivery cannot be verified from its numeric ID without its owning bot's live delivery; no webhook token is accessed.",
        ]
        return report


async def print_permission_audit(token, settings, guild_id, actor_id):
    import json
    report = await audit_permissions(token, settings, guild_id, actor_id)
    print(json.dumps({"READ_ONLY_AUDIT": report}, ensure_ascii=False))
