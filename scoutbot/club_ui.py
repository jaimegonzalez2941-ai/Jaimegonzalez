"""Private club-operation responses and shared access checks."""

from datetime import datetime

import discord
from discord import app_commands

from .access import AccessDenied, staff_access
from . import presentation as ui

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def member_access(interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise AccessDenied("Use Club Agent inside your server, not in a direct message.")


async def target_member(interaction, member):
    member_access(interaction)
    member = member or interaction.user
    if member.bot:
        raise ValueError("Choose a player, not a bot.")
    if member.id != interaction.user.id:
        await staff_access(interaction)
    return member


class MemberGroup(app_commands.Group):
    async def interaction_check(self, interaction):
        member_access(interaction)
        return True


def card(title, description=""):
    result = ui.embed(title, description)
    result.set_footer(text="Club Agent • Club operations")
    return result


def time_tag(value, style="F"):
    return f"<t:{int(datetime.fromisoformat(value).timestamp())}:{style}>"


def event_text(row):
    state = "CANCELLED · " if row["cancelled"] else ""
    return (f'{state}**#{row["id"]} · {ui.text(row["title"], 100)}** · {row["kind"]}\n'
            f'{time_tag(row["starts_at"])} → {time_tag(row["ends_at"], "t")}\n'
            f'Source timezone: {ui.text(row["timezone"])}')


def attendance_text(summary):
    rate = f'{summary["rate"]:.1f}%' if summary["rate"] is not None else "Not enough marked data"
    return (f'**{rate}** · {summary["attended"]}/{summary["eligible"]} eligible sessions attended\n'
            f'Present: {summary["present"]} · Late: {summary["late"]} · Absent: {summary["absent"]}\n'
            f'Excluded: {summary["excused"]} excused · {summary["leave"]} approved leave · '
            f'{summary["unmarked"]} unmarked\n'
            'Only completed, non-cancelled sessions you committed to or were assigned to count. '
            'Late counts as attended and is reported separately. No automatic no-shows.')


def promotion_text(evidence):
    policy = evidence["policy"]
    evaluation = evidence["evaluation"]
    score = f'{evaluation["score"]:.1f}/10 · {evaluation["created_at"][:10]}' if evaluation else "No recent saved evaluation"
    return (f'**{"Meets evidence standard — staff decision required" if evidence["ready"] else "Not ready for recommendation"}**\n'
            f'Policy: {policy["percent"]}% over {policy["days"]} days, '
            f'at least {policy["minimum"]} marked eligible sessions; latest scouting score ≥8.0.\n'
            f'Latest scouting evaluation: {score}\n'
            + attendance_text(evidence["attendance"]) + "\n"
            + ("\n".join("• " + reason for reason in evidence["reasons"]) or
               "Performance and attendance meet the configured minimums; this is not automatic promotion."))


@app_commands.command(name="club", description="Club Agent command guide for players and staff")
@app_commands.guild_only()
async def club_help(interaction: discord.Interaction):
    member_access(interaction)
    await interaction.response.send_message(embed=card(
        "Club Agent",
        "**Players**\n"
        "`/availability set` · Weekly availability, using your timezone\n"
        "`/availability view` · Your weekly windows\n"
        "`/availability clear` · Remove a weekday\n"
        "`/availability leave` · Request vacation/time away\n"
        "`/availability leaves` · Check approval status\n"
        "`/availability cancel_leave` · Cancel your leave\n"
        "`/schedule list` · Upcoming or past sessions\n"
        "`/schedule show` · Session details and your RSVP\n"
        "`/schedule rsvp` · Yes / maybe / no before kickoff\n"
        "`/attendance report` · Your attendance\n\n"
        "**Staff**\n"
        "`/schedule create`, `edit`, `cancel`, `assign`, `roster`, `notifications`\n"
        "`/availability roster`, `review_leave`\n"
        "`/attendance mark` · Present / late / absent / excused / unmarked\n"
        "`/promotion review` · Combined performance and attendance\n"
        "`/promotion policy` · Managers adjust attendance rules\n"
        "`/scout help` · Scouting, evaluations and recruitment roles\n\n"
        "Approved leave and excused absences do not reduce attendance. "
        "Declaring limited weekly availability alone never reduces a scouting score."
    ), ephemeral=True)
