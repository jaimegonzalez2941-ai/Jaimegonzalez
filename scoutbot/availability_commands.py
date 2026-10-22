"""Player availability and staff-reviewed leave."""

import discord
from discord import app_commands

from .access import staff_access
from .club_domain import leave_times
from .club_ui import MemberGroup, WEEKDAYS, card, target_member, time_tag
from .presentation import text

DAY_CHOICES = [app_commands.Choice(name=name, value=index) for index, name in enumerate(WEEKDAYS)]


@app_commands.guild_only()
class AvailabilityCommands(MemberGroup):
    def __init__(self, bot):
        super().__init__(name="availability", description="Weekly availability and vacation/leave")
        self.bot = bot

    @app_commands.command(description="Set or replace your availability window for a weekday")
    @app_commands.choices(weekday=DAY_CHOICES)
    @app_commands.describe(start="24-hour HH:MM", end="24-hour HH:MM; 24:00 allowed for end of day",
                           timezone="IANA timezone, e.g. America/Chicago")
    async def set(self, interaction: discord.Interaction, weekday: int, start: str, end: str, timezone: str):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.set_availability(interaction.guild_id, interaction.user.id, weekday, start, end, timezone)
        await interaction.followup.send(
            f"{WEEKDAYS[weekday]}: {start}–{end} ({text(timezone)}). "
            "One window per weekday; replacing it does not change session RSVPs. "
            "Split overnight availability across two days.", ephemeral=True)

    @app_commands.command(description="Remove your recurring window for a weekday")
    @app_commands.choices(weekday=DAY_CHOICES)
    async def clear(self, interaction: discord.Interaction, weekday: int):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.remove_availability(interaction.guild_id, interaction.user.id, weekday)
        await interaction.followup.send(f"{WEEKDAYS[weekday]} window removed. Existing RSVPs are unchanged.", ephemeral=True)

    @app_commands.command(description="View your weekly availability, or another player's as staff")
    async def view(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = await target_member(interaction, member)
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.all(
            "SELECT * FROM availability_windows WHERE guild_id=? AND member_id=? ORDER BY weekday",
            (str(interaction.guild_id), str(member.id)))
        lines = []
        for row in rows:
            start, end = row["start_min"], row["end_min"]
            lines.append(f'**{WEEKDAYS[row["weekday"]]}** · {start // 60:02}:{start % 60:02}–'
                         f'{end // 60:02}:{end % 60:02} · {text(row["timezone"])}')
        await interaction.followup.send(embed=card(f"Availability · {text(member.display_name, 80)}",
            "\n".join(lines) or "No windows recorded. Use `/availability set`."), ephemeral=True)

    @app_commands.command(description="Staff: review club-wide availability windows, ten per page")
    async def roster(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 10000] = 1):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        rows = await self.bot.db.all(
            "SELECT * FROM availability_windows WHERE guild_id=? ORDER BY member_id,weekday LIMIT 10 OFFSET ?",
            (str(interaction.guild_id), (page - 1) * 10))
        lines = []
        for row in rows:
            start, end = row["start_min"], row["end_min"]
            lines.append(f'<@{row["member_id"]}> · {WEEKDAYS[row["weekday"]]} · '
                         f'{start // 60:02}:{start % 60:02}–{end // 60:02}:{end % 60:02} '
                         f'({text(row["timezone"])})')
        await interaction.followup.send(embed=card(f"Availability roster · Page {page}",
            "\n".join(lines) or "No more windows."), ephemeral=True)

    @app_commands.command(description="Request vacation or time away; staff approval protects attendance")
    @app_commands.describe(start_date="First day, YYYY-MM-DD", end_date="Last day inclusive, YYYY-MM-DD",
                           timezone="IANA timezone, e.g. America/Chicago")
    async def leave(self, interaction: discord.Interaction, start_date: str, end_date: str, timezone: str):
        start, end = leave_times(start_date, end_date, timezone)
        await interaction.response.defer(ephemeral=True)
        identifier = await self.bot.db.request_leave(interaction.guild_id, interaction.user.id, start, end, timezone)
        await interaction.followup.send(
            f"Leave request **#{identifier}**: {text(start_date)} through {text(end_date)} "
            f"({text(timezone)}). **Pending staff approval**. Once approved, overlapping sessions "
            "are excluded from your attendance rate. No private reason is required.", ephemeral=True)

    @app_commands.command(description="View your leave requests; staff may choose a player or all requests")
    async def leaves(self, interaction: discord.Interaction, member: discord.Member | None = None,
                     all_players: bool = False, page: app_commands.Range[int, 1, 10000] = 1):
        if all_players:
            await staff_access(interaction)
        else:
            member = await target_member(interaction, member)
        await interaction.response.defer(ephemeral=True)
        sql = "SELECT * FROM club_leave WHERE guild_id=?"
        args = [str(interaction.guild_id)]
        if not all_players:
            sql += " AND member_id=?"
            args.append(str(member.id))
        sql += " ORDER BY id DESC LIMIT 10 OFFSET ?"
        args.append((page - 1) * 10)
        rows = await self.bot.db.all(sql, args)
        lines = [f'**#{row["id"]} · {row["status"]}** · <@{row["member_id"]}>\n'
                 f'{time_tag(row["starts_at"])} → {time_tag(row["ends_at"])} (end exclusive)'
                 for row in rows]
        await interaction.followup.send(embed=card(f"Leave requests · Page {page}",
            "\n\n".join(lines) or "No requests on this page."), ephemeral=True)

    @app_commands.command(description="Staff: approve, decline, or revoke a leave request")
    async def review_leave(self, interaction: discord.Interaction, leave_id: app_commands.Range[int, 1], approved: bool):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.review_leave(interaction.guild_id, interaction.user.id, leave_id, approved)
        await interaction.followup.send(
            f"Leave #{leave_id} {'approved' if approved else 'declined/revoked'}. "
            "Attendance reports now reflect that decision; the action was audited.", ephemeral=True)

    @app_commands.command(description="Cancel your leave; contact staff if approved leave has already started")
    async def cancel_leave(self, interaction: discord.Interaction, leave_id: app_commands.Range[int, 1]):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.cancel_leave(interaction.guild_id, interaction.user.id, leave_id)
        await interaction.followup.send(f"Your leave request #{leave_id} was cancelled.", ephemeral=True)
