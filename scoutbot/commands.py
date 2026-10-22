"""Staff-facing slash commands. All sensitive responses are ephemeral."""

import json

import discord
from discord import app_commands

from .access import (
    AccessDenied, manager_access, private_channel, safe_recruitment_role, staff_access,
)
from .domain import STAGES
from .ingestion import import_message
from . import presentation as ui
from .views import DocketView, HistoryView, PositionView
from .assistant_commands import ShortlistCommands, POSITION_CHOICES, player
from .assistant_ui import comparison_blocks, dossier_blocks, send_pages

STAGE_CHOICES = [app_commands.Choice(name=s, value=s) for s in STAGES]


@app_commands.guild_only()
class ScoutCommands(app_commands.Group):
    def __init__(self, bot):
        super().__init__(name="scout", description="Private scouting, recruitment, and match performance")
        self.bot = bot
        self.add_command(ShortlistCommands(bot))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await staff_access(interaction)
        return True

    @app_commands.command(description="Read a unified private player dossier with full saved evidence and follow-ups")
    async def player(self, interaction: discord.Interaction, member: discord.Member):
        await staff_access(interaction)
        player(member)
        await interaction.response.defer(ephemeral=True)
        await send_pages(interaction, "Player dossier",
                         await dossier_blocks(self.bot.db, interaction.guild_id, member))

    @app_commands.command(description="Compare latest saved position evaluations; compatible rubric evidence only")
    @app_commands.choices(position=POSITION_CHOICES)
    async def compare(self, interaction: discord.Interaction, first: discord.Member,
                      second: discord.Member, position: str):
        await staff_access(interaction)
        player(first)
        player(second)
        if first.id == second.id:
            raise ValueError("Choose two different players.")
        await interaction.response.defer(ephemeral=True)
        await send_pages(interaction, "Position evidence comparison",
                         await comparison_blocks(self.bot.db, interaction.guild_id, first, second, position))

    @app_commands.command(description="Configure this server's staff access and trusted recap source")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        staff_role="Role allowed to use scouting commands",
        staff_channel="Private staff-only text channel for the docket",
        recap_channel="Text channel where the recap bot or webhook posts",
        source_bot="Choose a trusted bot, OR provide a webhook ID, not both",
        webhook_id="Trusted webhook ID (not a webhook URL or token)",
    )
    async def setup(
        self, interaction: discord.Interaction, staff_role: discord.Role,
        staff_channel: discord.TextChannel, recap_channel: discord.TextChannel,
        source_bot: discord.Member | None = None, webhook_id: str | None = None,
    ):
        manager_access(interaction)
        if staff_role.is_default():
            raise ValueError("Choose a dedicated staff role, not @everyone.")
        if bool(source_bot) == bool(webhook_id):
            raise ValueError("Choose exactly one source: a bot member OR a webhook ID.")
        if source_bot and (not source_bot.bot or source_bot.id == self.bot.user.id):
            raise ValueError("The trusted source must be another bot, not a user or this bot.")
        if webhook_id:
            webhook_id = webhook_id.strip()
            if not webhook_id.isdecimal() or not 15 <= len(webhook_id) <= 22:
                raise ValueError("Enter only the webhook's numeric ID, never its URL or token.")
        private_channel(staff_channel, staff_role)
        if not staff_channel.permissions_for(staff_role).view_channel:
            raise ValueError("The staff role must be able to view the staff channel.")
        recap_permissions = recap_channel.permissions_for(interaction.guild.me)
        if not recap_permissions.view_channel or not recap_permissions.read_message_history:
            raise ValueError("The bot needs View Channel and Read Message History in the recap channel.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.db.configure(
            interaction.guild.id, interaction.user.id,
            staff_role=staff_role.id, staff_channel=staff_channel.id,
            recap_channel=recap_channel.id,
            source_kind="bot" if source_bot else "webhook",
            source_id=source_bot.id if source_bot else webhook_id,
        )
        await interaction.followup.send(
            "Server configuration saved. Scouting records are private. "
            "Next, map recruitment roles with `/scout stage_role` and player handles with `/scout link`.",
            ephemeral=True,
        )

    @app_commands.command(description="Choose a candidate's position and record a private evaluation")
    @app_commands.checks.cooldown(2, 10, key=lambda i: (i.guild_id, i.user.id))
    async def evaluate(self, interaction: discord.Interaction, candidate: discord.Member):
        if candidate.bot:
            raise ValueError("Select a player, not a bot.")
        await interaction.response.send_message(
            embed=ui.embed("Evaluate candidate", f"Choose a position for **{ui.text(candidate.display_name)}**."),
            view=PositionView(candidate, interaction.user.id, interaction.guild.id), ephemeral=True,
        )

    @app_commands.command(description="Read a player's paginated scouting dossier and notes")
    async def history(self, interaction: discord.Interaction, candidate: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.db.history(interaction.guild.id, candidate.id)
        if not rows:
            await interaction.followup.send("No evaluations for this player yet.", ephemeral=True)
            return
        view = HistoryView(interaction.user.id, candidate, "history")
        view.next.disabled = len(rows) < 5
        await interaction.followup.send(embed=ui.history(rows, candidate, 0), view=view, ephemeral=True)

    @app_commands.command(description="Map a recruitment stage to an existing, safe server role")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(stage=STAGE_CHOICES)
    async def stage_role(self, interaction: discord.Interaction, stage: str, role: discord.Role):
        manager_access(interaction)
        safe_recruitment_role(role, interaction.user)
        config = await self.bot.db.settings(interaction.guild.id)
        if config and str(role.id) == config["staff_role"]:
            raise AccessDenied("A recruitment role cannot also be the scouting staff role.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.db.set_stage_role(interaction.guild.id, interaction.user.id, stage, role.id)
        await interaction.followup.send(f"**{stage}** now uses **{ui.text(role.name)}**.", ephemeral=True)

    @app_commands.command(description="Add a configured recruitment role to a candidate (does not remove roles)")
    @app_commands.describe(override_reason="Staff justification if development-tier evidence is insufficient (10–500 characters)")
    @app_commands.choices(role=STAGE_CHOICES)
    @app_commands.checks.cooldown(3, 10, key=lambda i: (i.guild_id, i.user.id))
    async def assign(self, interaction: discord.Interaction, candidate: discord.Member, role: str,
                     override_reason: app_commands.Range[str, 10, 500] | None = None):
        if not interaction.user.guild_permissions.manage_roles:
            raise AccessDenied("Assigning roles also requires your Manage Roles permission.")
        if candidate.bot:
            raise ValueError("Choose a player, not a bot.")
        config = await self.bot.db.settings(interaction.guild.id)
        row = await self.bot.db.one(
            "SELECT role_id FROM stage_roles WHERE guild_id=? AND stage=?",
            (str(interaction.guild.id), role),
        )
        selected = interaction.guild.get_role(int(row["role_id"])) if row else None
        if selected is None:
            raise ValueError("A manager must configure this stage with /scout stage_role first.")
        safe_recruitment_role(selected, interaction.user)
        if config and str(selected.id) == config["staff_role"]:
            raise AccessDenied("The configured stage role now grants scouting access. Choose a different role.")
        if candidate.id == interaction.guild.owner_id:
            raise AccessDenied("Recruitment roles cannot be assigned to the server owner.")
        if candidate.top_role >= interaction.guild.me.top_role:
            raise AccessDenied("The candidate's highest role must be below the bot's.")
        if interaction.user.id != interaction.guild.owner_id and candidate.top_role >= interaction.user.top_role:
            raise AccessDenied("The candidate's highest role must be below yours.")
        if selected in candidate.roles:
            await interaction.response.send_message("The candidate already has this role.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        evidence = await self.bot.db.promotion_evidence(interaction.guild.id, candidate.id)
        if role != STAGES[0] and not evidence["ready"] and not (override_reason and len(override_reason.strip()) >= 10):
            raise ValueError(
                "Promotion evidence is not sufficient: " + " ".join(evidence["reasons"]) +
                " Review /promotion review. Staff may deliberately override with an override_reason (10–500 characters).")
        await candidate.add_roles(selected, reason=f"Club Agent assignment by {interaction.user.id}")
        try:
            await self.bot.db.audit(
                interaction.guild.id, interaction.user.id, "role.assigned",
                {"candidate": str(candidate.id), "role": str(selected.id), "stage": role,
                 "promotion_ready": evidence["ready"], "attendance": evidence["attendance"],
                 "policy": evidence["policy"], "override_reason": override_reason},
            )
        except Exception:
            await interaction.followup.send(
                "Discord assigned the role, but the local audit record could not be saved. "
                "Discord's server audit log contains the assignment.", ephemeral=True,
            )
            raise
        await interaction.followup.send(
            f"Added **{ui.text(selected.name)}** to **{ui.text(candidate.display_name)}**. "
            "Existing roles were not removed. "
            + ("Staff override was recorded." if role != STAGES[0] and not evidence["ready"] else
               "Attendance and scouting evidence were recorded in the audit."),
            ephemeral=True,
        )

    @app_commands.command(description="Create or update the restart-safe docket in the private staff channel")
    @app_commands.checks.cooldown(1, 15, key=lambda i: i.guild_id)
    async def docket(self, interaction: discord.Interaction):
        config = await self.bot.db.settings(interaction.guild.id)
        if not config:
            raise ValueError("A server manager must run /scout setup first.")
        channel = interaction.guild.get_channel(int(config["staff_channel"]))
        role = interaction.guild.get_role(int(config["staff_role"]))
        if not isinstance(channel, discord.TextChannel) or role is None:
            raise ValueError("The configured channel or staff role is missing. Run /scout setup again.")
        private_channel(channel, role)
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = ui.docket(await self.bot.db.recent(interaction.guild.id))
        old = await self.bot.db.one(
            "SELECT message_id FROM dockets WHERE guild_id=? AND channel_id=?",
            (str(interaction.guild.id), str(channel.id)),
        )
        message = None
        if old:
            try:
                message = await channel.fetch_message(int(old["message_id"]))
                await message.edit(embed=result, view=DocketView())
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=result, view=DocketView())
            await self.bot.db.set_docket(interaction.guild.id, channel.id, message.id)
        await interaction.followup.send(f"Staff docket updated: {message.jump_url}", ephemeral=True)

    @app_commands.command(description="Link an EA handle; earlier imported matches become visible too")
    async def link(self, interaction: discord.Interaction, ea_handle: str, member: discord.Member):
        if member.bot:
            raise ValueError("Link a player's Discord account, not a bot.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self.bot.db.link(interaction.guild.id, interaction.user.id, ea_handle, member.id)
        await interaction.followup.send(
            f"Linked **{ui.text(ea_handle)}** to **{ui.text(member.display_name)}**.", ephemeral=True,
        )

    @app_commands.command(description="Unlink an EA handle without deleting its recorded match statistics")
    async def unlink(self, interaction: discord.Interaction, ea_handle: str, confirm: bool = False):
        if not confirm:
            raise ValueError(
                "Unlinking hides this handle's matches from the member, but preserves the statistics. "
                "Run again with confirm:True to unlink."
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        removed = await self.bot.db.unlink(interaction.guild.id, interaction.user.id, ea_handle)
        await interaction.followup.send(
            "Handle unlinked. Match data was preserved." if removed else "That handle is not linked.",
            ephemeral=True,
        )

    @app_commands.command(description="Read a player's match history and reported aggregate statistics")
    async def stats(self, interaction: discord.Interaction, candidate: discord.Member):
        await interaction.response.defer(ephemeral=True, thinking=True)
        rows = await self.bot.db.stats(interaction.guild.id, candidate.id)
        if not rows:
            await interaction.followup.send(
                "No linked matches found. Use `/scout link` and check `/scout status` for recap setup.",
                ephemeral=True,
            )
            return
        totals = await self.bot.db.aggregate(interaction.guild.id, candidate.id)
        view = HistoryView(interaction.user.id, candidate, "stats")
        view.next.disabled = len(rows) < 5
        await interaction.followup.send(
            embed=ui.stats(rows, candidate, 0, totals), view=view, ephemeral=True,
        )

    @app_commands.command(description="Import or retry a trusted recap by message ID in the configured channel")
    @app_commands.checks.cooldown(2, 15, key=lambda i: (i.guild_id, i.user.id))
    async def ingest(self, interaction: discord.Interaction, message_id: str):
        config = await self.bot.db.settings(interaction.guild.id)
        if not config:
            raise ValueError("Configure the trusted recap source with /scout setup first.")
        if not message_id.isdecimal() or not 15 <= len(message_id) <= 22:
            raise ValueError("Enter the numeric message ID copied from Discord.")
        channel = interaction.guild.get_channel(int(config["recap_channel"]))
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("The configured recap channel is missing. Run /scout setup again.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await channel.fetch_message(int(message_id))
        inserted, count = await import_message(self.bot, message, interaction.user.id)
        await interaction.followup.send(
            f"Imported {count} player rows." if inserted
            else "This recap was already imported. No duplicate records were created.",
            ephemeral=True,
        )

    @app_commands.command(description="Inspect server configuration, bot health, and recent recap outcomes")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.bot.db.settings(interaction.guild.id)
        result = ui.embed("Club Agent status")
        result.add_field(name="Gateway", value=f"Connected · {self.bot.latency * 1000:.0f} ms")
        result.add_field(name="Recap listening", value="Enabled" if self.bot.settings.recaps_enabled else "Disabled")
        if config:
            result.add_field(
                name="Configuration",
                value=f'Staff role: <@&{config["staff_role"]}>\n'
                      f'Staff channel: <#{config["staff_channel"]}>\n'
                      f'Recap channel: <#{config["recap_channel"]}>\n'
                      f'Trusted {config["source_kind"]} ID: `{config["source_id"]}`', inline=False,
            )
        else:
            result.description = "Not configured yet. A manager should run `/scout setup`."
        events = await self.bot.db.all(
            """SELECT action,details,created_at FROM audit_log WHERE guild_id=?
            AND action IN ('recap.imported','recap.rejected') ORDER BY id DESC LIMIT 3""",
            (str(interaction.guild.id),),
        )
        for event in events:
            details = json.loads(event["details"])
            result.add_field(
                name=f'{event["action"]} · {event["created_at"][:19]} UTC',
                value=ui.text(
                    f'Message {details.get("message_id", "unknown")} · '
                    f'{details.get("reason", str(details.get("players", 0)) + " players")}', 500,
                ), inline=False,
            )
        await interaction.followup.send(embed=result, ephemeral=True)

    @app_commands.command(description="Show the command reference and the supported recap format")
    async def help(self, interaction: discord.Interaction):
        result = ui.embed("Club Agent · Scouting command reference")
        result.add_field(
            name="Club operations",
            value="`/club` · Full player/staff guide\n"
                  "`/availability` · Weekly windows and vacation leave\n"
                  "`/schedule` · Sessions and commitments\n"
                  "`/attendance` · Mark or report attendance\n"
                  "`/promotion` · Evidence review and configurable policy", inline=False,
        )
        result.add_field(
            name="Scouting",
            value="`/scout evaluate` · Position and scorecard\n"
                  "`/scout history` · Private player dossier\n"
                  "`/scout docket` · Private persistent staff panel\n"
                  "`/scout assign` · Add a recruitment role (requires Manage Roles)", inline=False,
        )
        result.add_field(
            name="Performance",
            value="`/scout link` / `/scout unlink` · EA handle mapping\n"
                  "`/scout stats` · Match history and reported totals\n"
                  "`/scout ingest` · Retry a trusted recap by message ID", inline=False,
        )
        result.add_field(
            name="Management",
            value="`/scout setup` · Staff and source configuration (Manage Server)\n"
                  "`/scout stage_role` · Map recruitment roles (Manage Server)\n"
                  "`/scout status` · Health and recent recap outcomes", inline=False,
        )
        result.add_field(
            name="Coach and owner workspace",
            value="`/scout player member` · Full saved evidence, attendance, follow-ups and goals\n"
                  "`/scout compare first second position` · Compatible rubric categories, no winner\n"
                  "`/scout shortlist add`, `list`, `remove` · Staff-owned recruitment follow-ups\n"
                  "`/development goal`, `review`, `complete` · Observable goals and outcome notes\n"
                  "`/assistant task add`, `list`, `complete` · Accountable staff actions\n"
                  "`/assistant brief` · Private on-demand next-seven-days facts and suggested actions\n"
                  "All staff-only, paginated and server-isolated; no private drafts. Explicit timezone deadlines "
                  "are shown in your Discord locale. Review dates are not player decisions; completion never "
                  "changes scores or roles. Imported metric samples remain separate from declared coverage.",
            inline=False,
        )
        result.add_field(
            name="Supported recap row",
            value="`EAHandle | Rating: 8.4 | Goals: 2 | Assists: 1 | Pass Acc: 88% | Tackles: 3`\n"
                  "Use one player per line in message content, an embed description, or field values. "
                  "At least one labeled metric is required. Missing metrics remain unreported. "
                  "Images aren't read.",
            inline=False,
        )
        result.add_field(
            name="Scoring",
            value="Five position-aware categories, each 1–10 (one decimal). Unobserved = unassessed. "
                  "Review and explicitly confirm all five with context and positive observed session count. "
                  "Computed 6.8–7.2 stays a reassessment draft. Trial-ready starts at 8.0, "
                  "not automatic recruitment. Resume drafts with the same candidate and position. "
                  "EA ratings and imported match records remain separate.", inline=False,
        )
        await interaction.response.send_message(embed=result, ephemeral=True)
