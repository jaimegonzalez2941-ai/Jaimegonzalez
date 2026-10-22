"""Local-only authenticated application boundary owned by the running Discord bot.

No TCP listener, raw SQL endpoint, supplied actor IDs, or separate SQLite lifecycle.
The filesystem restricts the Unix socket to the same service account as Express.
Clerk verification and same-origin CSRF checks happen before Express calls it.
"""

import asyncio
from contextlib import suppress
import json
import logging
import os
from pathlib import Path
import secrets
import time

import discord

from .access import AccessDenied
from .web_catalog import CONFIRM, MANAGER, READS, SCREENS
from .web_context import current_context, current_member_context, invoke
from .web_drafts import draft_action
from .web_identity import IdentityLinks, WEB_SCHEMA
from .web_workspace import workspace, verify_setup, player_development, share_player_goal

logger = logging.getLogger(__name__)
MAX_BYTES = 128 * 1024


def socket_path():
    return Path(os.getenv("CLUB_WEB_SOCKET", ".data/club-web/bridge.sock"))


class WebBridge:
    def __init__(self, bot):
        self.bot = bot
        self.identities = IdentityLinks(bot.db)
        self.server = None
        self.path = None
        self.snapshots = {}
        self.actor_locks = {}
        self.rates = {}

    async def start(self):
        path = socket_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        if path.exists():
            # A live socket belongs to another bot; don't disconnect it.
            try:
                reader, writer = await asyncio.open_unix_connection(str(path))
            except (ConnectionRefusedError, FileNotFoundError):
                path.unlink(missing_ok=True)
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError("Another bot already owns the website bridge.")
        await self.bot.db.conn.executescript(WEB_SCHEMA)
        self.server = await asyncio.start_unix_server(self.handle, path=str(path), limit=MAX_BYTES)
        self.path = path
        os.chmod(path, 0o600)
        logger.info("Website application boundary ready (local Unix socket only).")

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            self.path.unlink(missing_ok=True)
        for item in self.snapshots.values():
            view = item["view"]
            if hasattr(view, "source"):
                await view.on_timeout()
        self.snapshots.clear()

    async def cleanup(self):
        now = time.monotonic()
        for key, item in tuple(self.snapshots.items()):
            if item["expires"] <= now:
                self.snapshots.pop(key, None)
                if hasattr(item["view"], "source"):
                    await item["view"].on_timeout()
        self.rates = {k: v for k, v in self.rates.items() if now - v[0] < 60}

    async def handle(self, reader, writer):
        try:
            async with asyncio.timeout(45):
                data = await reader.readline()
                if not data or len(data) >= MAX_BYTES:
                    raise ValueError("Request is too large.")
                request = json.loads(data)
                if not isinstance(request, dict):
                    raise ValueError("Invalid application request.")
                response = {"ok": True, "data": await self.dispatch(request)}
        except AccessDenied as exc:
            response = {"ok": False, "status": 403, "error": str(exc)}
        except (ValueError, discord.app_commands.CommandOnCooldown) as exc:
            response = {"ok": False, "status": 400, "error": str(exc)}
        except discord.NotFound:
            response = {"ok": False, "status": 403, "error": "Discord membership or the selected item is unavailable. Refresh server access."}
        except discord.Forbidden:
            response = {"ok": False, "status": 403, "error": "Discord denied access. Ask a manager to inspect the selected item and bot permissions; do not grant Administrator."}
        except (ConnectionError, TimeoutError, discord.HTTPException):
            response = {"ok": False, "status": 503, "error":
                        "Discord could not confirm this request. Check existing records or roles before retrying; an interrupted request may have completed."}
        except Exception:
            reference = secrets.token_hex(4)
            logger.exception("Website operation failed reference=%s", reference)
            response = {"ok": False, "status": 500, "error":
                        f"Action could not be confirmed. Check existing records before retrying. Operator reference: {reference}"}
        try:
            writer.write(json.dumps(response).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            # Never repeat the operation after a response delivery failure.
            logger.warning("Website response delivery interrupted; operation not retried.")
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    def rate_limit(self, subject):
        now = time.monotonic()
        start, count = self.rates.get(subject, (now, 0))
        if now - start > 60:
            start, count = now, 0
        if count >= 90:
            raise ValueError("Too many requests. Wait a minute before refreshing.")
        self.rates[subject] = (start, count + 1)

    async def dispatch(self, request):
        await self.cleanup()
        kind = request.get("kind")
        if kind == "public":
            connected = self.bot.is_ready()
            permissions = discord.Permissions(view_channel=True, send_messages=True, embed_links=True,
                                               read_message_history=True, manage_roles=True)
            return {"connected": connected, "installUrl":
                    discord.utils.oauth_url(self.bot.user.id, permissions=permissions,
                                            scopes=("bot", "applications.commands"))
                    if self.bot.user else None,
                    "message": "Bot connected" if connected else "Bot disconnected; live services unavailable"}
        subject = request.get("subject")
        if not isinstance(subject, str) or not subject.startswith("user_") or len(subject) > 200:
            raise AccessDenied("Verified website authentication is required.")
        self.rate_limit(subject)
        if kind == "link":
            return await self.identities.challenge(subject)
        actor_id = await self.identities.actor_id(subject)
        if kind in {"access", "player.access"}:
            if actor_id is None:
                return {"linked": False, "servers": []}
            if not self.bot.is_ready():
                raise ConnectionError("Discord is unavailable.")
            # Only bot guild IDs are enumerated; individual current actor REST
            # checks never enumerate guild members or trust cached actor roles.
            guilds = sorted(self.bot.guilds, key=lambda g: g.id)
            if len(guilds) > 200:
                raise ValueError("Server discovery requires a bounded installation directory; contact the operator.")
            servers = []
            name = "Verified Discord account"
            for guild in guilds:
                try:
                    authorize = current_member_context if kind == "player.access" else current_context
                    interaction = await authorize(self.bot, guild.id, actor_id)
                except (AccessDenied, discord.NotFound, discord.Forbidden):
                    continue
                name = interaction.user.display_name
                server = {"id": str(guild.id), "name": interaction.guild.name}
                if kind == "access":
                    server["manager"] = interaction.user.guild_permissions.manage_guild
                servers.append(server)
            return {"linked": True, "discordName": name, "servers": servers}
        if actor_id is None:
            raise AccessDenied("Verify your own Discord account before opening private club pages.")
        guild = str(request.get("guild", ""))
        if not guild.isdecimal() or not 15 <= len(guild) <= 22:
            raise ValueError("Choose an authorized server.")
        if kind == "player.development":
            interaction = await current_member_context(self.bot, guild, actor_id)
            return await player_development(self.bot, interaction, request.get("params", {}))
        interaction = await current_context(self.bot, guild, actor_id)
        if kind == "workspace":
            params = request.get("params", {})
            if params.get("screen") == "snapshot":
                return await self.snapshot_page(subject, guild, interaction, params)
            return await workspace(self.bot, interaction, params)
        if kind != "action":
            raise ValueError("Unknown application operation.")
        operation, values = request.get("operation"), request.get("values")
        if not isinstance(values, dict):
            raise ValueError("Operation fields must be an object.")
        confirmed = request.get("confirmed") is True
        if operation in {"goal.share", "goal.unshare"}:
            return await share_player_goal(self.bot, interaction, operation, values, confirmed)
        if operation in CONFIRM and not confirmed:
            raise ValueError("Review this action and explicitly confirm it before submitting.")
        if operation in MANAGER | {"setup.verify"} and not interaction.user.guild_permissions.manage_guild:
            raise AccessDenied("Manage Server permission is required for configuration.")
        if operation == "setup.verify":
            return await verify_setup(self.bot, interaction)
        if operation in {"draft.start", "draft.read", "draft.correct", "draft.save", "draft.discard"}:
            return await draft_action(self.bot, interaction, operation, values, confirmed)
        if operation not in {name for names in SCREENS.values() for name in names}:
            raise AccessDenied("This operation is not available on the website.")
        # A single actor cannot create overlapping web writes. Repository
        # transactions also serialize website and Discord record mutations.
        key = (guild, actor_id)
        if key in self.actor_locks:
            raise ValueError("Your previous action is still running. Check its result before submitting another.")
        self.actor_locks[key] = True
        try:
            await invoke(self.bot, interaction, operation, values, confirmed)
        finally:
            self.actor_locks.pop(key, None)
        result = interaction.result()
        if not result["message"]:
            result["message"] = "Saved evidence loaded." if operation in READS else "Operation confirmed."
        if interaction.view is not None:
            token = await self.remember_snapshot(subject, guild, interaction.view)
            result["snapshot"] = token
        return result

    async def remember_snapshot(self, subject, guild, view):
        # One active evidence snapshot per account/server bounds readers and WAL.
        for key, item in tuple(self.snapshots.items()):
            if item["subject"] == subject and item["guild"] == guild:
                self.snapshots.pop(key)
                if hasattr(item["view"], "source"):
                    await item["view"].on_timeout()
        if len(self.snapshots) >= 100:
            if hasattr(view, "source"):
                await view.on_timeout()
            raise ValueError("Too many active evidence views. Try again after older views expire.")
        token = secrets.token_urlsafe(24)
        self.snapshots[token] = {"subject": subject, "guild": guild, "view": view,
                                 "expires": time.monotonic() + 600, "lock": asyncio.Lock(), "last": 1}
        return token

    async def snapshot_page(self, subject, guild, interaction, params):
        item = self.snapshots.get(params.get("snapshot"))
        if not item or item["subject"] != subject or item["guild"] != guild:
            raise AccessDenied("Evidence view expired or does not belong to this account and server. Reopen the workflow.")
        page = params.get("page", 1)
        if type(page) is not int or page < 1 or page > item["last"] + 1:
            raise ValueError("Use the next or previous evidence page.")
        async with item["lock"]:
            view = item["view"]
            if hasattr(view, "source"):
                index, embed = await view.source.page(page - 1)
                await interaction.response.send(embed=embed)
                has_next = not view.source.exhausted or index < view.source.loaded - 1
            elif hasattr(view, "items"):
                if page > len(view.items):
                    raise ValueError("There are no more evidence pages.")
                await interaction.response.send(embed=view.items[page - 1])
                has_next = page < len(view.items)
            elif hasattr(view, "render"):
                await view.render(interaction, page - 1)
                has_next = not view.next.disabled
            else:
                raise ValueError("This interactive workflow is available in Discord.")
            item["last"] = max(page, item["last"])
        return {"title": "Private saved evidence", "rows": [], "blocks":
                [{"title": m["title"] or "Evidence", "text": m["text"]} for m in interaction.messages],
                "page": page, "hasNext": has_next, "operations": [],
                "snapshot": params["snapshot"], "live": "Current staff access reverified; evidence snapshot is saved"}
