"""Time and attendance rules, independent of Discord and storage."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_POLICY = {"days": 30, "minimum": 5, "percent": 80}
ATTENDANCE = ("present", "late", "absent", "excused", "unmarked")


def utc_now():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def zone(name):
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Use an IANA timezone, for example America/Chicago or Europe/London.") from None


def clock(value):
    try:
        parsed = datetime.strptime(value.strip(), "%H:%M")
    except ValueError:
        raise ValueError("Enter a 24-hour time as HH:MM, for example 20:30.") from None
    return parsed.hour * 60 + parsed.minute


def local_datetime(day, time, timezone_name):
    tz = zone(timezone_name)
    try:
        naive = datetime.strptime(f"{day.strip()} {time.strip()}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Use YYYY-MM-DD for dates and HH:MM (24-hour) for times.") from None
    candidates = set()
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        result = aware.astimezone(timezone.utc)
        if result.astimezone(tz).replace(tzinfo=None) == naive:
            candidates.add(result)
    if not candidates:
        raise ValueError("That local time does not exist due to daylight saving. Choose another time.")
    if len(candidates) > 1:
        raise ValueError("That local time occurs twice due to daylight saving. Use timezone UTC instead.")
    return candidates.pop()


def session_times(day, time, timezone_name, minutes, *, now=None):
    if not 15 <= minutes <= 720:
        raise ValueError("Sessions must last between 15 and 720 minutes.")
    start = local_datetime(day, time, timezone_name)
    now = now or utc_now()
    if not now < start <= now + timedelta(days=366):
        raise ValueError("Schedule a future session within the next year.")
    return stamp(start), stamp(start + timedelta(minutes=minutes))


def leave_times(first, last, timezone_name, *, now=None):
    start = local_datetime(first, "00:00", timezone_name)
    try:
        last_date = datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)
    except ValueError:
        raise ValueError("Enter leave dates as YYYY-MM-DD.") from None
    end = local_datetime(last_date.strftime("%Y-%m-%d"), "00:00", timezone_name)
    now = now or utc_now()
    if end <= start or end - start > timedelta(days=367):
        raise ValueError("Leave must end on or after its first day and last no more than a year.")
    if end <= now or start > now + timedelta(days=366):
        raise ValueError("Leave must include upcoming time within the next year.")
    return stamp(start), stamp(end)


def summarize(rows):
    counts = {key: 0 for key in ATTENDANCE}
    counts["leave"] = 0
    for row in rows:
        if row["on_leave"]:
            counts["leave"] += 1
        else:
            counts[row["status"] or "unmarked"] += 1
    eligible = counts["present"] + counts["late"] + counts["absent"]
    attended = counts["present"] + counts["late"]
    return {**counts, "eligible": eligible, "attended": attended,
            "rate": 100 * attended / eligible if eligible else None}


def readiness(summary, policy, evaluation):
    reasons = []
    if summary["eligible"] < policy["minimum"]:
        reasons.append(f'Need {policy["minimum"]} marked eligible sessions; have {summary["eligible"]}.')
    # Compare the unrounded ratio, not the display percentage.
    if summary["rate"] is not None and summary["attended"] * 100 < policy["percent"] * summary["eligible"]:
        reasons.append(f'Attendance is below {policy["percent"]}%.')
    if not evaluation:
        reasons.append(f'Need a saved scouting evaluation within {policy["days"]} days.')
    elif evaluation["score"] < 8:
        reasons.append("Latest scouting rating is below the 8.0 trial-ready standard.")
    return not reasons, reasons
