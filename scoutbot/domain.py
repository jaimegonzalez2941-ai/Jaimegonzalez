"""Discord-independent validation and authorization rules."""

import math
from decimal import Decimal, InvalidOperation

POSITIONS = ("Goalkeeper", "Defender", "Midfielder", "Attacker")
STAGES = ("Trialist", "Academia Prospect", "First Team Roster")


def rating(value: str) -> float:
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError("Enter a number between 1.0 and 10.0.") from None
    if not number.is_finite() or not Decimal(1) <= number <= Decimal(10):
        raise ValueError("Rating must be a finite number between 1.0 and 10.0.")
    if number.as_tuple().exponent < -1:
        raise ValueError("Use at most one decimal place.")
    if Decimal("6.8") <= number <= Decimal("7.2"):
        raise ValueError(
            "Your no-7 rule excludes 6.8–7.2. Choose 6.7 or below, or 7.3 or above. "
            "Trial-ready status starts at 8.0."
        )
    return float(number)


def handle(value: str) -> tuple[str, str]:
    display = value.strip()
    if not display or len(display) > 100 or any(ord(c) < 32 for c in display):
        raise ValueError("EA handles must contain 1–100 characters, without line breaks.")
    return display.casefold(), display


def is_staff(*, manage_guild: bool, role_ids: set[int], staff_role: str | None) -> bool:
    return manage_guild or bool(staff_role and int(staff_role) in role_ids)


def source_allowed(config: dict, *, channel_id: int, author_id: int,
                   author_is_bot: bool, webhook_id: int | None) -> bool:
    if str(channel_id) != config.get("recap_channel"):
        return False
    if config.get("source_kind") == "webhook":
        return webhook_id is not None and str(webhook_id) == config.get("source_id")
    return (
        webhook_id is None and author_is_bot
        and str(author_id) == config.get("source_id")
    )


def numeric(value: str, *, maximum: float, integer: bool = False) -> float | int:
    try:
        number = float(value.strip().removesuffix("%"))
    except ValueError:
        raise ValueError("A statistic is not a valid number.") from None
    if not math.isfinite(number) or not 0 <= number <= maximum:
        raise ValueError("A statistic is outside its permitted range.")
    if integer and not number.is_integer():
        raise ValueError("Count statistics must be whole numbers.")
    return int(number) if integer else number
