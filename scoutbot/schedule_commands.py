"""Session scheduling and commitments, available through Discord slash commands."""

import discord
from discord import app_commands

from .access import staff_access
from .club_domain import session_times, stamp, utc_now
from .club_ui import MemberGroup, card, event_text
from .presentation import text


@app_commands.guild_only()
class ScheduleCommands(MemberGroup):
    def __init__(self, bot):
        super().__init__(name="schedule", description="Club sessions, RSVPs, and staff roster management")
        self.bot = bot

    @app_commands.command(description="Staff: create a training, match, or trial session")
    @app_commands.choices(kind=[app_commands.Choice(name=k.title(), value=k) for k in ("training", "match", "trial")])
    @app_commands.describe(date="YYYY-MM-DD", time="24-hour HH:MM",
                           timezone="IANA timezone, e.g. America/Chicago")
    async def create(self, interaction: discord.Interaction, title: app_commands.Range[str, 1, 100],
                     kind: str, date: str, time: str, timezone: str,
                     minutes: app_commands.Range[int, 15, 720] = 90):
        await staff_access(interaction)
        start, end = session_times(date, time, timezone, minutes)
        await interaction.response.defer(ephemeral=True)
        identifier = await self.bot.db.create_event(interaction.guild_id, interaction.user.id, title, kind, start, end, timezone)
        row = await self.bot.db.event(interaction.guild_id, identifier)
        await interaction.followup.send(embed=card("Session created", event_text(row) +
            "\nPlayers can find it with `/schedule list` and commit using `/schedule rsvp`."), ephemeral=True)

    @app_commands.command(description="List upcoming or past sessions, ten per page")
    async def list(self, interaction: discord.Interaction, past: bool = False,
                   page: app_commands.Range[int, 1, 10000] = 1):
        await interaction.response.defer(ephemeral=True)
        comparison, order = ("<=", "DESC") if past else (">", "ASC")
        rows = await self.bot.db.all(
            f"SELECT * FROM club_events WHERE guild_id=? AND ends_at{comparison}? "
            f"ORDER BY starts_at {order} LIMIT 10 OFFSET ?",
            (str(interaction.guild_id), stamp(utc_now()), (page - 1) * 10))
        await interaction.followup.send(embed=card(
            f'{"Past" if past else "Upcoming"} sessions · Page {page}',
            "\n\n".join(event_text(row) for row in rows) or "No sessions on this page."), ephemeral=True)

    @app_commands.command(description="Show a session, your RSVP, and any overlapping leave")
    async def show(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1]):
        await interaction.response.defer(ephemeral=True)
        row = await self.bot.db.event(interaction.guild_id, session_id)
        rsvp = await self.bot.db.one(
            "SELECT response FROM club_roster WHERE guild_id=? AND event_id=? AND member_id=?",
            (str(interaction.guild_id), session_id, str(interaction.user.id)))
        leaves = await self.bot.db.all(
            "SELECT id,status FROM club_leave WHERE guild_id=? AND member_id=? "
            "AND status IN ('approved','pending') AND starts_at<? AND ends_at>?",
            (str(interaction.guild_id), str(interaction.user.id), row["ends_at"], row["starts_at"]))
        await interaction.followup.send(embed=card("Session details", event_text(row) +
            f'\nYour RSVP: **{rsvp["response"] if rsvp else "Not set"}**\n'
            + "\n".join(f'Leave #{leave["id"]}: {leave["status"]}' for leave in leaves)), ephemeral=True)

    @app_commands.command(description="Commit to a session, or mark maybe/no, before it starts")
    @app_commands.choices(response=[app_commands.Choice(name=label, value=value) for label, value in
                                    (("Yes — I commit to attending", "yes"), ("Maybe", "maybe"), ("No", "no"))])
    async def rsvp(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1], response: str):
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.rsvp(interaction.guild_id, interaction.user.id, session_id, response)
        await interaction.followup.send(f"Session #{session_id}: **{response}**. "
            "You can change your own RSVP before kickoff unless staff assigned you; ask staff for release in that case. "
            "Approved leave still protects your attendance.", ephemeral=True)

    @app_commands.command(description="Staff: assign a player to a session, or release an assignment")
    async def assign(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1],
                     member: discord.Member, release: bool = False):
        await staff_access(interaction)
        if member.bot:
            raise ValueError("Choose a player, not a bot.")
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.rsvp(interaction.guild_id, member.id, session_id,
                               "pending" if release else "assigned", actor=interaction.user.id)
        await interaction.followup.send(
            f"{text(member.display_name)} {'released; RSVP is now pending' if release else 'assigned'} "
            f"for session #{session_id}. Check `/schedule roster` for leave conflicts.", ephemeral=True)

    @app_commands.command(description="Staff: review commitments, attendance, and leave conflicts")
    async def roster(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1],
                     page: app_commands.Range[int, 1, 10000] = 1):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        event = await self.bot.db.event(interaction.guild_id, session_id)
        rows = await self.bot.db.all(
            """SELECT r.member_id,r.response,a.status,
            EXISTS(SELECT 1 FROM club_leave l WHERE l.guild_id=r.guild_id AND l.member_id=r.member_id
                AND l.status='approved' AND l.starts_at<? AND l.ends_at>?) AS on_leave
            FROM club_roster r LEFT JOIN club_attendance a ON a.guild_id=r.guild_id
            AND a.event_id=r.event_id AND a.member_id=r.member_id WHERE r.guild_id=? AND r.event_id=?
            ORDER BY r.member_id LIMIT 10 OFFSET ?""",
            (event["ends_at"], event["starts_at"], str(interaction.guild_id), session_id, (page - 1) * 10))
        lines = [f'<@{r["member_id"]}> · {r["response"]} · attendance: {r["status"] or "unmarked"}'
                 + (" · APPROVED LEAVE" if r["on_leave"] else "") for r in rows]
        await interaction.followup.send(embed=card(f"Session #{session_id} roster · Page {page}",
            "\n".join(lines) or "No players on this page."), ephemeral=True)

    @app_commands.command(description="Staff: edit an upcoming session; changed times require all RSVPs to reconfirm")
    @app_commands.describe(date="YYYY-MM-DD", time="24-hour HH:MM", timezone="IANA timezone")
    async def edit(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1],
                   title: app_commands.Range[str, 1, 100], date: str, time: str, timezone: str,
                   minutes: app_commands.Range[int, 15, 720] = 90):
        await staff_access(interaction)
        start, end = session_times(date, time, timezone, minutes)
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.edit_event(interaction.guild_id, interaction.user.id,
                                               session_id, title, start, end, timezone)
        await interaction.followup.send(
            f"Session #{session_id} updated. " +
            ("**All RSVPs and assignments are now pending. Players must reconfirm or staff must reassign.**"
             if changed else "Times did not change; existing RSVPs remain."), ephemeral=True)

    @app_commands.command(description="Staff: cancel a session and exclude it from attendance calculations")
    async def cancel(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1], confirm: bool = False):
        await staff_access(interaction)
        if not confirm:
            raise ValueError("Cancellation removes this session from attendance calculations. Re-run with confirm:True.")
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.cancel_event(interaction.guild_id, interaction.user.id, session_id)
        await interaction.followup.send(
            f"Session #{session_id} cancelled and excluded from attendance. Existing records are retained.", ephemeral=True)
