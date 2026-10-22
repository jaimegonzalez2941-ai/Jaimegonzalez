"""Lazy private Discord pages with bounded memory and a disposable disk page cache."""

import asyncio
from contextlib import aclosing

import aiosqlite
import discord

from .access import AccessDenied
from .assistant_ui import StaffPages
from .club_domain import stamp
from .presentation import embed
from .workspace_snapshot import WorkspaceSnapshot


async def descriptions(blocks):
    """Streaming equivalent of pages(); retain at most one page plus the current block."""
    current = ""
    async with aclosing(blocks):
        async for block in blocks:
            if current and len(block) <= 3500 and len(current) + len(block) + 2 > 3500:
                yield current
                current = ""
            while block:
                space = 3500 - len(current) - (2 if current else 0)
                if space <= 0:
                    yield current
                    current = ""
                    continue
                piece, block = block[:space], block[space:]
                current += ("\n\n" if current else "") + piece
                if block:
                    yield current
                    current = ""
    if current:
        yield current


class WorkspacePages:
    """The cache grows on disk only, and only as pages are requested."""

    def __init__(self, snapshot, cache, title, blocks):
        self.snapshot, self.cache, self.title = snapshot, cache, title
        self.stream = descriptions(blocks)
        self.loaded = 0
        self.exhausted = False
        self.closed = False

    @classmethod
    async def open(cls, database, title, factory):
        snapshot = await WorkspaceSnapshot.open(database)
        cache = None
        try:
            cache = await aiosqlite.connect(snapshot.directory.name + "/pages.db",
                                            isolation_level=None)
            await cache.execute("CREATE TABLE pages (id INTEGER PRIMARY KEY, description TEXT NOT NULL)")
            return cls(snapshot, cache, title, factory(snapshot))
        except BaseException:
            if cache:
                await cache.close()
            await snapshot.close()
            raise

    async def page(self, index):
        if self.closed:
            raise AccessDenied("Workspace expired. Reopen the command for a fresh snapshot.")
        # One page lookahead makes Next accurate without counting/rendering the full history.
        while not self.exhausted and self.loaded <= index + 1:
            description = await anext(self.stream, None)
            if description is None:
                self.exhausted = True
                if self.loaded:
                    break
                description = "No reported records."
            await self.cache.execute("INSERT INTO pages VALUES (?,?)", (self.loaded, description))
            self.loaded += 1
        index = max(0, min(index, self.loaded - 1))
        async with self.cache.execute("SELECT description FROM pages WHERE id=?", (index,)) as cur:
            row = await cur.fetchone()
        item = embed(self.title, row[0])
        total = str(self.loaded) if self.exhausted else "…"
        item.set_footer(text=f"Club Agent • Staff only • Page {index + 1}/{total} • "
                        f"Snapshot {stamp(self.snapshot.now)} • Refresh for changes • Expires in 10 min")
        return index, item

    async def close(self):
        if not self.closed:
            self.closed = True
            try:
                await self.stream.aclose()
            finally:
                try:
                    await self.cache.close()
                finally:
                    await self.snapshot.close()


class LazyStaffPages(StaffPages):
    def __init__(self, owner, guild, source, database, factory):
        # StaffPages provides the shared owner/server/staff checks and navigation buttons.
        self.source, self.database, self.factory = source, database, factory
        self.mutex = asyncio.Lock()
        super().__init__(owner, guild, ())
        database.workspace_views.add(self)
        self.expiry = asyncio.create_task(self.expire(source))

    def controls(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.source.exhausted and self.index >= self.source.loaded - 1

    async def expire(self, source):
        try:
            await asyncio.sleep(600)
        finally:
            # Also release SQLite readers on bot shutdown / event-loop cancellation.
            # Capture this source: a cancelled old timer must never close a refresh.
            async with self.mutex:
                await source.close()
                if self.source is source:
                    self.database.workspace_views.discard(self)
                    self.stop()

    async def on_timeout(self):
        if asyncio.current_task() is not self.expiry:
            self.expiry.cancel()
        async with self.mutex:
            await self.source.close()
            self.database.workspace_views.discard(self)
            self.stop()

    async def move(self, interaction, offset):
        async with self.mutex:
            await self.interaction_check(interaction)
            if self.source.closed:
                raise AccessDenied("Workspace expired. Reopen the command for a fresh snapshot.")
            await interaction.response.defer()
            try:
                index, item = await self.source.page(max(0, self.index + offset))
            except BaseException:
                await self.source.close()
                raise
            # Recheck after disk I/O as well; no snapshot authorization is trusted.
            await self.interaction_check(interaction)
            self.index = index
            self.controls()
            await interaction.edit_original_response(
                embed=item, view=self, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        async with self.mutex:
            await self.interaction_check(interaction)
            if self.source.closed:
                raise AccessDenied("Workspace expired. Reopen the command for a fresh snapshot.")
            await interaction.response.defer()
            replacement = await WorkspacePages.open(self.database, self.source.title, self.factory)
            try:
                index, item = await replacement.page(0)
                await self.interaction_check(interaction)
                old, self.source = self.source, replacement
                old_index, self.index = self.index, index
                self.controls()
                await interaction.edit_original_response(
                    embed=item, view=self, allowed_mentions=discord.AllowedMentions.none())
            except BaseException:
                if self.source is replacement:
                    self.source, self.index = old, old_index
                    self.controls()
                await replacement.close()
                raise
            await old.close()
            self.expiry.cancel()
            self.expiry = asyncio.create_task(self.expire(replacement))


async def send_workspace(interaction, title, factory):
    """Open one consistent snapshot; refresh re-runs the same scoped query factory."""
    source = await WorkspacePages.open(interaction.client.db, title, factory)
    view = None
    try:
        _, item = await source.page(0)
        view = LazyStaffPages(interaction.user.id, interaction.guild.id, source,
                              interaction.client.db, factory)
        await view.interaction_check(interaction)
        await interaction.followup.send(embed=item, view=view, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())
    except BaseException:
        if view:
            view.expiry.cancel()
            view.database.workspace_views.discard(view)
            view.stop()
        await source.close()
        raise
