"""Trusted-workspace setup maintenance; never expose this module as a web endpoint."""

import asyncio
import json

import discord

from .access import private_channel, safe_recruitment_role
from .database import Database
from .domain import STAGES


def validate_manifest(manifest):
    required = {"guild_id", "actor_id", "staff_role", "staff_channel", "recap_channel", "source_bot", "stages"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("Supply exactly the documented setup fields.")
    if not isinstance(manifest["stages"], dict) or set(manifest["stages"]) != set(STAGES):
        raise ValueError("Supply all three development-stage role mappings.")
    for value in [manifest[k] for k in required if k != "stages"] + list(manifest["stages"].values()):
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
            raise ValueError("Setup resource IDs must be positive numeric strings.")
    if len(set(manifest["stages"].values())) != 3:
        raise ValueError("Use a different Discord role for each development tier.")
    return manifest


async def persist_setup(db, guild, actor, values, stages):
    """Fill missing settings atomically; never overwrite conflicting saved choices."""
    columns = ("staff_role", "staff_channel", "recap_channel", "source_kind", "source_id")
    async with db.transaction() as conn:
        async with conn.execute("SELECT * FROM guild_settings WHERE guild_id=?", (str(guild),)) as cursor:
            old = await cursor.fetchone()
        if old and any(str(old[key]) != str(values[key]) for key in columns):
            raise ValueError("Saved setup differs from the requested setup; no settings were changed.")
        async with conn.execute("SELECT stage,role_id FROM stage_roles WHERE guild_id=?", (str(guild),)) as cursor:
            saved = {row["stage"]: row["role_id"] for row in await cursor.fetchall()}
        if any(stage in saved and saved[stage] != str(role) for stage, role in stages.items()):
            raise ValueError("A saved tier mapping differs; no settings were changed.")
        if not old:
            await conn.execute(
                "INSERT INTO guild_settings(guild_id,staff_role,staff_channel,recap_channel,source_kind,source_id) "
                "VALUES (?,?,?,?,?,?)",
                (str(guild), *(str(values[key]) for key in columns)),
            )
            await db._audit(conn, guild, actor, "settings.updated", values)
        for stage, role in stages.items():
            if stage not in saved:
                await conn.execute("INSERT INTO stage_roles(guild_id,stage,role_id) VALUES (?,?,?)",
                                   (str(guild), stage, str(role)))
                await db._audit(conn, guild, actor, "stage.configured", {"stage": stage, "role": str(role)})


async def apply_setup(token, settings, manifest):
    manifest = validate_manifest(manifest)
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(int(manifest["guild_id"]))
        actor = await guild.fetch_member(int(manifest["actor_id"]))
        if actor.bot or not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member must currently be a human server manager.")
        me = await guild.fetch_member(client.user.id)
        guild._add_member(me)
        channels = {channel.id: channel for channel in await guild.fetch_channels()}
        staff = guild.get_role(int(manifest["staff_role"]))
        staff_channel = channels.get(int(manifest["staff_channel"]))
        recap_channel = channels.get(int(manifest["recap_channel"]))
        if staff is None or staff.is_default() or not all(
            isinstance(channel, discord.TextChannel) for channel in (staff_channel, recap_channel)
        ):
            raise ValueError("Choose an existing dedicated staff role and two server text channels.")
        source = await guild.fetch_member(int(manifest["source_bot"]))
        if not source.bot or source.id == me.id:
            raise ValueError("The source must be another bot currently in this server.")
        await private_channel(staff_channel, staff)
        if not staff_channel.permissions_for(staff).view_channel:
            raise ValueError("The selected staff role cannot view the staff channel.")
        recap_permissions = recap_channel.permissions_for(me)
        if not recap_permissions.view_channel or not recap_permissions.read_message_history:
            raise ValueError("Club Agent cannot read the selected recap channel.")
        for role_id in manifest["stages"].values():
            role = guild.get_role(int(role_id))
            if role is None or role == staff:
                raise ValueError("Each development tier needs an existing non-staff role.")
            safe_recruitment_role(role, actor)
        values = {key: manifest[key] for key in ("staff_role", "staff_channel", "recap_channel")}
        values.update(source_kind="bot", source_id=manifest["source_bot"])
        db = Database(settings.database)
        try:
            await db.open()
            await persist_setup(db, guild.id, actor.id, values, manifest["stages"])
            stored = await db.settings(guild.id)
            mappings = await db.all("SELECT stage,role_id FROM stage_roles WHERE guild_id=?", (str(guild.id),))
            return {"saved": True, "staff_channel": staff_channel.name, "recap_channel": recap_channel.name,
                    "source": source.name, "stages": {row["stage"]: row["role_id"] for row in mappings},
                    "settings_verified": all(stored[key] == str(value) for key, value in values.items()),
                    "discord_permissions_changed": False, "player_roles_changed": False}
        finally:
            await db.close()


async def inspect_setup(token, guild_id, actor_id):
    """Inspect only setup resources; never connect a second gateway or expose tokens."""
    async with discord.Client(intents=discord.Intents.none()) as client:
        await client.login(token)
        guild = await client.fetch_guild(guild_id)
        actor = await guild.fetch_member(actor_id)
        if not actor.guild_permissions.manage_guild:
            raise ValueError("The requesting member does not currently have Manage Server.")
        me = await guild.fetch_member(client.user.id)
        guild._add_member(me)
        channels = await guild.fetch_channels()
        candidates = [c for c in channels if isinstance(c, discord.TextChannel)
                      and any(word in c.name.lower() for word in ("staff", "main", "stats", "recap"))]
        result = {
            "guild": {"id": str(guild.id), "name": guild.name},
            "actor_verified": True,
            "bot_manage_roles": me.guild_permissions.manage_roles,
            "roles": [{"id": str(r.id), "name": r.name, "managed": r.managed,
                       "manager": r.permissions.manage_guild, "administrator": r.permissions.administrator,
                       "below_bot": r < me.top_role}
                      for r in guild.roles if any(word in r.name.lower()
                                                for word in ("coach", "owner", "staff", "trial", "prospect", "first", "academia"))],
            "channels": [],
        }
        async with asyncio.timeout(25):
            for channel in candidates:
                perms = channel.permissions_for(me)
                item = {"id": str(channel.id), "name": channel.name,
                        "everyone_view_override": channel.overwrites_for(guild.default_role).view_channel,
                        "bot_view": perms.view_channel, "bot_send": perms.send_messages,
                        "bot_embed": perms.embed_links, "bot_read_history": perms.read_message_history,
                        "bot_manage_roles": perms.manage_roles}
                if channel.name.lower() == "staff":
                    item["explicit_viewers"] = []
                    for target, overwrite in channel.overwrites.items():
                        if overwrite.view_channel is not True:
                            continue
                        if isinstance(target, discord.Object) and target.type in (
                            discord.abc.User, discord.User, discord.Member,
                        ):
                            try:
                                target = await guild.fetch_member(target.id)
                            except discord.NotFound:
                                item["explicit_viewers"].append({"id": str(target.id), "departed": True})
                                continue
                        item["explicit_viewers"].append({
                            "id": str(target.id), "name": getattr(target, "name", None),
                            "kind": "role" if isinstance(target, discord.Role) else "member",
                            "bot": target.bot if isinstance(target, discord.Member) else None,
                            "manager": target.guild_permissions.manage_guild
                            if isinstance(target, discord.Member) else None,
                            "roles": [str(r.id) for r in target.roles]
                            if isinstance(target, discord.Member) else None,
                        })
                elif perms.view_channel and perms.read_message_history and (
                    "main" in channel.name.lower() or channel.name.lower() == "stats"
                ):
                    # Identify the report destination without returning human chat.
                    item["recent_bot_posts"] = []
                    async for message in channel.history(limit=50):
                        if not message.author.bot:
                            continue
                        if not (
                            " · " in message.content and "match" in message.content.lower()
                            or any("leaderboard" in (e.title or "").lower() for e in message.embeds)
                        ):
                            continue
                        item["recent_bot_posts"].append({
                            "author_id": str(message.author.id), "author": message.author.name,
                            "caption": message.content[:180],
                            "embed_titles": [e.title for e in message.embeds[:2]],
                            "attachment_count": len(message.attachments),
                        })
                        if len(item["recent_bot_posts"]) >= 4:
                            break
                result["channels"].append(item)
        return result


async def report_setup(token, guild_id, actor_id):
    print(json.dumps(await inspect_setup(token, guild_id, actor_id), ensure_ascii=False))
