"""Staff-only recruitment, development and accountable action workflows."""

import discord
from discord import app_commands

from .access import staff_access, staff_assignee
from .assistant_store import deadline
from .assistant_ui import briefing_stream, records_stream
from .workspace_pages import send_workspace
from .domain import POSITIONS

POSITION_CHOICES = [app_commands.Choice(name=p, value=p) for p in POSITIONS]
STATUS_CHOICES = [app_commands.Choice(name=s.title(), value=s)
                  for s in ("active", "overdue", "completed", "all")]
RECRUITMENT_CHOICES = [app_commands.Choice(name=s.title(), value=s)
                       for s in ("active", "overdue", "archived", "all")]


def player(member):
    if member.bot:
        raise ValueError("Choose a player, not a bot.")
    return member


async def acknowledge(interaction, message):
    await interaction.followup.send(message, ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())


async def edit_work(bot, interaction, kind, identifier, owner, date, time, timezone):
    await staff_access(interaction)
    parts = (date, time, timezone)
    if any(part is not None for part in parts) and any(part is None for part in parts):
        raise ValueError("To change a deadline, supply the date, time and timezone together.")
    due = deadline(date, time, timezone) if date is not None else None
    if owner is None and due is None:
        raise ValueError("Choose a new staff owner or deadline.")
    await interaction.response.defer(ephemeral=True)
    current = await staff_assignee(interaction, owner) if owner is not None else None
    changed = await bot.db.edit_staff_record(
        interaction.guild_id, interaction.user.id, kind, identifier,
        owner=current.id if current is not None else None, due_at=due, timezone=timezone)
    label = "Development goal" if kind == "goal" else "Staff task"
    await acknowledge(interaction, f"{label} #{identifier} updated; ID and history preserved. "
                      "No scores, roles or notifications changed." if changed else
                      f"{label} #{identifier} is unchanged; no duplicate audit record created.")


class StaffGroup(app_commands.Group):
    async def interaction_check(self, interaction):
        await staff_access(interaction)
        return True


class ShortlistCommands(StaffGroup):
    def __init__(self, bot):
        super().__init__(name="shortlist", description="Private staff-managed recruitment follow-ups")
        self.bot = bot

    @app_commands.command(description="Add or update the active candidate/position follow-up; no role or score changes")
    @app_commands.choices(position=POSITION_CHOICES)
    @app_commands.describe(review_date="YYYY-MM-DD follow-up deadline, not automatic rejection",
                           time="24-hour HH:MM", timezone="IANA timezone, e.g. Europe/London")
    async def add(self, interaction: discord.Interaction, candidate: discord.Member,
                  position: str, responsible: discord.Member,
                  next_action: app_commands.Range[str, 3, 1000],
                  review_date: str, time: str, timezone: str):
        await staff_access(interaction)
        player(candidate)
        due = deadline(review_date, time, timezone)
        await interaction.response.defer(ephemeral=True)
        owner = await staff_assignee(interaction, responsible)
        identifier = await self.bot.db.save_staff_record(
            interaction.guild_id, interaction.user.id, "recruitment", owner.id,
            next_action, due, timezone, member=candidate.id, position=position)
        await acknowledge(interaction, f"Recruitment follow-up #{identifier} saved. An existing active "
                          "candidate/position entry was updated rather than duplicated. "
                          "This deadline is a staff follow-up, not automatic rejection or promotion. No roles changed.")

    @app_commands.command(description="Review recruitment follow-ups including archived entries")
    @app_commands.choices(status=RECRUITMENT_CHOICES)
    async def list(self, interaction: discord.Interaction, status: str = "active"):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild_id
        await send_workspace(interaction, "Recruitment board · " + status,
                             lambda db: records_stream(db, guild, "recruitment", status))

    @app_commands.command(description="Archive a recruitment follow-up with explicit confirmation; retain its history")
    async def remove(self, interaction: discord.Interaction,
                     entry_id: app_commands.Range[int, 1], confirm: bool = False):
        await staff_access(interaction)
        if not confirm:
            raise ValueError("Removal archives the recruitment entry without deleting history or changing roles. "
                             "Run again with confirm:True.")
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.finish_staff_record(
            interaction.guild_id, interaction.user.id, "recruitment", entry_id)
        await acknowledge(interaction, f"Recruitment entry #{entry_id} archived; history retained." if changed
                          else "This entry is already archived. No duplicate archive record created.")


@app_commands.guild_only()
class DevelopmentCommands(StaffGroup):
    def __init__(self, bot):
        super().__init__(name="development", description="Private player goals and observed outcomes")
        self.bot = bot

    @app_commands.command(description="Create a player-specific goal with observable success criteria")
    @app_commands.describe(target_date="YYYY-MM-DD", time="24-hour HH:MM", timezone="IANA timezone",
                           criteria="Observable actions and evidence that staff will review, not inferred potential")
    async def goal(self, interaction: discord.Interaction, member: discord.Member,
                   description: app_commands.Range[str, 3, 1000],
                   criteria: app_commands.Range[str, 3, 1000],
                   target_date: str, time: str, timezone: str,
                   responsible: discord.Member | None = None):
        await staff_access(interaction)
        player(member)
        due = deadline(target_date, time, timezone)
        await interaction.response.defer(ephemeral=True)
        owner = await staff_assignee(interaction, responsible or interaction.user)
        identifier = await self.bot.db.save_staff_record(
            interaction.guild_id, interaction.user.id, "goal", owner.id, description,
            due, timezone, member=member.id, criteria=criteria)
        await acknowledge(interaction, f"Development goal #{identifier} saved with observable criteria. "
                          "No scouting scores changed.")

    @app_commands.command(description="Reassign an unfinished goal or change its deadline; retain its ID and history")
    @app_commands.describe(responsible="New owner: must currently be staff in this server",
                           target_date="YYYY-MM-DD; supply time and timezone too",
                           time="24-hour HH:MM", timezone="IANA timezone")
    async def edit(self, interaction: discord.Interaction, goal_id: app_commands.Range[int, 1],
                   responsible: discord.Member | None = None, target_date: str | None = None,
                   time: str | None = None, timezone: str | None = None):
        await edit_work(self.bot, interaction, "goal", goal_id, responsible, target_date, time, timezone)

    @app_commands.command(description="Review active, overdue or completed goals and outcome notes")
    @app_commands.choices(status=STATUS_CHOICES)
    async def review(self, interaction: discord.Interaction, member: discord.Member | None = None,
                     status: str = "active"):
        await staff_access(interaction)
        if member:
            player(member)
        await interaction.response.defer(ephemeral=True)
        guild, member_id = interaction.guild_id, member.id if member else None
        await send_workspace(interaction, "Development goals · " + status,
                             lambda db: records_stream(db, guild, "goal", status, member=member_id))

    @app_commands.command(description="Complete a goal with a required observed outcome note; scores remain unchanged")
    async def complete(self, interaction: discord.Interaction, goal_id: app_commands.Range[int, 1],
                       outcome: app_commands.Range[str, 3, 1000]):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.finish_staff_record(
            interaction.guild_id, interaction.user.id, "goal", goal_id, outcome)
        await acknowledge(interaction, f"Goal #{goal_id} completed with your outcome note. This records a staff "
                          "observation, not proven improvement; scouting scores were not changed." if changed else
                          "Goal already completed; the original completion note and record were preserved.")


class TaskCommands(StaffGroup):
    def __init__(self, bot):
        super().__init__(name="task", description="Private accountable staff actions")
        self.bot = bot

    @app_commands.command(description="Create a private staff task with a currently eligible accountable owner")
    @app_commands.describe(due_date="YYYY-MM-DD", time="24-hour HH:MM", timezone="IANA timezone")
    async def add(self, interaction: discord.Interaction, owner: discord.Member,
                  description: app_commands.Range[str, 3, 1000],
                  due_date: str, time: str, timezone: str):
        await staff_access(interaction)
        due = deadline(due_date, time, timezone)
        await interaction.response.defer(ephemeral=True)
        owner = await staff_assignee(interaction, owner)
        identifier = await self.bot.db.save_staff_record(
            interaction.guild_id, interaction.user.id, "task", owner.id, description, due, timezone)
        await acknowledge(interaction, f"Staff task #{identifier} created. No reminder or DM is scheduled.")

    @app_commands.command(description="Reassign an unfinished task or change its deadline; retain its ID and history")
    @app_commands.describe(owner="New owner: must currently be staff in this server",
                           due_date="YYYY-MM-DD; supply time and timezone too",
                           time="24-hour HH:MM", timezone="IANA timezone")
    async def edit(self, interaction: discord.Interaction, task_id: app_commands.Range[int, 1],
                   owner: discord.Member | None = None, due_date: str | None = None,
                   time: str | None = None, timezone: str | None = None):
        await edit_work(self.bot, interaction, "task", task_id, owner, due_date, time, timezone)

    @app_commands.command(description="Review active, overdue or completed staff tasks and their completion records")
    @app_commands.choices(status=STATUS_CHOICES)
    async def list(self, interaction: discord.Interaction, status: str = "active"):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild_id
        await send_workspace(interaction, "Staff tasks · " + status,
                             lambda db: records_stream(db, guild, "task", status))

    @app_commands.command(description="Complete a task once, retaining the accountable owner and completion record")
    async def complete(self, interaction: discord.Interaction, task_id: app_commands.Range[int, 1],
                       outcome: app_commands.Range[str, 3, 1000]):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        changed = await self.bot.db.finish_staff_record(
            interaction.guild_id, interaction.user.id, "task", task_id, outcome)
        await acknowledge(interaction, f"Task #{task_id} completed with your outcome note." if changed else
                          "Task already completed; original completion preserved. No duplicate record created.")


@app_commands.guild_only()
class AssistantCommands(StaffGroup):
    def __init__(self, bot):
        super().__init__(name="assistant", description="Private coach and owner briefing and staff actions")
        self.bot = bot
        self.add_command(TaskCommands(bot))

    @app_commands.command(description="Private on-demand seven-day briefing; facts separated from suggested actions")
    async def brief(self, interaction: discord.Interaction):
        await staff_access(interaction)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild_id
        await send_workspace(interaction, "Coach and owner briefing",
                             lambda db: briefing_stream(db, guild))
