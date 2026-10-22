"""Stateful private forms and restart-safe docket controls."""

import json

import discord

from .access import AccessDenied, private_channel, staff_access
from .domain import POSITIONS
from .rubric import CATEGORIES, LABELS, OBSERVABLES, ANCHORS, category_score, coverage
from . import presentation as ui
from .errors import respond_error


class SecureView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await respond_error(interaction, error)


async def draft_access(interaction, row):
    await staff_access(interaction)
    if str(interaction.guild.id) != row["guild_id"] or str(interaction.user.id) != row["scout_id"]:
        raise AccessDenied("This scorecard belongs to another scout or server.")


class EvaluationModal(discord.ui.Modal):
    """Context editor; category observations have their own guided editor."""

    def __init__(self, row):
        super().__init__(title="Observation context", timeout=600)
        self.row = row
        data = json.loads(row["payload"])
        self.count = discord.ui.TextInput(label="Observed matches/sessions (1–10000)",
                                         default=str(data["count"] or ""), max_length=5)
        self.context = discord.ui.TextInput(
            label="Dates, opposition, mode and assigned role", style=discord.TextStyle.paragraph,
            max_length=500, default=data["context"])
        self.notes = discord.ui.TextInput(
            label="Summary evidence and next steps", style=discord.TextStyle.paragraph,
            max_length=1000, default=data["notes"])
        for item in (self.count, self.context, self.notes):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await draft_access(interaction, self.row)
        value = self.count.value.strip()
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Enter a positive whole observed session count.")
        count = int(value)
        coverage(count)
        data = json.loads(self.row["payload"])
        data.update(count=count, context=self.context.value.strip(), notes=self.notes.value.strip())
        await correct_and_render(interaction, self.row, data)

    async def on_error(self, interaction, error):
        await respond_error(interaction, error)


class CategoryModal(discord.ui.Modal):
    def __init__(self, row, key):
        index = CATEGORIES.index(key)
        super().__init__(title=LABELS[index], timeout=600)
        self.row, self.key = row, key
        entry = json.loads(row["payload"])["categories"].get(key, {})
        self.score = discord.ui.TextInput(
            label="1–10, one decimal; blank = unassessed", required=False,
            max_length=8, default=entry.get("score") or "")
        self.evidence = discord.ui.TextInput(
            label="Observed actions supporting this category", required=False,
            style=discord.TextStyle.paragraph, max_length=500, default=entry.get("evidence", ""))
        self.add_item(self.score)
        self.add_item(self.evidence)

    async def on_submit(self, interaction):
        await draft_access(interaction, self.row)
        data = json.loads(self.row["payload"])
        data["categories"][self.key] = {
            "score": category_score(self.score.value), "evidence": self.evidence.value.strip()}
        await correct_and_render(interaction, self.row, data)

    async def on_error(self, interaction, error):
        await respond_error(interaction, error)


async def render_draft(interaction, row):
    await interaction.edit_original_response(embed=ui.scorecard(row), view=ScorecardView(row))


async def correct_and_render(interaction, row, data):
    await interaction.response.defer(ephemeral=True)
    db = interaction.client.db
    await db.change_draft(interaction.guild.id, interaction.user.id, row["id"],
                          row["revision"], payload=data)
    await render_draft(interaction, await db.draft(interaction.guild.id, interaction.user.id, row["id"]))


class CategoryView(SecureView):
    def __init__(self, row, key):
        super().__init__(timeout=600)
        self.row, self.key = row, key

    @discord.ui.button(label="Enter / correct observations", style=discord.ButtonStyle.primary)
    async def edit(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.send_modal(CategoryModal(self.row, self.key))

    @discord.ui.button(label="Back to review")
    async def back(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.defer()
        row = await interaction.client.db.draft(interaction.guild.id, interaction.user.id, self.row["id"])
        await render_draft(interaction, row)


class ScorecardView(SecureView):
    def __init__(self, row):
        super().__init__(timeout=600)
        self.row = row
        select = discord.ui.Select(placeholder="Assess / correct a category", row=0, options=[
            discord.SelectOption(label=label, value=key) for key, label in zip(CATEGORIES, LABELS)])
        select.callback = self.category
        self.select = select
        self.add_item(select)

    async def category(self, interaction):
        await draft_access(interaction, self.row)
        key = self.select.values[0]
        index = CATEGORIES.index(key)
        guide = ui.embed(f'{self.row["position"]} · {LABELS[index]}',
                         OBSERVABLES[self.row["position"]][index] + "\n\n" + ANCHORS)
        await interaction.response.edit_message(embed=guide, view=CategoryView(self.row, key))

    @discord.ui.button(label="Context / coverage", row=1)
    async def context(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.send_modal(EvaluationModal(self.row))

    @discord.ui.button(label="Trusted match context", row=1)
    async def statistics(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.defer(ephemeral=True)
        db, guild, member = interaction.client.db, interaction.guild.id, int(self.row["candidate_id"])
        totals = await db.aggregate(guild, member)
        rows = await db.stats(guild, member)
        candidate = type("Candidate", (), {"display_name": self.row["candidate_name"], "id": member})()
        await interaction.followup.send(
            embed=ui.stats(rows, candidate, 0, totals),
            view=HistoryView(interaction.user.id, candidate, "stats"), ephemeral=True)

    @discord.ui.button(label="Confirm & save", style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.defer(ephemeral=True)
        identifier = await interaction.client.db.change_draft(
            interaction.guild.id, interaction.user.id, self.row["id"], self.row["revision"], action="save")
        saved = await interaction.client.db.draft(interaction.guild.id, interaction.user.id, self.row["id"])
        result = ui.scorecard(saved)
        result.title = f"Evaluation saved · #{identifier}"
        result.description = result.description.replace(
            "Review/correct before Confirm & save. Nothing is published yet.",
            "Final evaluation saved. This scorecard is now read-only.")
        await interaction.edit_original_response(embed=result, view=None)

    @discord.ui.button(label="Discard draft", style=discord.ButtonStyle.danger, row=2)
    async def discard(self, interaction, button):
        await draft_access(interaction, self.row)
        await interaction.response.defer(ephemeral=True)
        await interaction.client.db.change_draft(
            interaction.guild.id, interaction.user.id, self.row["id"], self.row["revision"], action="discard")
        await interaction.edit_original_response(content="Draft discarded. No evaluation was published.",
                                                 embed=None, view=None)


class PositionView(SecureView):
    def __init__(self, candidate: discord.Member, owner: int, guild: int):
        super().__init__(timeout=180)
        self.candidate, self.owner = candidate, owner
        self.guild = guild
        select = discord.ui.Select(
            placeholder="Choose a position group",
            options=[discord.SelectOption(label=p) for p in POSITIONS],
        )
        select.callback = self.select_position
        self.select = select
        self.add_item(select)

    async def select_position(self, interaction):
        await staff_access(interaction)
        if interaction.user.id != self.owner or interaction.guild.id != self.guild:
            raise AccessDenied("Start your own evaluation with /scout evaluate.")
        await interaction.response.defer()
        row = await interaction.client.db.start_draft(
            interaction.guild.id, self.candidate.id, self.candidate.display_name,
            interaction.user.id, interaction.user.display_name, self.select.values[0])
        await render_draft(interaction, row)


class DocketView(SecureView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Refresh docket", style=discord.ButtonStyle.secondary,
                       custom_id="scout:v2:docket:refresh")
    async def refresh(self, interaction: discord.Interaction, button):
        config = await staff_access(interaction)
        if not config or str(interaction.channel_id) != config["staff_channel"]:
            raise AccessDenied("This docket is no longer in the configured staff channel.")
        staff = interaction.guild.get_role(int(config["staff_role"]))
        if staff is None or not isinstance(interaction.channel, discord.TextChannel):
            raise AccessDenied("The configured staff role or channel is unavailable.")
        await interaction.response.defer()
        await private_channel(interaction.channel, staff)
        rows = await interaction.client.db.recent(interaction.guild.id)
        await interaction.edit_original_response(embed=ui.docket(rows), view=self)


class HistoryView(SecureView):
    def __init__(self, owner: int, candidate: discord.Member, mode: str, page: int = 0):
        super().__init__(timeout=180)
        self.owner, self.candidate, self.mode, self.page = owner, candidate, mode, page
        self.previous.disabled = page == 0

    async def render(self, interaction, page: int):
        await staff_access(interaction)
        if interaction.user.id != self.owner:
            raise AccessDenied("This private history belongs to another scout.")
        await interaction.response.defer()
        db = interaction.client.db
        guild = interaction.guild.id
        rows = await (db.history(guild, self.candidate.id, page) if self.mode == "history"
                      else db.stats(guild, self.candidate.id, page))
        if not rows:
            await interaction.followup.send("There are no more records.", ephemeral=True)
            return
        self.page = page
        self.previous.disabled = page == 0
        self.next.disabled = len(rows) < 5
        result = (ui.history(rows, self.candidate, page) if self.mode == "history"
                  else ui.stats(rows, self.candidate, page, await db.aggregate(guild, self.candidate.id)))
        await interaction.edit_original_response(embed=result, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        await self.render(interaction, max(0, self.page - 1))

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction, button):
        await self.render(interaction, self.page + 1)
