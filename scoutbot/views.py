"""Stateful private forms and restart-safe docket controls."""

import logging
import uuid

import discord

from .access import AccessDenied, private_channel, staff_access
from .domain import POSITIONS, rating
from . import presentation as ui

logger = logging.getLogger(__name__)


async def respond_error(interaction: discord.Interaction, error: Exception):
    if isinstance(error, (ValueError, AccessDenied)):
        message = str(error)
    elif isinstance(error, discord.Forbidden):
        message = "Discord denied that action. Check the bot's channel permissions and role order."
    elif isinstance(error, discord.NotFound):
        message = "That Discord message, channel, or member is no longer available."
    elif isinstance(error, discord.HTTPException):
        message = "Discord could not complete the request. Please try again shortly."
    else:
        reference = uuid.uuid4().hex[:8]
        logger.exception("Unhandled interaction error reference=%s", reference,
                         exc_info=(type(error), error, error.__traceback__))
        message = f"Something went wrong. No success is being assumed. Reference: {reference}"
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class SecureView(discord.ui.View):
    async def on_error(self, interaction, error, item):
        await respond_error(interaction, error)


class EvaluationModal(discord.ui.Modal):
    def __init__(self, candidate: discord.Member, position: str, owner: int):
        super().__init__(title=f"Evaluate · {position}", timeout=600)
        self.candidate = candidate
        self.position = position
        self.owner = owner
        self.score = discord.ui.TextInput(
            label="Rating (1–10; 6.8–7.2 excluded)", max_length=8,
            placeholder="8.5", required=True,
        )
        self.notes = discord.ui.TextInput(
            label="Scout notes", style=discord.TextStyle.paragraph, min_length=3,
            max_length=1000, placeholder="Performance, tactical awareness, next steps…",
        )
        self.add_item(self.score)
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        await staff_access(interaction)
        if interaction.user.id != self.owner:
            raise AccessDenied("This evaluation belongs to another scout.")
        score = rating(self.score.value)
        notes = self.notes.value.strip()
        if len(notes) < 3:
            raise ValueError("Please enter at least three non-whitespace characters of notes.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        identifier = await interaction.client.db.evaluate(
            interaction.guild.id, self.candidate.id, self.candidate.display_name,
            interaction.user.id, interaction.user.display_name, self.position, score, notes,
        )
        result = ui.embed("Evaluation saved", f"Record #{identifier} · Visible only to scouting staff")
        result.add_field(name="Candidate", value=ui.text(self.candidate.display_name))
        result.add_field(name="Position", value=self.position)
        result.add_field(name="Score", value=f"{score:.1f}/10")
        result.add_field(name="Standard", value="Trial-ready" if score >= 8 else "Developmental")
        result.add_field(name="Scout notes", value=ui.text(notes, 1000), inline=False)
        await interaction.followup.send(embed=result, ephemeral=True)

    async def on_error(self, interaction, error):
        await respond_error(interaction, error)


class PositionView(SecureView):
    def __init__(self, candidate: discord.Member, owner: int):
        super().__init__(timeout=180)
        self.candidate, self.owner = candidate, owner
        select = discord.ui.Select(
            placeholder="Choose a position group",
            options=[discord.SelectOption(label=p) for p in POSITIONS],
        )
        select.callback = self.select_position
        self.select = select
        self.add_item(select)

    async def select_position(self, interaction):
        await staff_access(interaction)
        if interaction.user.id != self.owner:
            raise AccessDenied("Start your own evaluation with /scout evaluate.")
        await interaction.response.send_modal(
            EvaluationModal(self.candidate, self.select.values[0], self.owner)
        )


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
        private_channel(interaction.channel, staff)
        await interaction.response.defer()
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
