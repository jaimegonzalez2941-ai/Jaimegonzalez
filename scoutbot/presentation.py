"""Bounded Discord embeds. Scouting records are never exposed through a web API."""

import discord
import json
from decimal import Decimal

from .rubric import CATEGORIES, LABELS, WEIGHTS, calculate, coverage


def text(value: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def embed(title: str, description: str = "", *, color: int = 0x2B8C7F) -> discord.Embed:
    result = discord.Embed(title=title[:256], description=description[:4000], color=color)
    result.set_footer(text="Club Agent • Staff only")
    return result


def docket(rows: list[dict]) -> discord.Embed:
    result = embed("Scouting docket", "The five most recent evaluations in this server. Recommendations are not recruitment stages or role authorization.")
    for row in rows:
        result.add_field(
            name=f'{text(row["candidate_name"], 90)} · {row["position"]}',
            value=record_summary(row, details=False) + f'\n{row["created_at"][:10]}',
            inline=False,
        )
    if not rows:
        result.description = "No evaluations yet. Start with `/scout evaluate`."
    result.timestamp = discord.utils.utcnow()
    return result


def history(rows: list[dict], member: discord.Member, page: int) -> discord.Embed:
    result = embed(f"Trial dossier · {text(member.display_name, 90)}", f"Page {page + 1} · Five evaluations per page")
    for row in rows:
        result.add_field(
            name=f'#{row["id"]} · {row["position"]} · {row["score"]:.1f}/10',
            value=record_summary(row, details=True)[:880] + '\n'
                  f'Scout: {text(row["scout_name"], 100)} · {row["created_at"][:10]}',
            inline=False,
        )
    return result


def record_summary(row: dict, *, details: bool) -> str:
    recommendation = "Trial-ready for staff consideration" if row["score"] >= 8 else "Developmental"
    if not row.get("rubric_version"):
        return (f'Legacy single-score entry · **{row["score"]:.1f}/10** · {recommendation}\n'
                'No category scores or declared coverage recorded; not retrospectively recalculated.\n'
                + (text(row["notes"], 450) if details else "No recruitment role change."))
    data = json.loads(row["scorecard"])
    lines = [
        f'Weighted scouting rating: **{row["score"]:.1f}/10** · {row["rubric_version"]}',
        f'Recommendation: {recommendation} (not a role assignment)',
        f'Declared: {data["count"]} matches/sessions · {coverage(data["count"])} coverage; not statistical confidence.',
    ]
    for key, label, weight in zip(CATEGORIES, LABELS, WEIGHTS[row["position"]]):
        entry = data["categories"][key]
        line = f'{label}: {entry["score"]} × {weight}%'
        if details:
            line += " — " + text(entry["evidence"], 65)
        lines.append(line)
    if details:
        lines += ["Context: " + text(data["context"], 90), "Notes: " + text(data["notes"], 110)]
    return "\n".join(lines)


def scorecard(row: dict) -> discord.Embed:
    data = json.loads(row["payload"])
    result = calculate(row["position"], data["categories"], row["rubric_version"])
    summary = "Incomplete — assess all five categories; no rating yet."
    if result:
        summary = (f'Raw weighted total: {result.raw} → **{result.score:.1f}/10** (half-up to 1 decimal)\n'
                   f'Recommendation: {result.recommendation}')
        if result.borderline:
            summary += "\nCannot save: the raw or rounded total is in 6.8–7.2. Reassess underlying observations or discard; never snap the score."
    coverage_text = (f'{data["count"]} declared matches/sessions · {coverage(data["count"])}'
                     if data["count"] else "Not declared — positive count required to save")
    card = embed(
        f'Draft · {text(row["candidate_name"], 90)} · {row["position"]}',
        f'{summary}\nCoverage: {coverage_text}. Declared coverage is not verified statistical confidence.\n'
        f'Rubric: {row["rubric_version"]} · Revision {row["revision"]}\n'
        'Review/correct before Confirm & save. Nothing is published yet. No recruitment role will change. '
        'EA match ratings, imported record counts and recruitment stages are separate. '
        'Use Trusted match context to view imported records; they never enter this calculation.',
    )
    for key, label, weight in zip(CATEGORIES, LABELS, WEIGHTS[row["position"]]):
        entry = data["categories"].get(key, {})
        score = entry.get("score")
        contribution = str(Decimal(score) * weight / 100) if score else "—"
        card.add_field(
            name=f'{label} · {weight}% · {score or "Unassessed"}',
            value=f'Contribution: {contribution}\n{text(entry.get("evidence") or "No observations yet", 500)}',
            inline=False)
    card.add_field(name="Observation context", value=text(data["context"] or "Not entered", 500), inline=False)
    card.add_field(name="Summary notes", value=text(data["notes"] or "Not entered", 1000), inline=False)
    return card


def metric(value, *, percent: bool = False) -> str:
    if value is None:
        return "Not reported"
    rendered = f"{value:.1f}" if isinstance(value, float) else str(value)
    return rendered + ("%" if percent else "")


def stats(rows: list[dict], member: discord.Member, page: int, totals: dict) -> discord.Embed:
    result = embed(
        f"Match performance · {text(member.display_name, 90)}",
        f'Page {page + 1} · {totals["matches"]} verified imported player records (not declared observations)\n'
        f'Average EA match rating: **{metric(totals["rating"])}** · '
        f'Reported goals: **{metric(totals["goals"])}** · '
        f'Reported assists: **{metric(totals["assists"])}**\n'
        "Missing metrics are excluded from averages. Totals cover reported values only. "
        "Dates reflect the source message, not a verified match kickoff. "
        "Trusted-source records are not independent verification of EA data. "
        "These statistics are supporting context only, never blended into human scouting scores.",
    )
    for row in rows:
        result.add_field(
            name=f'{row["match_date"][:10]} · {text(row["player_handle"], 100)}',
            value=(
                f'EA match rating: {metric(row["rating"])} · Goals: {metric(row["goals"])} · '
                f'Assists: {metric(row["assists"])}\n'
                f'Pass accuracy: {metric(row["passing_acc"], percent=True)} · '
                f'Tackles: {metric(row["tackles"])}\n'
                f'[Source recap](https://discord.com/channels/{row["guild_id"]}/'
                f'{row["channel_id"]}/{row["message_id"]})'
            ),
            inline=False,
        )
    return result
