"""Attendance recording and evidence-based staff promotion recommendations."""

import discord
from discord import app_commands

from .access import manager_access, staff_access
from .club_domain import ATTENDANCE
from .club_ui import MemberGroup, card, target_member, attendance_text, promotion_text
from .domain import STAGES
from .presentation import text


@app_commands.guild_only()
class AttendanceCommands(MemberGroup):
    def __init__(self, bot):
        super().__init__(name="attendance", description="Attendance records and personal attendance reports")
        self.bot = bot

    @app_commands.command(description="Staff: record or correct a committed player's attendance after kickoff")
    @app_commands.choices(status=[app_commands.Choice(name=k.title(), value=k) for k in ATTENDANCE])
    async def mark(self, interaction: discord.Interaction, session_id: app_commands.Range[int, 1],
                   member: discord.Member, status: str):
        await staff_access(interaction)
        if member.bot:
            raise ValueError("Choose a player, not a bot.")
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.mark_attendance(interaction.guild_id, interaction.user.id, session_id, member.id, status)
        await interaction.followup.send(
            f"Session #{session_id} · {text(member.display_name)}: **{status}**. "
            "Corrections replace the prior mark and are audited. Reports include the session once it ends.",
            ephemeral=True)

    @app_commands.command(description="View your attendance; staff may choose another player")
    async def report(self, interaction: discord.Interaction, member: discord.Member | None = None,
                     days: app_commands.Range[int, 1, 365] | None = None):
        member = await target_member(interaction, member)
        await interaction.response.defer(ephemeral=True)
        policy = await self.bot.db.policy(interaction.guild_id)
        days = days or policy["days"]
        summary = await self.bot.db.attendance_summary(interaction.guild_id, member.id, days)
        await interaction.followup.send(embed=card(
            f"Attendance · {text(member.display_name, 80)} · {days} days",
            attendance_text(summary)), ephemeral=True)


@app_commands.guild_only()
class PromotionCommands(MemberGroup):
    def __init__(self, bot):
        super().__init__(name="promotion", description="Staff promotion evidence and configurable attendance policy")
        self.bot = bot

    @app_commands.command(description="Staff: review development-tier readiness using scouting and attendance")
    async def review(self, interaction: discord.Interaction, member: discord.Member):
        await staff_access(interaction)
        if member.bot:
            raise ValueError("Choose a player, not a bot.")
        await interaction.response.defer(ephemeral=True)
        evidence = await self.bot.db.promotion_evidence(interaction.guild_id, member.id)
        mappings = await self.bot.db.all("SELECT stage,role_id FROM stage_roles WHERE guild_id=?",
                                          (str(interaction.guild_id),))
        owned = {str(role.id) for role in member.roles}
        stages = [STAGES.index(row["stage"]) for row in mappings
                  if row["stage"] in STAGES and row["role_id"] in owned]
        current = max(stages) if stages else -1
        target = STAGES[current + 1] if current + 1 < len(STAGES) else None
        result = card(f"Promotion review · {text(member.display_name, 80)}",
                      promotion_text(evidence))
        result.add_field(name="Development path", value=" → ".join(STAGES), inline=False)
        result.add_field(name="Current tier", value=STAGES[current] if current >= 0 else "No mapped tier assigned")
        result.add_field(name="Next tier", value=target or "Already at the highest tier")
        result.add_field(name="Staff action",
            value="No role was changed. Map roles with `/scout stage_role`, then use `/scout assign`. "
                  "Assigning a development tier without sufficient evidence requires an explicit override reason. "
                  "Roles are additive; prior roles are not automatically removed.", inline=False)
        await interaction.followup.send(embed=result, ephemeral=True)

    @app_commands.command(description="Managers: adjust attendance rules to fit the club's availability")
    @app_commands.default_permissions(manage_guild=True)
    async def policy(self, interaction: discord.Interaction,
                     percent: app_commands.Range[int, 1, 100],
                     days: app_commands.Range[int, 1, 365],
                     minimum_sessions: app_commands.Range[int, 1, 100]):
        manager_access(interaction)
        await interaction.response.defer(ephemeral=True)
        await self.bot.db.set_policy(interaction.guild_id, interaction.user.id, days, minimum_sessions, percent)
        await interaction.followup.send(
            f"Promotion attendance policy: **{percent}% over {days} days, at least {minimum_sessions} "
            "marked eligible sessions**. Approved leave and excused absences remain excluded. "
            "The latest scouting evaluation must still be at least 8.0 within that window. "
            "Existing attendance marks and scouting scores were not changed.", ephemeral=True)
