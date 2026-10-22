"""Owner-scoped Discord navigation. Reading this guide never changes club records."""

import asyncio
from dataclasses import dataclass
from time import monotonic

import discord

from .access import AccessDenied
from .club_ui import card, member_access
from .domain import STAGES, is_staff
from .start_content import CONTENT_VERSION, JOURNEYS, REFERENCE, ROUTES, Step
from .views import SecureView


@dataclass(frozen=True)
class Audience:
    staff: bool
    manager: bool
    config: dict | None

    def allows(self, level):
        return level == "member" or (level == "staff" and self.staff) or (level == "manager" and self.manager)


async def audience(interaction):
    member_access(interaction)
    config = await interaction.client.db.settings(interaction.guild.id)
    manager = interaction.user.guild_permissions.manage_guild
    staff = is_staff(manage_guild=manager, role_ids={r.id for r in interaction.user.roles},
                     staff_role=config["staff_role"] if config else None)
    return Audience(staff=staff, manager=manager, config=config)


def cached_resource(guild, value, *, role=False):
    """A cache miss is not proof of a deleted resource or a permission failure."""
    try:
        identifier = int(value)
    except (ValueError, TypeError):
        return None
    return guild.get_role(identifier) if role else guild.get_channel(identifier)


async def manager_steps(interaction, access):
    db, guild = interaction.client.db, interaction.guild
    config = access.config
    role = cached_resource(guild, config["staff_role"], role=True) if config else None
    staff_channel = cached_resource(guild, config["staff_channel"]) if config else None
    recap_channel = cached_resource(guild, config["recap_channel"]) if config else None
    core_found = bool(role is not None and isinstance(staff_channel, discord.TextChannel)
                      and isinstance(recap_channel, discord.TextChannel))
    rows = await db.all("SELECT stage,role_id FROM stage_roles WHERE guild_id=?", (str(guild.id),))
    mappings = {row["stage"]: row["role_id"] for row in rows}
    mapped = {stage: cached_resource(guild, mappings.get(stage), role=True) is not None for stage in STAGES}
    policy = await db.policy(guild.id)
    notifications = await db.notification_settings(guild.id)
    missing = [stage for stage in STAGES if not mapped[stage]]
    next_setup = "/scout setup" if not core_found else "/scout stage_role" if missing else "/assistant brief"
    if not config:
        setup = "Not configured. Start with the staff role, private staff channel and trusted recap source."
    else:
        setup = (
            "Saved configuration found.\n"
            f'• Staff role: {"found" if role is not None else "not found"} in cached server data.\n'
            f'• Staff text channel: {"found" if isinstance(staff_channel, discord.TextChannel) else "not found"}.\n'
            f'• Recap text channel: {"found" if isinstance(recap_channel, discord.TextChannel) else "not found"}.\n'
            "• Trusted source identity: saved, not verified by this guide.")
    mapping_lines = "\n".join(
        f'• {stage}: {"saved role found" if mapped[stage] else "not mapped or role not found"}' for stage in STAGES)
    if not notifications["channel_id"]:
        notice = "Optional reminders: off. This does not block club setup."
    elif not notifications["enabled_since"]:
        notice = "A notification channel is saved, but enabling is incomplete. Review notification settings."
    else:
        notice = (f'Reminder settings are enabled, {notifications["lead_minutes"] // 60} hour(s) before sessions. '
                  "A saved setting is not proof of channel access or successful delivery.")
        if notifications["last_error"]:
            notice += " A delivery problem is recorded; check notification status."
    return (
        Step("Check server access and source setup",
             setup, "/scout status" if core_found else "/scout setup",
             f'Next setup action: `{next_setup}`.\n'
             "These are saved settings and cached resource checks, not a live privacy/permission test. "
             "The guide cannot verify source authenticity, gateway intents, webhook access or recap delivery. "
             "Use setup to revalidate channel privacy and status to inspect outcomes.",
             (("/scout setup", "Configure or repair staff access and the recap source"),)),
        Step("Map the three development tiers",
             mapping_lines, "/scout stage_role",
             (f'Next missing mapping: {missing[0]}.' if missing else "All three saved role mappings were found.")
             + " Map one stage at a time. Found does not mean safe to assign: role safety, permissions "
               "and hierarchy are checked by the existing configuration/assignment commands."),
        Step("Review the current attendance standard",
             f'Effective standard: {policy["percent"]}% over {policy["days"]} days, '
             f'at least {policy["minimum"]} marked eligible sessions.',
             "/promotion policy",
             "No change is needed if this standard fits your club. Only a manager can adjust it. "
             "Approved leave, excused absences and unmarked sessions remain excluded; limited declared "
             "availability does not lower a scouting score. Promotion still requires staff review.",
             (("/promotion review", "Inspect the evidence before a development-tier decision"),)),
        Step("Decide whether you want session reminders",
             notice, "/schedule notifications",
             "Optional: choose a channel and lead time, or use disable:True. "
             "Run this command without options to check settings, cached channel access and delivery problems. "
             "This guide does not send test notices or change any settings."),
        Step("Move into the daily staff workflow",
             f'Checklist: server resources {"found" if core_found else "need setup/review"}; '
             f'{sum(mapped.values())}/3 saved tier roles found. Reminders are optional.',
             next_setup,
             "Nothing was configured or changed by this guide. "
             "When setup is ready, choose Staff workspace from the menu and start with the briefing. "
             "Use Player to see what ordinary members can do.",
             (("/assistant brief", "Start the private daily briefing"),)),
    )


def reference_steps(access):
    return tuple(Step(title, "Look up a command, then type it in Discord to use it.",
                      entries[0][0], entries[0][1], entries[1:])
                 for title, level, entries in REFERENCE if access.allows(level))


class StartGuide(SecureView):
    def __init__(self, owner, guild):
        super().__init__(timeout=600)
        self.owner, self.guild = owner, guild
        self.route, self.index = None, 0
        self.expires_at, self.expired = monotonic() + 600, False
        self.navigation_lock = asyncio.Lock()
        self.content_version = CONTENT_VERSION
        self.resume_pending = False
        # Only shown on reopening a saved guide.
        self.remove_item(self.resume)
        self.remove_item(self.start_over)
        self.menu = discord.ui.Select(
            placeholder="Choose your workflow", custom_id="club:start:route", row=0)
        self.menu.callback = self.choose
        self.add_item(self.menu)

    async def interaction_check(self, interaction):
        member_access(interaction)
        if interaction.user.id != self.owner or interaction.guild.id != self.guild:
            raise AccessDenied("Open your own private Start Here guide with /club in this server.")
        if self.expired or monotonic() >= self.expires_at:
            raise ValueError("This guide has expired. Run /club to open a fresh Start Here menu.")
        return True

    async def on_timeout(self):
        self.expired = True
        for item in self.children:
            item.disabled = True

    def show_resume_choice(self):
        # Do not copy a checkpoint into the view: Resume reads the newest position
        # under the shared lock, including navigation in another open menu.
        self.resume_pending = True
        self.remove_item(self.menu)
        self.remove_item(self.back)
        self.remove_item(self.home)
        self.remove_item(self.next)
        self.add_item(self.resume)
        self.add_item(self.start_over)
        return card("Club Agent · Resume Start Here?",
                    "A recent guide position is saved for you in this server.\n\n"
                    "Choose **Resume** to return to it, or **Start over** for Home. "
                    "This saves navigation only, not completed actions. "
                    "Current permissions are checked again when you resume.")

    async def resume_position(self, interaction):
        row = await interaction.client.db.guide_checkpoint(self.guild, self.owner)
        access = await audience(interaction)
        if not row or row["content_version"] != CONTENT_VERSION:
            return None, 0
        route, index = row["route"], row["step"]
        if route not in ROUTES or not access.allows(ROUTES[route][2]):
            return None, 0
        steps = (await manager_steps(interaction, access) if route == "manager"
                 else reference_steps(access) if route == "reference" else JOURNEYS[route])
        if type(index) is not int or not 0 <= index < len(steps):
            return None, 0
        return route, index

    async def render(self, interaction, route, index):
        access = await audience(interaction)
        if route is not None and route not in ROUTES:
            raise ValueError("That route is no longer available. Return Home or run /club again.")
        if route is not None and not access.allows(ROUTES[route][2]):
            raise AccessDenied("You no longer have access to this staff/manager route. Use Home or run /club again.")
        self.menu.options = [
            discord.SelectOption(label=label, value=key, description=description, default=key == route)
            for key, (label, description, level) in ROUTES.items() if access.allows(level)
        ]
        if route is None:
            if access.manager and not access.config:
                recommendation = "First time setting up this server? Choose **Manager setup**."
            elif access.staff:
                recommendation = "For daily coaching work, choose **Staff workspace** and start with the briefing."
            else:
                recommendation = "New player? Choose **Player** to set availability and find your next session."
            result = card("Club Agent · Start Here",
                "What do you need to do?\n\n" + recommendation +
                "\n\nChoose a workflow below. You will see one step and its next command at a time. "
                "The command reference is available when you need a lookup.")
            count = 0
        else:
            steps = (await manager_steps(interaction, access) if route == "manager"
                     else reference_steps(access) if route == "reference" else JOURNEYS[route])
            count = len(steps)
            if type(index) is not int or not 0 <= index < count:
                raise ValueError("This step is no longer available. Use Home or reopen /club.")
            step = steps[index]
            result = card(f'{ROUTES[route][0]} · {step.title}',
                          f'**Step {index + 1} of {count}**\n{step.action}')
            result.add_field(name="Next command", value=f"`{step.command}`", inline=False)
            if step.detail:
                result.add_field(name="What to know", value=step.detail, inline=False)
            if step.related:
                result.add_field(name="When needed",
                    value="\n".join(f"`{command}` — {why}" for command, why in step.related), inline=False)
        result.set_footer(text="Club Agent • Only you can see this guide • Lasts 10 minutes; /club to reopen/resume")
        self.back.disabled = route is None
        self.home.disabled = route is None
        self.next.disabled = route is None or index >= count - 1
        return result

    async def choose(self, interaction):
        values = self.menu.values
        if len(values) != 1:
            raise ValueError("Choose one workflow, or reopen /club.")
        await self.navigate(interaction, "choose", selected=values[0])

    async def navigate(self, interaction, action, *, selected=None):
        # Check again inside callbacks as well as at Discord's view-dispatch boundary.
        await self.interaction_check(interaction)
        # Defer before waiting on another menu's network request.
        await interaction.response.defer()
        db = interaction.client.db
        async with self.navigation_lock, db.start_guide_lock:
            await self.interaction_check(interaction)
            route, index = self.route, self.index
            choosing_resume = action in ("resume", "start_over")
            if choosing_resume:
                if not self.resume_pending:
                    raise ValueError("This choice was already used. Run /club to reopen the guide.")
                route, index = ((await self.resume_position(interaction))
                                if action == "resume" else (None, 0))
                self.remove_item(self.resume)
                self.remove_item(self.start_over)
                for item in (self.menu, self.back, self.home, self.next):
                    self.add_item(item)
            elif self.resume_pending:
                raise ValueError("Choose Resume or Start over first.")
            elif action == "home":
                route, index = None, 0
            elif action == "choose":
                if selected not in ROUTES:
                    raise ValueError("That route is no longer available. Use Home or run /club.")
                route, index = selected, 0
            elif action == "back":
                route, index = (None, 0) if index == 0 else (route, index - 1)
            elif action == "next" and route is not None:
                index += 1
            else:
                raise ValueError("Choose a workflow first, or return Home.")
            # An already-open view must not write an old position as new content.
            if self.content_version != CONTENT_VERSION:
                route, index = None, 0
            try:
                result = await self.render(interaction, route, index)
                await interaction.edit_original_response(
                    embed=result, view=self, allowed_mentions=discord.AllowedMentions.none())
            except BaseException:
                if choosing_resume:
                    # Keep the choice usable when Discord failed to publish it.
                    self.show_resume_choice()
                raise
            self.resume_pending = False
            self.content_version = CONTENT_VERSION
            self.route, self.index = route, index
            if route is None:
                await db.clear_guide_checkpoint(self.guild, self.owner)
            else:
                await db.save_guide_checkpoint(self.guild, self.owner, route, index, CONTENT_VERSION)

    @discord.ui.button(label="Resume", style=discord.ButtonStyle.primary,
                       custom_id="club:start:resume", row=1)
    async def resume(self, interaction, button):
        await self.navigate(interaction, "resume")

    @discord.ui.button(label="Start over", custom_id="club:start:over", row=1)
    async def start_over(self, interaction, button):
        await self.navigate(interaction, "start_over")

    @discord.ui.button(label="Back", custom_id="club:start:back", row=1)
    async def back(self, interaction, button):
        await self.navigate(interaction, "back")

    @discord.ui.button(label="Home", custom_id="club:start:home", row=1)
    async def home(self, interaction, button):
        await self.navigate(interaction, "home")

    @discord.ui.button(label="Next step", style=discord.ButtonStyle.primary,
                       custom_id="club:start:next", row=1)
    async def next(self, interaction, button):
        await self.navigate(interaction, "next")


async def send_start(interaction):
    member_access(interaction)
    await interaction.response.defer(ephemeral=True)
    db = interaction.client.db
    async with db.start_guide_lock:
        view = StartGuide(interaction.user.id, interaction.guild.id)
        # Always validate current access, even when the opening card is generic.
        result = await view.render(interaction, None, 0)
        if await db.guide_checkpoint(view.guild, view.owner):
            result = view.show_resume_choice()
        await interaction.followup.send(embed=result, view=view, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())
