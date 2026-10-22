"""Bounded read models over the bot repository; no alternate data store."""

import json

from .access import private_channel, manager_access
from .club_domain import stamp, utc_now
from .web_catalog import operations
from .web_drafts import draft_operations

TITLES = {"overview": "Club overview", "setup": "Setup & diagnostics", "schedule": "Sessions & rosters",
          "availability": "Availability & leave", "attendance": "Attendance & promotion",
          "scouting": "Player evidence", "evaluations": "Private evaluations", "recruitment": "Recruitment follow-ups",
          "goals": "Staff development goals", "tasks": "Accountable staff tasks", "briefing": "On-demand briefing",
          "audit": "Saved audit history"}


async def verify_setup(bot, interaction):
    manager_access(interaction)
    config = await bot.db.settings(interaction.guild_id)
    if not config:
        raise ValueError("No saved setup. Configure a dedicated staff role and private channel first.")
    role = interaction.guild.get_role(int(config["staff_role"]))
    if role is None:
        raise ValueError("The saved staff role no longer exists. Select its replacement.")
    channel = await interaction.guild.fetch_channel(int(config["staff_channel"]))
    await private_channel(channel, role)
    recap = await interaction.guild.fetch_channel(int(config["recap_channel"]))
    permissions = recap.permissions_for(interaction.guild.me)
    if not permissions.view_channel or not permissions.read_message_history:
        raise ValueError("Club Agent needs View Channel and Read Message History in the recap channel.")
    if config["source_kind"] == "bot":
        source = await interaction.guild.fetch_member(int(config["source_id"]))
        if not source.bot or source.id == bot.user.id:
            raise ValueError("The trusted source is not another current bot member.")
    return {"message": "Current Discord privacy and recap read permissions verified.",
            "blocks": [{"title": "Live check limits", "text":
                        "This verifies current role/channel permissions and configured bot membership, not webhook authenticity, "
                        "actual recap delivery or text format. A saved webhook ID is not proof of ownership. "
                        "Inspect trusted text recaps and import diagnostics separately."}]}


async def workspace(bot, interaction, params):
    db, guild = bot.db, str(interaction.guild_id)
    screen = params.get("screen", "overview")
    if screen not in TITLES:
        raise ValueError("Choose an available staff workflow.")
    page = params.get("page", 1)
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("Page must be between 1 and 10000.")
    search = params.get("search", "")
    if not isinstance(search, str) or len(search) > 100:
        raise ValueError("Search must be at most 100 characters.")
    status = params.get("status", "active")
    if status not in {"active", "all", "pending", "approved", "declined", "cancelled",
                      "overdue", "archived", "completed", "past", "upcoming", ""}:
        raise ValueError("Unsupported record filter.")
    manager = interaction.user.guild_permissions.manage_guild
    result = {"title": TITLES[screen], "rows": [], "blocks": [], "page": page, "hasNext": False,
              "live": "Discord membership and staff permission verified for this request",
              "operations": operations(bot, screen, manager)}
    args = [guild]
    query = None
    if screen == "overview":
        settings = await db.settings(interaction.guild_id)
        notifications = await db.notification_settings(interaction.guild_id)
        policy = await db.policy(interaction.guild_id)
        for title, table, condition in (
                ("Pending leave decisions", "club_leave", "status='pending'"),
                ("Active staff tasks", "staff_records", "kind='task' AND status='active'"),
                ("Active recruitment follow-ups", "staff_records", "kind='recruitment' AND status='active'"),
                ("Active development goals", "staff_records", "kind='goal' AND status='active'")):
            row = await db.one(f"SELECT COUNT(*) AS count FROM {table} WHERE guild_id=? AND {condition}", (guild,))
            result["blocks"].append({"title": title, "text": str(row["count"]) + " saved records"})
        result["blocks"] += [
            {"title": "Saved setup — not a live privacy check", "text":
             "Staff configuration is saved. Open Setup to inspect settings and verify current channel privacy."
             if settings else "Setup is not saved. A server manager needs to configure staff access."},
            {"title": "Saved attendance policy", "text":
             f"{policy['percent']}% over {policy['days']} days, minimum {policy['minimum']} marked eligible sessions. "
             "Approved leave, excused absences and unmarked sessions stay excluded. No automatic promotions."},
            {"title": "Saved notification setting — delivery is separate", "text":
             (f"Channel ID {notifications['channel_id']}; lead {notifications['lead_minutes'] // 60} hours."
              if notifications["channel_id"] else "Session notifications are off.")
             + (" Last delivery problem: " + notifications["last_error"] if notifications["last_error"] else
                " No saved delivery error; this is not proof that a notification was delivered.")}]
        query = "SELECT id,title,kind,starts_at,ends_at,timezone FROM club_events WHERE guild_id=? AND cancelled=0 AND ends_at>? ORDER BY starts_at,id"
        args.append(stamp(utc_now()))
    elif screen == "setup":
        if not manager:
            result["blocks"].append({"title": "Manager configuration", "text":
                                     "Only server managers can change staff access, tier mappings or attendance policy. Staff may inspect status and manage existing notification settings."})
        for label, data in (("Saved staff & trusted recap settings", await db.settings(interaction.guild_id)),
                            ("Saved attendance policy", await db.policy(interaction.guild_id)),
                            ("Saved notification settings", await db.notification_settings(interaction.guild_id))):
            result["blocks"].append({"title": label, "text":
                                     "\n".join(f"{k.replace('_', ' ').capitalize()}: {v if v is not None else 'Not set'}" for k, v in data.items() if k != "guild_id")
                                     if data else "Not configured"})
        result["rows"] = await db.all("SELECT stage,role_id FROM stage_roles WHERE guild_id=? ORDER BY stage", (guild,))
        result["blocks"].append({"title": "Current checks and safe publication", "text":
                                 "Saved settings do not verify privacy, trusted-source authenticity or delivery. "
                                 "Managers can run Verify current channel privacy below. "
                                 "For the public Start Here panel, use /start_panel in Discord. "
                                 "Retry uncertain publication in the same channel so the bot can reconcile its saved marker; do not create duplicates. "
                                 "For the private staff docket, use /scout docket in Discord."})
        if manager:
            result["operations"].append({"id": "setup.verify", "label": "Verify current channel privacy",
                                         "description": "Read-only live Discord permission checks; never changes permissions.",
                                         "fields": [], "confirmation": False})
    elif screen == "schedule":
        comparison = "<=" if status == "past" else ">"
        query = f"SELECT id,title,kind,starts_at,ends_at,timezone,cancelled,revision FROM club_events WHERE guild_id=? AND ends_at{comparison}?"
        args.append(stamp(utc_now()))
        if search:
            query += " AND title LIKE ? ESCAPE '\\'"
            args.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        query += " ORDER BY starts_at " + ("DESC" if status == "past" else "ASC") + ",id"
    elif screen == "availability":
        query = "SELECT id,member_id,starts_at,ends_at,timezone,status FROM club_leave WHERE guild_id=?"
        if status != "all":
            query += " AND status=?"
            args.append(status if status in {"pending", "approved", "declined", "cancelled"} else "pending")
        if search:
            query += " AND member_id=?"
            args.append(search)
        query += " ORDER BY id DESC"
        result["blocks"].append({"title": "Availability is planning context", "text":
                                 "Review weekly windows with Review roster or View availability below. Weekly windows never reduce scouting ratings. "
                                 "Leave is pending until approved. Approved leave excludes overlapping attendance, including historical sessions. No private reason is collected."})
    elif screen == "attendance":
        query = ("SELECT e.id AS session_id,e.title,e.starts_at,r.member_id,r.response,"
                 "COALESCE(a.status,'unmarked') AS attendance,"
                 "EXISTS(SELECT 1 FROM club_leave l WHERE l.guild_id=r.guild_id AND l.member_id=r.member_id "
                 "AND l.status='approved' AND l.starts_at<e.ends_at AND l.ends_at>e.starts_at) AS approved_leave "
                 "FROM club_events e JOIN club_roster r ON r.guild_id=e.guild_id AND r.event_id=e.id "
                 "LEFT JOIN club_attendance a ON a.guild_id=r.guild_id AND a.event_id=r.event_id AND a.member_id=r.member_id "
                 "WHERE e.guild_id=? AND e.cancelled=0 AND e.ends_at<=? AND r.response IN ('yes','assigned')")
        args.append(stamp(utc_now()))
        if search:
            query += " AND r.member_id=?"
            args.append(search)
        query += " ORDER BY e.starts_at DESC,e.id DESC,r.member_id"
        result["blocks"].append({"title": "Protected attendance meanings", "text":
                                 "Present and late count as attended. Absent is a recorded no-show, never inferred. "
                                 "Approved leave, excused and unmarked are excluded from the denominator. "
                                 "Use a player's report for the current policy window; no evidence is not 0% attendance."})
    elif screen == "scouting":
        query = ("SELECT candidate_id AS player_id,MAX(candidate_name) AS saved_name,COUNT(*) AS saved_evaluations,"
                 "MAX(created_at) AS latest_saved_at FROM evaluations WHERE guild_id=?")
        if search:
            query += " AND (candidate_id=? OR candidate_name LIKE ? ESCAPE '\\')"
            args += [search, "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
        query += " GROUP BY candidate_id ORDER BY latest_saved_at DESC,candidate_id"
        result["blocks"].append({"title": "Private saved evidence", "text":
                                 "Search saved player names or exact Discord ID. A player without saved evaluations can still be opened by ID in Open player dossier. "
                                 "Trusted EA metrics, declared observations and human ratings remain distinct. No inferred potential or public dossiers."})
    elif screen == "evaluations":
        result["operations"] = draft_operations()
        query = ("SELECT id,candidate_id,candidate_name,position,rubric_version,revision,status,updated_at "
                 "FROM evaluation_drafts WHERE guild_id=? AND scout_id=?")
        args.append(str(interaction.user.id))
        if search:
            query += " AND candidate_id=?"
            args.append(search)
        query += " ORDER BY updated_at DESC,id"
        result["blocks"].append({"title": "Your private scorecards", "text":
                                 "Only your drafts are listed. Resume the same candidate and position, or reopen a draft ID. "
                                 "Category scores 1–10, at most one decimal. Overall raw or rounded 6.8–7.2 results remain drafts. "
                                 "Confirm & save never assigns a recruitment role."})
    elif screen in {"recruitment", "goals", "tasks"}:
        kind = {"recruitment": "recruitment", "goals": "goal", "tasks": "task"}[screen]
        query, args_tuple = db.staff_record_query(interaction.guild_id, kind,
                                                  status if status in {"active", "overdue", "completed", "archived", "all"} else "active",
                                                  member=search or None if screen != "tasks" else None)
        args = list(args_tuple)
        if search and screen == "tasks":
            sql, order = query.rsplit(" ORDER BY ", 1)
            query = sql + " AND (owner_id=? OR description LIKE ? ESCAPE '\\') ORDER BY " + order
            args += [search, "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"]
    elif screen == "briefing":
        result["blocks"].append({"title": "Read the saved-evidence briefing", "text":
                                 "Generate an on-demand briefing below for upcoming sessions, all pending leave decisions, protected attendance gaps "
                                 "and staff deadlines. Nothing is sent to Discord or scheduled automatically."})
    elif screen == "audit":
        query = "SELECT id,actor_id,action,details,created_at FROM audit_log WHERE guild_id=?"
        if search:
            query += " AND action LIKE ? ESCAPE '\\'"
            args.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        query += " ORDER BY id DESC"
    if query:
        rows = await db.all(query + " LIMIT 21 OFFSET ?", (*args, (page - 1) * 20))
        result["hasNext"] = len(rows) > 20
        result["rows"] = rows[:20]
    return result
