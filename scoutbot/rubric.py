"""Versioned, deterministic staff rubric. Not a validated prediction model."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from types import MappingProxyType

VERSION = "staff-v1"
CATEGORIES = ("role_execution", "decision_making", "positioning", "ball_use", "teamwork")
LABELS = ("Role execution", "Decision-making", "Positioning", "Ball use", "Teamwork")
WEIGHTS = MappingProxyType({
    "Goalkeeper": (35, 20, 20, 15, 10),
    "Defender": (30, 20, 25, 15, 10),
    "Midfielder": (20, 25, 20, 25, 10),
    "Attacker": (35, 20, 20, 15, 10),
})
OBSERVABLES = MappingProxyType({
    "Goalkeeper": (
        "Shot handling, parries and dealing with crosses actually observed.",
        "Choice to hold, release, claim or stay; timing under pressure.",
        "Starting depth, angles and recovery relative to ball and goal.",
        "First touch and distribution accuracy, direction and pace.",
        "Visible coordination with defenders and availability for back passes.",
    ),
    "Defender": (
        "Observed marking, challenges and defensive duels without needless fouls.",
        "Choice to engage, delay, clear or retain under pressure.",
        "Line discipline, cover, tracking runners and recovery shape.",
        "First touch and passing that retain or progress possession.",
        "Cover for teammates and coordinated defensive movements.",
    ),
    "Midfielder": (
        "Connecting phases, supporting transitions and fulfilling the assigned midfield role.",
        "Choice of tempo, pass, carry or defensive response under pressure.",
        "Receiving angles, spacing, scanning movement and transition coverage.",
        "First touch, pass execution and ball retention or progression.",
        "Support combinations, cover and visible coordination with teammates.",
    ),
    "Attacker": (
        "Observed finishing, chance creation and execution of assigned attacking duties.",
        "Choice to shoot, pass, carry or press given available options.",
        "Timing of runs, space creation, onside discipline and pressing angles.",
        "First touch, combination passes, dribbling and shot execution.",
        "Link play, coordinated pressing and runs that support teammates.",
    ),
})
ANCHORS = (
    "1: repeatedly fails the observed duty; 2: rare successful execution; "
    "3: frequent errors; 4: inconsistent with substantial support needed; "
    "5: adequate on simple actions; 6: usually reliable, some pressure errors; "
    "7: consistently effective in the observed context; 8: strong under pressure; "
    "9: repeatedly exceptional against observed opposition; "
    "10: exceptional throughout the observed sample with no material lapse. "
    "Decimals interpolate adjacent anchors. Blank = unassessed, never zero. "
    "Score observed actions only; do not infer attitude."
)


def category_score(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("Category scores must be 1–10 with at most one decimal place.") from None
    if (not number.is_finite() or not Decimal(1) <= number <= Decimal(10)
            or number.as_tuple().exponent < -1):
        raise ValueError("Category scores must be 1–10 with at most one decimal place.")
    return format(number, ".1f")


def coverage(count: int) -> str:
    if type(count) is not int or not 1 <= count <= 10000:
        raise ValueError("Declared observations must be a whole number from 1 to 10000.")
    return "Limited" if count <= 2 else "Developing" if count <= 4 else "Broader"


@dataclass(frozen=True)
class Result:
    raw: Decimal
    score: Decimal
    borderline: bool

    @property
    def recommendation(self) -> str:
        if self.borderline:
            return "Reassess — excluded 6.8–7.2 band"
        return "Trial-ready for staff consideration" if self.score >= 8 else "Developmental"


def calculate(position: str, categories: dict, version: str = VERSION) -> Result | None:
    if version != VERSION:
        raise ValueError("Unsupported rubric version; do not recalculate with a different rubric.")
    if position not in WEIGHTS or not isinstance(categories, dict) or set(categories) - set(CATEGORIES):
        raise ValueError("Unknown position or category.")
    if any(not isinstance(entry, dict) for entry in categories.values()):
        raise ValueError("Invalid category observations.")
    values = [category_score(str(categories.get(key, {}).get("score") or ""))
              for key in CATEGORIES]
    if any(value is None for value in values):
        return None
    raw = sum((Decimal(value) * weight / 100
               for value, weight in zip(values, WEIGHTS[position])), Decimal(0))
    rounded = raw.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    borderline = any(Decimal("6.8") <= value <= Decimal("7.2") for value in (raw, rounded))
    return Result(raw, rounded, borderline)


def validate_payload(payload: dict, position: str, *, complete: bool = False,
                     version: str = VERSION) -> Result | None:
    if set(payload) != {"categories", "count", "context", "notes"}:
        raise ValueError("Invalid scorecard fields.")
    categories = payload["categories"]
    if not isinstance(categories, dict):
        raise ValueError("Invalid categories.")
    result = calculate(position, categories, version)
    for entry in categories.values():
        if set(entry) != {"score", "evidence"}:
            raise ValueError("Invalid category fields.")
        score = category_score(str(entry["score"] or ""))
        evidence = entry["evidence"]
        if not isinstance(evidence, str) or len(evidence) > 500:
            raise ValueError("Category evidence must be at most 500 characters.")
        if score is not None and len(evidence.strip()) < 3:
            raise ValueError("Each assessed category needs at least three characters of observed evidence.")
    for key, limit in (("context", 500), ("notes", 1000)):
        if not isinstance(payload[key], str) or len(payload[key]) > limit:
            raise ValueError(f"{key.title()} exceeds its permitted length.")
    if payload["count"] is not None:
        coverage(payload["count"])
    if complete:
        if result is None:
            raise ValueError("All five categories must be assessed before saving.")
        coverage(payload["count"])
        if len(payload["context"].strip()) < 3 or len(payload["notes"].strip()) < 3:
            raise ValueError("Provide observation context and summary notes before saving.")
        if result.borderline:
            raise ValueError("Borderline draft: raw or rounded result is in 6.8–7.2. Reassess observations or discard.")
    return result
