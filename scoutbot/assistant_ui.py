"""Private paginated staff workspace presentation; no ratings are recalculated."""

import json

import discord

from .access import AccessDenied, staff_access
from .club_ui import attendance_text, time_tag
from .presentation import embed, metric, text
from .rubric import CATEGORIES, LABELS, VERSION, coverage
from .views import SecureView


def escaped(value):
    """Preserve full bounded saved observations even when escaping expands them."""
    value = str(value)
    return text(value, len(value) * 3 + 20)


def pages(title, blocks):
    """No silent embed truncation: overflow continues on another page."""
    descriptions, current = [], ""
    for block in blocks:
        if current and len(block) <= 3500 and len(current) + len(block) + 2 > 3500:
            descriptions.append(current)
            current = ""
        # Split large legacy notes too; saved data can predate current text limits.
        while block:
            space = 3500 - len(current) - (2 if current else 0)
            if space <= 0:
                descriptions.append(current)
                current = ""
                continue
            piece, block = block[:space], block[space:]
            current += ("\n\n" if current else "") + piece
            if block:
                descriptions.append(current)
                current = ""
    if current:
        descriptions.append(current)
    result = []
    for index, description in enumerate(descriptions or ["No reported records."]):
        item = embed(title, description)
        item.set_footer(text=f"Club Agent • Staff only • Page {index + 1}/{len(descriptions) or 1}")
        result.append(item)
    return result


class StaffPages(SecureView):
    def __init__(self, owner, guild, items):
        super().__init__(timeout=600)
        self.owner, self.guild, self.items, self.index = owner, guild, items, 0
        self.controls()

    def controls(self):
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.items) - 1

    async def interaction_check(self, interaction):
        await staff_access(interaction)
        if interaction.user.id != self.owner or interaction.guild.id != self.guild:
            raise AccessDenied("Open your own private workspace view in this server.")
        return True

    async def move(self, interaction, offset):
        # Defense in depth for direct callback execution as well as Discord dispatch.
        await self.interaction_check(interaction)
        self.index = max(0, min(len(self.items) - 1, self.index + offset))
        self.controls()
        await interaction.response.edit_message(
            embed=self.items[self.index], view=self, allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Previous")
    async def previous(self, interaction, button):
        await self.move(interaction, -1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction, button):
        await self.move(interaction, 1)


async def send_pages(interaction, title, blocks):
    items = pages(title, blocks)
    view = StaffPages(interaction.user.id, interaction.guild.id, items) if len(items) > 1 else None
    await interaction.followup.send(embed=items[0], view=view, ephemeral=True,
                                    allowed_mentions=discord.AllowedMentions.none())


def record_blocks(row):
    kind = {"recruitment": "Recruitment follow-up", "goal": "Development goal", "task": "Staff task"}[row["kind"]]
    lines = [f'**{kind} #{row["id"]} · {row["status"]}**']
    if row["member_id"]:
        lines.append(f'Player ID: {row["member_id"]}')
    if row["position"]:
        lines.append("Target position: " + escaped(row["position"]))
    lines += [f'Responsible staff ID: {row["owner_id"]}',
              f'Deadline: {time_tag(row["due_at"])} · source timezone: {escaped(row["timezone"])}',
              "Next action / description: " + escaped(row["description"])]
    if row["criteria"]:
        lines.append("Observable success criteria: " + escaped(row["criteria"]))
    lines.append(f'Created by staff ID {row["created_by"]}: {time_tag(row["created_at"])}'
                 f' · Updated: {time_tag(row["updated_at"])}')
    if row["completed_at"]:
        lines.append(f'Recorded {row["status"]} by staff ID {row["completed_by"]}: '
                     f'{time_tag(row["completed_at"])}')
    if row["outcome"]:
        lines.append("Outcome note: " + escaped(row["outcome"]))
    return ["\n".join(lines)]


def evaluation_blocks(row, *, show_values=True):
    heading = (f'**Saved evaluation #{row["id"]} · {escaped(row["position"])}**\n'
               f'Saved: {time_tag(row["created_at"])} · Scout: {escaped(row["scout_name"])}\n')
    version = row.get("rubric_version")
    if not version or not row.get("scorecard"):
        return [heading + "Legacy single-score record; categories, observation dates and declared coverage "
                "are unreported. No subscores invented.\n"
                + (f'Saved human rating: {row["score"]:.1f}/10\n' if show_values else
                   "Numeric comparison withheld.\n")
                + "Full saved notes: " + escaped(row["notes"])]
    data = json.loads(row["scorecard"])
    count = data.get("count")
    label = coverage(count) if type(count) is int and 1 <= count <= 10000 else "Unreported"
    lines = [heading + f'Rubric: {escaped(version)}'
             + (" · unsupported/incompatible rubric; not recalculated." if version != VERSION else ""),
             f'Declared observed sessions: {count if count is not None else "Unreported"} · {label} coverage.',
             "Coverage is a staff declaration, not statistical confidence.",
             "Structured observation dates: Unreported; the saved timestamp is not an observation date.",
             "Observation dates / context (staff-entered, not independently verified): "
             + escaped(data.get("context") or "Unreported")]
    if show_values:
        lines.append(f'Saved human rating: {row["score"]:.1f}/10 (not a recruitment decision)')
    else:
        lines.append("Numeric category comparison withheld: legacy, missing or incompatible rubric records.")
    blocks = ["\n".join(lines)]
    for key, label in zip(CATEGORIES, LABELS):
        entry = data.get("categories", {}).get(key, {})
        blocks.append(
            f'**Evaluation #{row["id"]} · {label}**\n'
            + (f'Saved category: {escaped(entry.get("score") or "Unreported")}\n' if show_values else
               "Category value withheld from comparison.\n")
            + "Observed evidence: " + escaped(entry.get("evidence") or "Unreported"))
    blocks.append(f'**Evaluation #{row["id"]} · Full saved notes**\n'
                  + escaped(data.get("notes") or row.get("notes") or "Unreported"))
    return blocks


def trusted_block(samples):
    lines = ["**Trusted-source match statistics — separate from human ratings**",
             f'Imported player rows: {samples["records"]} · distinct source recaps: {samples["recaps"]}',
             "Metric samples are reported player rows, not necessarily unique matches or independent observations."]
    for key, label in (("rating", "Mean EA rating"), ("goals", "Reported goals total"),
                       ("assists", "Reported assists total"), ("passing_acc", "Mean passing accuracy"),
                       ("tackles", "Reported tackles total")):
        lines.append(f'{label}: {metric(samples[key], percent=key == "passing_acc")} '
                     f'· sample n={samples[key + "_n"]}')
    lines.append("Missing metrics remain unreported; totals cover reported values only. "
                 "Source dates are message dates, not verified kickoff or observation dates. "
                 "Trusted sources are not independent verification of EA data.")
    return "\n".join(lines)


async def dossier_blocks(db, guild, member):
    blocks = [f'**Player dossier · {escaped(member.display_name)} · ID {member.id}**\n'
              "Saved records only; private drafts are never included. Unavailable data is unreported."]
    policy = await db.policy(guild)
    summary = await db.attendance_summary(guild, member.id, policy["days"])
    blocks.append(f'**Attendance · current {policy["days"]}-day policy window**\n'
                  f'Policy: {policy["percent"]}% / minimum {policy["minimum"]} marked eligible sessions.\n'
                  + attendance_text(summary))
    blocks.append(trusted_block(await db.trusted_samples(guild, member.id)))
    stats = await db.all(
        """SELECT s.*,r.match_date,r.channel_id FROM match_stats s
        JOIN player_links l ON l.guild_id=s.guild_id AND l.handle_key=s.handle_key
        JOIN recap_receipts r ON r.guild_id=s.guild_id AND r.message_id=s.message_id
        WHERE s.guild_id=? AND l.discord_id=? ORDER BY r.match_date DESC,s.id DESC""",
        (str(guild), str(member.id)))
    for row in stats:
        blocks.append(
            f'**Trusted imported row #{row["id"]} · {escaped(row["player_handle"])}**\n'
            f'Source message date: {time_tag(row["match_date"])}\n'
            f'EA rating: {metric(row["rating"])} · Goals: {metric(row["goals"])} · '
            f'Assists: {metric(row["assists"])} · Passing: {metric(row["passing_acc"], percent=True)} · '
            f'Tackles: {metric(row["tackles"])}\n'
            f'[Source recap](https://discord.com/channels/{guild}/{row["channel_id"]}/{row["message_id"]})')
    evaluations = await db.all(
        "SELECT * FROM evaluations WHERE guild_id=? AND candidate_id=? ORDER BY id DESC",
        (str(guild), str(member.id)))
    if not evaluations:
        blocks.append("**Saved human evaluations**\nUnreported; no saved evaluations.")
    for row in evaluations:
        blocks.extend(evaluation_blocks(row))
    for kind, heading in (("recruitment", "Recruitment follow-ups"), ("goal", "Development goals")):
        rows = await db.staff_records(guild, kind, "all", member=member.id)
        blocks.append(f'**{heading}**' + ("" if rows else "\nUnreported; no tracked records."))
        for row in rows:
            blocks.extend(record_blocks(row))
    return blocks


async def comparison_blocks(db, guild, first, second, position):
    a = await db.latest_position_evaluation(guild, first.id, position)
    b = await db.latest_position_evaluation(guild, second.id, position)
    compatible = bool(a and b and a.get("scorecard") and b.get("scorecard")
                      and a.get("rubric_version") == b.get("rubric_version") == VERSION)
    blocks = [f'**{escaped(position)} · latest saved evaluation per player**\n'
              + ("Compatible rubric versions; category values may be read alongside evidence."
                 if compatible else
                 "Legacy, missing or incompatible rubric versions: numeric category comparison is withheld.")
              + "\nNo overall winner, inferred potential or statistical confidence. "
              "Different observation contexts and coverage are not controlled comparisons."]
    for member, row in ((first, a), (second, b)):
        blocks.append(f'**{escaped(member.display_name)} · Player ID {member.id}**')
        blocks.extend(evaluation_blocks(row, show_values=compatible) if row else
                      ["Evaluation, observation dates and coverage: Unreported at this position."])
        blocks.append(trusted_block(await db.trusted_samples(guild, member.id)))
    return blocks


def briefing_blocks(data):
    blocks = [
        f'**Recorded facts · {time_tag(data["now"])} to {time_tag(data["end"])} (end exclusive)**\n'
        "On-demand snapshot, not a notification or automatic decision. Due soon = within this seven-day window; "
        "overdue = before its start. All pending leave decisions are included, even outside this window.",
        "**Upcoming sessions**\nCounts cover recorded roster responses only, not every server member. "
        "Pending means an explicit pending roster record; unknown members are not inferred."]
    for row in data["sessions"]:
        blocks.append(f'**Session #{row["id"]} · {escaped(row["title"])}** · {time_tag(row["starts_at"])}\n'
                      f'Yes: {row["yes_count"]} · Assigned: {row["assigned_count"]} · '
                      f'Maybe: {row["maybe_count"]} · No: {row["no_count"]} · Pending: {row["pending_count"]}\n'
                      f'Next lookup: `/schedule roster session_id:{row["id"]}`')
    if not data["sessions"]:
        blocks.append("No upcoming sessions recorded in this window.")
    blocks.append("**Pending leave decisions**")
    for row in data["leave"]:
        blocks.append(f'Leave #{row["id"]} · Player ID {row["member_id"]}\n'
                      f'{time_tag(row["starts_at"])} → {time_tag(row["ends_at"])}\n'
                      "Decision command: `/availability review_leave` · No private reasons displayed.")
    if not data["leave"]:
        blocks.append("No pending leave decisions recorded.")
    blocks.append(f'**Incomplete attendance · current {data["policy"]["days"]}-day policy window**\n'
                  "Only committed/assigned players in completed, active sessions; approved leave and excused "
                  "records are protected. Unmarked is not absent.")
    for row in data["gaps"]:
        blocks.append(f'Session #{row["id"]} · {escaped(row["title"])} · {time_tag(row["starts_at"])}\n'
                      f'Player ID {row["member_id"]}: attendance unmarked. Lookup: `/attendance mark`.')
    if not data["gaps"]:
        blocks.append("No eligible incomplete attendance records in this policy window.")
    for kind, heading in (("task", "Staff tasks"), ("recruitment", "Recruitment reviews"),
                          ("goal", "Development goals")):
        blocks.append(f'**{heading} · overdue / due soon**')
        rows = [row for row in data["followups"] if row["kind"] == kind]
        for row in rows:
            blocks.append("**" + ("Overdue" if row["due_at"] < data["now"] else "Due soon") + "**")
            blocks.extend(record_blocks(row))
        if not rows:
            blocks.append("No overdue or due-soon active records.")
    blocks.append(
        "**Suggested next actions — not recorded conclusions**\n"
        "Review session rosters and ask for clarification where needed; do not treat maybe, pending or declared "
        "availability as a commitment. Review pending leave in `/availability review_leave`. Verify attendance "
        "before entering a mark; never infer a missed session. Follow up with the responsible staff IDs on "
        "`/assistant task list`, `/scout shortlist list` and `/development review`. "
        "Recruitment deadlines are follow-ups, not rejection/promotion. Goal completion records a staff "
        "observation, not proven improvement; no scores or roles change.")
    return blocks
