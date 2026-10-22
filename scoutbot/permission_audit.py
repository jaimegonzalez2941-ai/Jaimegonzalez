"""Discord permission audit and narrowly scoped, manager-approved repairs."""

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


def without_view_channel(overwrite):
    updated = discord.PermissionOverwrite.from_pair(*overwrite.pair())
    updated.view_channel = None
    return updated


def other_overwrite_bits_unchanged(before, after):
    view_channel_mask = discord.Permissions.VALID_FLAGS["view_channel"]
    before_allow, before_deny = before.pair()
    after_allow, after_deny = after.pair()
    return (
        (before_allow.value & ~view_channel_mask) == (after_allow.value & ~view_channel_mask)
        and (before_deny.value & ~view_channel_mask) == (after_deny.value & ~view_channel_mask)
    )


def member_tier_overlap(member, tier_roles):
    tier_ids = {role.id for role in tier_roles}
    return sorted({role.name for role in member.roles if role.id in tier_ids})


def require_exact_overlap_names(found_names, expected_names):
    if (
        len(expected_names) != len(set(expected_names))
        or len(found_names) != len(expected_names)
        or set(found_names) != set(expected_names)
    ):
        raise ValueError("The current individual access list differs from the approved accounts; no grants were changed.")


async def audit_report_member_overlaps(token, guild_id, actor_id):
    """Read only explicit #staff-reports member viewers and check their current tier roles."""
    tier_role_names = ("⏳ Trialist", "⚽ Dev Squad")
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        actor = await guild.fetch_member(actor_id)
        if actor.bot or not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member must currently be a human server manager.")

        channels = await guild.fetch_channels()
        matches = [
            channel for channel in channels
            if isinstance(channel, discord.TextChannel) and channel.name.casefold() == "staff-reports"
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one text channel named #staff-reports.")
        channel = matches[0]
        roles_by_name = {}
        for role_name in tier_role_names:
            role_matches = [role for role in guild.roles if role.name == role_name]
            if len(role_matches) != 1:
                raise ValueError(f"Expected exactly one server role named {role_name}.")
            roles_by_name[role_name] = role_matches[0]

        tier_roles = list(roles_by_name.values())
        report = {
            "channel": channel.name,
            "member_roster_enumerated": False,
            "role_overrides": {
                name: (
                    "allow" if channel.overwrites_for(role).view_channel is True else
                    "deny" if channel.overwrites_for(role).view_channel is False else "inherit"
                )
                for name, role in roles_by_name.items()
            },
            "explicit_member_view_grants_checked": 0,
            "verified_member_overrides": 0,
            "unverified_member_overrides": 0,
            "former_or_missing_members": 0,
            "tier_role_overlaps": [],
        }

        seen_ids = set()
        member_ids = []
        for target, overwrite in channel.overwrites.items():
            if overwrite.view_channel is not True:
                continue
            if isinstance(target, discord.Member):
                member_id = target.id
            elif isinstance(target, discord.Object) and target.type is not discord.Role:
                member_id = target.id
            else:
                continue
            if member_id not in seen_ids:
                seen_ids.add(member_id)
                member_ids.append(member_id)

        report["explicit_member_view_grants_checked"] = len(member_ids)
        for member_id in member_ids:
            try:
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                report["former_or_missing_members"] += 1
                continue
            except discord.HTTPException:
                report["unverified_member_overrides"] += 1
                continue

            report["verified_member_overrides"] += 1
            overlap = member_tier_overlap(member, tier_roles)
            if overlap:
                report["tier_role_overlaps"].append({
                    "name": member.display_name,
                    "tier_roles": overlap,
                    "effective_view_channel": bool(channel.permissions_for(member).view_channel),
                })

        if report["tier_role_overlaps"]:
            report["summary"] = "One or more explicit member viewers also have a restricted tier role."
        elif report["unverified_member_overrides"]:
            report["summary"] = "No overlap found among verified members; some explicit viewers could not be checked."
        else:
            report["summary"] = "No verified explicit member viewer has either tier role."
        return report


async def remove_report_member_overlaps(token, guild_id, actor_id, expected_names):
    """Remove approved individual grants only when live names and tier membership still match."""
    if not expected_names or len(expected_names) != len(set(expected_names)):
        raise ValueError("Approved member names must be a non-empty unique list.")
    reason = "Manager-approved: remove individual access for restricted development-tier members"
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        actor = await guild.fetch_member(actor_id)
        if actor.bot or not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member must currently be a human server manager.")

        channels = await guild.fetch_channels()
        matches = [
            channel for channel in channels
            if isinstance(channel, discord.TextChannel) and channel.name.casefold() == "staff-reports"
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one text channel named #staff-reports; no permissions were changed.")
        channel = matches[0]
        if channel.category is not None:
            raise ValueError("#staff-reports now inherits category permissions; no permissions were changed.")
        if channel.overwrites_for(guild.default_role).view_channel is not False:
            raise ValueError("#staff-reports no longer explicitly denies @everyone; no permissions were changed.")

        tier_role_names = ("⏳ Trialist", "⚽ Dev Squad")
        tier_roles = []
        for role_name in tier_role_names:
            role_matches = [role for role in guild.roles if role.name == role_name]
            if len(role_matches) != 1:
                raise ValueError(f"Expected exactly one server role named {role_name}; no permissions were changed.")
            tier_roles.append(role_matches[0])

        direct_member_ids = []
        for target, overwrite in channel.overwrites.items():
            if overwrite.view_channel is not True:
                continue
            if isinstance(target, discord.Member):
                direct_member_ids.append(target.id)
            elif isinstance(target, discord.Object) and target.type is not discord.Role:
                direct_member_ids.append(target.id)

        overlaps = []
        seen_ids = set()
        for member_id in direct_member_ids:
            if member_id in seen_ids:
                continue
            seen_ids.add(member_id)
            try:
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                continue
            except discord.HTTPException:
                raise ValueError("Could not verify every explicit member viewer; no grants were changed.") from None
            if member_tier_overlap(member, tier_roles):
                overlaps.append(member)

        require_exact_overlap_names(
            [member.display_name for member in overlaps],
            expected_names,
        )
        for member in overlaps:
            if channel.overwrites_for(member).view_channel is not True:
                raise ValueError("An approved member no longer has an explicit allow; no grants were changed.")

        results = []
        for member in overlaps:
            before = channel.overwrites_for(member)
            try:
                await channel.set_permissions(
                    member,
                    overwrite=without_view_channel(before),
                    reason=reason,
                )
            except discord.HTTPException:
                # Verify live state before treating an API error as a failed or successful write.
                pass

            try:
                refreshed = await client.fetch_channel(channel.id)
                current_member = await guild.fetch_member(member.id)
            except discord.HTTPException:
                results.append({
                    "name": member.display_name,
                    "status": "unverified; check the individual overwrite before retrying",
                    "effective_view_channel": "unknown",
                    "other_permissions_preserved": False,
                })
                break

            after = refreshed.overwrites_for(current_member)
            preserved = other_overwrite_bits_unchanged(before, after)
            if after.view_channel is None and preserved:
                still_can_view = bool(refreshed.permissions_for(current_member).view_channel)
                results.append({
                    "name": current_member.display_name,
                    "status": "personal grant removed and verified",
                    "effective_view_channel": still_can_view,
                    "other_permissions_preserved": True,
                })
            else:
                results.append({
                    "name": current_member.display_name,
                    "status": "not fully verified; inspect before retrying",
                    "effective_view_channel": bool(refreshed.permissions_for(current_member).view_channel),
                    "other_permissions_preserved": preserved,
                })
                break

        complete = len(results) == len(overlaps) and all(
            item["status"] == "personal grant removed and verified" for item in results
        )
        return {
            "channel": channel.name,
            "status": "complete" if complete else "partial or unverified",
            "results": results,
            "other_channel_overrides_changed": False,
            "posts_deleted": False,
        }


async def remove_report_tier_access(token, guild_id, actor_id):
    """Remove only two approved tier-role View Channel grants from #staff-reports."""
    tier_role_names = ("⏳ Trialist", "⚽ Dev Squad")
    reason = "Manager-approved: remove development-tier access to staff reports"
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        actor = await guild.fetch_member(actor_id)
        if actor.bot or not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member must currently be a human server manager.")

        channels = await guild.fetch_channels()
        matches = [
            channel for channel in channels
            if isinstance(channel, discord.TextChannel) and channel.name.casefold() == "staff-reports"
        ]
        if len(matches) != 1:
            raise ValueError("Expected exactly one text channel named #staff-reports; no permissions were changed.")
        channel = matches[0]
        if channel.category is not None:
            raise ValueError("#staff-reports now inherits category permissions; no permissions were changed.")
        if channel.overwrites_for(guild.default_role).view_channel is not False:
            raise ValueError("#staff-reports no longer explicitly denies @everyone; no permissions were changed.")

        roles_by_name = {}
        for role_name in tier_role_names:
            role_matches = [role for role in guild.roles if role.name == role_name]
            if len(role_matches) != 1:
                raise ValueError(
                    f"Expected exactly one server role named {role_name}; no permissions were changed."
                )
            roles_by_name[role_name] = role_matches[0]

        results = []
        for role_name, role in roles_by_name.items():
            before = channel.overwrites_for(role)
            if before.view_channel is not True:
                results.append({
                    "role": role_name,
                    "status": "already restricted" if before.view_channel is False else "no direct grant",
                    "view_channel": "deny" if before.view_channel is False else "inherit",
                    "other_permissions_preserved": True,
                })
                continue

            try:
                await channel.set_permissions(role, overwrite=without_view_channel(before), reason=reason)
            except discord.HTTPException:
                # A failed response may follow a successful write. Read live state before deciding.
                pass

            try:
                refreshed = await client.fetch_channel(channel.id)
            except discord.HTTPException:
                results.append({
                    "role": role_name,
                    "status": "unverified; check this role's overwrite in Discord before retrying",
                    "view_channel": "unknown",
                    "other_permissions_preserved": False,
                })
                break

            after = refreshed.overwrites_for(role)
            preserved = other_overwrite_bits_unchanged(before, after)
            if after.view_channel is None and preserved:
                results.append({
                    "role": role_name,
                    "status": "removed and verified",
                    "view_channel": "inherit",
                    "other_permissions_preserved": True,
                })
            else:
                results.append({
                    "role": role_name,
                    "status": "not fully verified; inspect before retrying",
                    "view_channel": "allow" if after.view_channel is True else "deny",
                    "other_permissions_preserved": preserved,
                })
                break

        changed_or_verified = all(
            result["status"] in ("removed and verified", "already restricted", "no direct grant")
            for result in results
        )
        return {
            "channel": channel.name,
            "status": "complete" if changed_or_verified and len(results) == len(tier_role_names) else "partial or unverified",
            "results": results,
            "other_channel_overrides_changed": False,
            "posts_deleted": False,
        }


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


async def print_report_tier_access_change(token, guild_id, actor_id):
    import json
    report = await remove_report_tier_access(token, guild_id, actor_id)
    print(json.dumps({"REPORT_ACCESS_CHANGE": report}, ensure_ascii=False))


async def print_report_member_overlap_audit(token, guild_id, actor_id):
    import json
    report = await audit_report_member_overlaps(token, guild_id, actor_id)
    print(json.dumps({"REPORT_MEMBER_OVERLAP_AUDIT": report}, ensure_ascii=False))


async def print_report_member_access_change(token, guild_id, actor_id, expected_names):
    import json
    report = await remove_report_member_overlaps(token, guild_id, actor_id, expected_names)
    print(json.dumps({"REPORT_MEMBER_ACCESS_CHANGE": report}, ensure_ascii=False))
