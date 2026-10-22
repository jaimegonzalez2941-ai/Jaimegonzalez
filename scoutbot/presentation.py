"""Bounded Discord embeds. Scouting records are never exposed through a web API."""

import discord


def text(value: str, limit: int = 200) -> str:
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


def embed(title: str, description: str = "", *, color: int = 0x2B8C7F) -> discord.Embed:
    result = discord.Embed(title=title[:256], description=description[:4000], color=color)
    result.set_footer(text="Scout V2 • Staff only")
    return result


def docket(rows: list[dict]) -> discord.Embed:
    result = embed("Scouting docket", "The five most recent evaluations in this server.")
    for row in rows:
        status = "Trial-ready" if row["score"] >= 8 else "Developmental"
        result.add_field(
            name=f'{text(row["candidate_name"], 90)} · {row["position"]}',
            value=f'**{row["score"]:.1f}/10** · {status}\n{row["created_at"][:10]}',
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
            value=f'{text(row["notes"], 750)}\n'
                  f'Scout: {text(row["scout_name"], 100)} · {row["created_at"][:10]}',
            inline=False,
        )
    return result


def metric(value, *, percent: bool = False) -> str:
    if value is None:
        return "Not reported"
    rendered = f"{value:.1f}" if isinstance(value, float) else str(value)
    return rendered + ("%" if percent else "")


def stats(rows: list[dict], member: discord.Member, page: int, totals: dict) -> discord.Embed:
    result = embed(
        f"Match performance · {text(member.display_name, 90)}",
        f'Page {page + 1} · {totals["matches"]} player appearances\n'
        f'Average rating: **{metric(totals["rating"])}** · '
        f'Reported goals: **{metric(totals["goals"])}** · '
        f'Reported assists: **{metric(totals["assists"])}**\n'
        "Missing metrics are excluded from averages. Totals cover reported values only. "
        "Dates reflect the source message, not a verified match kickoff.",
    )
    for row in rows:
        result.add_field(
            name=f'{row["match_date"][:10]} · {text(row["player_handle"], 100)}',
            value=(
                f'Rating: {metric(row["rating"])} · Goals: {metric(row["goals"])} · '
                f'Assists: {metric(row["assists"])}\n'
                f'Pass accuracy: {metric(row["passing_acc"], percent=True)} · '
                f'Tackles: {metric(row["tackles"])}\n'
                f'[Source recap](https://discord.com/channels/{row["guild_id"]}/'
                f'{row["channel_id"]}/{row["message_id"]})'
            ),
            inline=False,
        )
    return result
