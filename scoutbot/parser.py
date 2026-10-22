"""Strict, deterministic recap adapter; never supplies missing values as zero.

Supported row:
EAHandle | Rating: 8.4 | Goals: 2 | Assists: 1 | Pass Acc: 88% | Tackles: 3
Rows may be in embed descriptions, field values, or message content.
"""

from dataclasses import asdict, dataclass
import re

from .domain import handle, numeric


class RecapFormatError(ValueError):
    pass


@dataclass(frozen=True)
class PlayerStats:
    handle_key: str
    player_handle: str
    rating: float | None = None
    goals: int | None = None
    assists: int | None = None
    passing_acc: float | None = None
    tackles: int | None = None

    def asdict(self) -> dict:
        return asdict(self)


LABELS = {
    "rating": ("rating", 10, False),
    "goals": ("goals", 100, True),
    "assists": ("assists", 100, True),
    "pass acc": ("passing_acc", 100, False),
    "passing acc": ("passing_acc", 100, False),
    "passing accuracy": ("passing_acc", 100, False),
    "tackles": ("tackles", 200, True),
}


def parse_recaps(texts: list[str]) -> list[PlayerStats]:
    players: dict[str, PlayerStats] = {}
    for text in texts:
        for raw in text.splitlines():
            line = raw.strip().strip("`").strip()
            if "|" not in line:
                continue
            # Formatting wrappers are safe to remove; punctuation in handles is not.
            parts = [p.strip().strip("*").strip() for p in line.split("|")]
            if not any(re.match(r"(rating|goals|assists|pass|tackles)\b", p, re.I)
                       for p in parts[1:]):
                raise RecapFormatError(
                    "Unsupported pipe-separated row. Expected labeled statistics; no data was saved."
                )
            try:
                key, display = handle(parts[0])
                values = {}
                for part in parts[1:]:
                    if not part:
                        continue
                    label, separator, value = part.partition(":")
                    label = label.casefold().strip()
                    if not separator or label not in LABELS:
                        raise ValueError("Use labeled Rating, Goals, Assists, Pass Acc, or Tackles fields.")
                    name, maximum, integer = LABELS[label]
                    if name in values:
                        raise ValueError("A row contains the same statistic more than once.")
                    values[name] = numeric(value, maximum=maximum, integer=integer)
                player = PlayerStats(key, display, **values)
            except ValueError as exc:
                raise RecapFormatError(str(exc)) from None
            if key in players:
                raise RecapFormatError("A recap repeats a player handle; no data was saved.")
            players[key] = player
            if len(players) > 100:
                raise RecapFormatError("A recap may contain at most 100 players.")
    if not players:
        raise RecapFormatError(
            "No supported player rows found. Use: EAHandle | Rating: 8.4 | Goals: 2"
        )
    return list(players.values())
