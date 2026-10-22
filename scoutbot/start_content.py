"""Small, read-only journeys through the existing Discord commands."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    title: str
    action: str
    command: str
    detail: str = ""
    related: tuple[tuple[str, str], ...] = ()


ROUTES = {
    "player": ("Player", "Availability, RSVP, time away and attendance", "member"),
    "session": ("Training and match", "Plan a session and record attendance", "staff"),
    "scouting": ("Scouting and development", "Evaluate, follow up and review progression", "staff"),
    "workspace": ("Staff workspace", "Briefing, accountable tasks and player records", "staff"),
    "manager": ("Manager setup", "Review configuration and the next setup step", "manager"),
    "reference": ("Command reference", "Look up commands by category when needed", "member"),
}

JOURNEYS = {
    "player": (
        Step("Set your weekly availability",
             "Choose a weekday, your start/end time and your timezone.",
             "/availability set",
             "Use 24-hour HH:MM and an IANA timezone such as America/Chicago or Europe/London. "
             "There is one window per weekday; split overnight windows across two days (24:00 is allowed as an end). "
             "Availability is planning context, not a session commitment or a scoring penalty.",
             (("/availability view", "Check your saved windows"),
              ("/availability clear", "Remove a weekday"))),
        Step("Find a session and RSVP",
             "Find the next session, then use its session ID to inspect it and respond.",
             "/schedule list",
             "RSVP yes only when you commit to attending. Maybe and no do not count as commitments. "
             "RSVPs close at kickoff. If staff assigned you, ask them to release you if needed.",
             (("/schedule show", "Read session details using its ID"),
              ("/schedule rsvp", "Choose yes, maybe or no before kickoff"))),
        Step("Plan vacation or time away",
             "Request your first and last day away, including your timezone.",
             "/availability leave",
             "Use YYYY-MM-DD; the last day is inclusive. Leave starts pending and needs staff approval. "
             "Approved overlapping leave is excluded from attendance. No private reason is required.",
             (("/availability leaves", "Check request IDs and approval status"),
              ("/availability cancel_leave", "Cancel your leave; ask staff if approved leave has begun"))),
        Step("Check your attendance",
             "Review your own completed-session attendance.",
             "/attendance report",
             "Staff record attendance after kickoff; reports include sessions once they end. "
             "Only yes/assigned commitments count. Approved leave, excused absences and unmarked sessions "
             "are excluded. Missing marks are never automatic no-shows. "
             "That is the player workflow; use Home to choose another route."),
    ),
    "session": (
        Step("Plan the session",
             "Create training, a match or a trial with a date, local time, timezone and duration.",
             "/schedule create",
             "Check availability first; a recurring availability window is not an RSVP.",
             (("/availability roster", "Review weekly availability"),
              ("/assistant brief", "Check the next seven days before planning"))),
        Step("Check commitments before kickoff",
             "Use the session ID to review the roster and approved-leave conflicts.",
             "/schedule roster",
             "Only yes or assigned players are committed. Maybe, no and pending are not attendance commitments. "
             "Assignments and changes to RSVPs close at kickoff.",
             (("/schedule assign", "Assign a player, or use release:True to release them"),)),
        Step("Handle changes",
             "Edit an upcoming session only when a change is needed.",
             "/schedule edit",
             "Changing start/end time resets all RSVPs and assignments to pending: players must reconfirm "
             "or staff must reassign. A title-only edit preserves commitments. "
             "Reminders and change notices are optional and off until configured.",
             (("/schedule cancel", "Cancel with confirm:True; records are retained"),
              ("/schedule notifications", "Check or configure optional reminders"))),
        Step("Record attendance after kickoff",
             "Mark only attendance you have checked, for a committed or assigned player.",
             "/attendance mark",
             "Choose present, late, absent, excused or unmarked. Repeating a mark corrects it and is audited. "
             "Never infer a no-show from an empty record. Reports include a session after it ends; "
             "approved leave and excuses remain protected.",
             (("/attendance report", "Review a player's completed attendance"),
              ("/promotion review", "Consider attendance alongside scouting evidence"))),
    ),
    "scouting": (
        Step("Observe and evaluate",
             "Choose a candidate and position, record observed evidence, then review and explicitly save.",
             "/scout evaluate",
             "Unobserved categories remain unassessed. A borderline 6.8–7.2 total stays a draft. "
             "A saved human score is not an EA match rating or a recruitment decision.",
             (("/scout link", "Staff can link an EA handle for separate trusted match context"),)),
        Step("Read the saved evidence",
             "Read the private dossier before comparing or deciding.",
             "/scout player",
             "Saved evidence, dates and declared coverage are separate from imported metric samples. "
             "Comparisons do not invent missing categories or declare an overall winner.",
             (("/scout compare", "Compare two players at the chosen position"),
              ("/scout history", "Read the saved evaluation history"))),
        Step("Track the recruitment follow-up",
             "Record the candidate, position, responsible staff member, next action and review date.",
             "/scout shortlist add",
             "A review date is a staff follow-up deadline, not automatic player rejection or promotion. "
             "Updating the same active candidate/position updates the existing entry.",
             (("/scout shortlist list", "Review the recruitment board"),
              ("/scout shortlist remove", "Archive with confirmation; retain history"))),
        Step("Set an observable development goal",
             "Give the player a concrete goal, success criteria, owner and target date.",
             "/development goal",
             "Review the goal before recording an outcome. Completion is a staff observation, "
             "not proven improvement or an automatic score change.",
             (("/development review", "Read active or completed goals"),
              ("/development edit", "Reassign an unfinished goal or adjust its deadline"),
              ("/development complete", "Save the observed outcome"))),
        Step("Review tier progression",
             "Review the latest scouting evidence and attendance together before a staff decision.",
             "/promotion review",
             "Trialist → Academia Prospect → First Team Roster. Approved leave and excuses are protected. "
             "No role changes happen in this guide or from a recommendation. Assigning roles requires "
             "the existing permissions and safety checks; insufficient evidence requires an explicit staff override.",
             (("/scout assign", "Authorized staff make the final role decision; existing roles are not removed"),)),
    ),
    "workspace": (
        Step("Start with the private briefing",
             "See the next seven days of recorded sessions and staff follow-ups.",
             "/assistant brief",
             "Facts and suggested next actions are labeled separately. Pending leave needs a staff decision; "
             "incomplete attendance is not absence. This briefing is on demand, not a scheduled DM.",
             (("/availability leaves", "Staff can use all_players:True to review pending requests"),
              ("/availability review_leave", "Make an audited leave decision"))),
        Step("Give work an accountable owner",
             "Create a staff task with a current staff owner, description and deadline.",
             "/assistant task add",
             "Use an explicit date, local time and IANA timezone. Completion requires an outcome note; "
             "completed records remain reviewable. These deadlines do not trigger automatic actions.",
             (("/assistant task list", "Review active, overdue or completed work"),
              ("/assistant task edit", "Transfer unfinished work or change its deadline"),
              ("/assistant task complete", "Record completion and its outcome"))),
        Step("Follow through with player records",
             "Open a player's dossier when a briefing needs context.",
             "/scout player",
             "Only saved records are shown, never another scout's private drafts. "
             "Use the Scouting and development route for the full player-review workflow.",
             (("/scout shortlist list", "Follow up on recruitment reviews"),
              ("/development review", "Review observable development goals"))),
    ),
}

# A categorized reference remains available, but never appears on the first screen.
REFERENCE = (
    ("Availability and time away", "member", (
        ("/availability set", "Set a weekly window"),
        ("/availability view", "Read your windows; staff may choose a player"),
        ("/availability clear", "Remove a weekday"),
        ("/availability leave", "Request vacation or time away"),
        ("/availability leaves", "Read request status"),
        ("/availability cancel_leave", "Cancel your leave"))),
    ("Your sessions and attendance", "member", (
        ("/schedule list", "List upcoming or past sessions"),
        ("/schedule show", "Read a session and your RSVP"),
        ("/schedule rsvp", "Commit yes, maybe or no before kickoff"),
        ("/attendance report", "Read attendance; staff may choose a player"))),
    ("Scouting evidence", "staff", (
        ("/scout evaluate", "Open a position-aware scorecard"),
        ("/scout player", "Read the unified saved dossier"),
        ("/scout compare", "Compare compatible position evidence"),
        ("/scout history", "Read saved evaluations"),
        ("/scout docket", "Update the private staff panel"),
        ("/scout help", "Read the detailed scouting reference"))),
    ("Trusted match context", "staff", (
        ("/scout link", "Link an EA handle"),
        ("/scout unlink", "Unlink with confirmation; retain records"),
        ("/scout stats", "Read reported match metrics"),
        ("/scout ingest", "Retry a trusted text recap by message ID"),
        ("/scout status", "Check configuration and recent recap outcomes"))),
    ("Session operations", "staff", (
        ("/schedule create", "Plan training, a match or a trial"),
        ("/schedule edit", "Edit an upcoming session"),
        ("/schedule cancel", "Cancel with confirmation"),
        ("/schedule assign", "Assign or release a player"),
        ("/schedule roster", "Read commitments and leave conflicts"),
        ("/schedule notifications", "Check/configure optional reminders"))),
    ("Attendance and promotion", "staff", (
        ("/availability roster", "Review recurring availability"),
        ("/availability review_leave", "Approve, decline or revoke leave"),
        ("/attendance mark", "Record or correct checked attendance"),
        ("/promotion review", "Review the combined evidence"),
        ("/scout assign", "Make an authorized, audited role decision"))),
    ("Recruitment and development", "staff", (
        ("/scout shortlist add", "Create/update a recruitment follow-up"),
        ("/scout shortlist list", "Read the recruitment board"),
        ("/scout shortlist remove", "Archive with confirmation"),
        ("/development goal", "Create an observable goal"),
        ("/development review", "Read goals and outcomes"),
        ("/development edit", "Reassign unfinished goals/change deadlines"),
        ("/development complete", "Record the observed outcome"))),
    ("Staff actions", "staff", (
        ("/assistant brief", "Get an on-demand private briefing"),
        ("/assistant task add", "Assign accountable work"),
        ("/assistant task list", "Read staff work"),
        ("/assistant task edit", "Reassign unfinished work/change deadlines"),
        ("/assistant task complete", "Record completion and outcome"))),
    ("Manager settings", "manager", (
        ("/scout setup", "Configure staff access and trusted recap source"),
        ("/scout stage_role", "Map each development tier to a safe role"),
        ("/promotion policy", "Adjust attendance requirements"))),
)
