"""Position choices and data-driven squad formation suggestions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import config, metrics, utils

Slot = tuple[str, tuple[str, ...], int, int]

DEFAULT_FORMATION = "4-2-3-1"

# Coordinates are percentages on a vertical pitch: the opponent's goal is at
# the top and our goalkeeper is at the bottom.  Keeping them beside the
# eligibility rules makes the backend payload the single source of truth for
# both assignment and presentation.
FORMATIONS: dict[str, tuple[Slot, ...]] = {
    "4-2-3-1": (
        ("GK", ("GK",), 50, 92),
        ("RB", ("D(R)", "WB(R)"), 86, 76),
        ("RCB", ("D(C)",), 62, 76),
        ("LCB", ("D(C)",), 38, 76),
        ("LB", ("D(L)", "WB(L)"), 14, 76),
        ("RDM", ("DM", "DM(C)", "M(C)"), 62, 57),
        ("LDM", ("DM", "DM(C)", "M(C)"), 38, 57),
        ("RW", ("AM(R)", "M(R)"), 84, 34),
        ("AM", ("AM(C)",), 50, 39),
        ("LW", ("AM(L)", "M(L)"), 16, 34),
        ("ST", ("ST", "ST(C)"), 50, 13),
    ),
    "4-3-3 DM": (
        ("GK", ("GK",), 50, 92),
        ("RB", ("D(R)", "WB(R)"), 86, 76),
        ("RCB", ("D(C)",), 62, 76),
        ("LCB", ("D(C)",), 38, 76),
        ("LB", ("D(L)", "WB(L)"), 14, 76),
        ("DM", ("DM", "DM(C)", "M(C)"), 50, 60),
        ("RCM", ("M(C)", "DM(C)", "DM"), 66, 43),
        ("LCM", ("M(C)", "DM(C)", "DM"), 34, 43),
        ("RW", ("AM(R)", "M(R)"), 84, 25),
        ("LW", ("AM(L)", "M(L)"), 16, 25),
        ("ST", ("ST", "ST(C)"), 50, 13),
    ),
    "4-4-2": (
        ("GK", ("GK",), 50, 92),
        ("RB", ("D(R)", "WB(R)"), 86, 76),
        ("RCB", ("D(C)",), 62, 76),
        ("LCB", ("D(C)",), 38, 76),
        ("LB", ("D(L)", "WB(L)"), 14, 76),
        ("RM", ("M(R)", "AM(R)", "WB(R)"), 88, 52),
        ("RCM", ("M(C)", "DM(C)", "DM"), 64, 55),
        ("LCM", ("M(C)", "DM(C)", "DM"), 36, 55),
        ("LM", ("M(L)", "AM(L)", "WB(L)"), 12, 52),
        ("RST", ("ST", "ST(C)"), 64, 17),
        ("LST", ("ST", "ST(C)"), 36, 17),
    ),
    "3-4-2-1": (
        ("GK", ("GK",), 50, 92),
        ("RCB", ("D(C)",), 73, 73),
        ("CB", ("D(C)",), 50, 73),
        ("LCB", ("D(C)",), 27, 73),
        ("RWB", ("WB(R)", "M(R)", "D(R)"), 88, 54),
        ("RCM", ("M(C)", "DM(C)", "DM"), 62, 57),
        ("LCM", ("M(C)", "DM(C)", "DM"), 38, 57),
        ("LWB", ("WB(L)", "M(L)", "D(L)"), 12, 54),
        ("RAM", ("AM(R)", "AM(C)", "M(R)"), 64, 34),
        ("LAM", ("AM(L)", "AM(C)", "M(L)"), 36, 34),
        ("ST", ("ST", "ST(C)"), 50, 13),
    ),
}

# Compatibility for code that imported the original fixed slot list.
SLOTS = tuple((name, eligible) for name, eligible, _, _ in FORMATIONS[DEFAULT_FORMATION])


def choices(fm_position: str | None, manual: tuple[str, list[str]] | None) -> tuple[str | None, list[str]]:
    """Manual choices win; otherwise retain FM's first listed position as primary."""
    if manual:
        return manual
    tokens = utils.parse_position_tokens(fm_position)
    if not tokens:
        return None, []
    first = str(fm_position).split(",")[0]
    primary = next((token for token in sorted(utils.parse_position_tokens(first)) if token in tokens), sorted(tokens)[0])
    return primary, sorted(tokens - {primary})


def _assignment(scores: list[list[float]]) -> list[int | None]:
    """Maximum weight assignment; one private zero-value dummy per slot."""
    n = len(scores)
    if not n:
        return []
    real_count = len(scores[0])
    costs = [[-score for score in row] + [0.0] * n for row in scores]
    width = real_count + n
    u = [0.0] * (n + 1)
    v = [0.0] * (width + 1)
    p = [0] * (width + 1)
    way = [0] * (width + 1)
    for i in range(1, n + 1):
        p[0] = i
        col = 0
        minv = [float("inf")] * (width + 1)
        used = [False] * (width + 1)
        while True:
            used[col] = True
            row = p[col]
            delta = float("inf")
            next_col = 0
            for j in range(1, width + 1):
                if used[j]:
                    continue
                cur = costs[row - 1][j - 1] - u[row] - v[j]
                if cur < minv[j]:
                    minv[j] = cur
                    way[j] = col
                if minv[j] < delta:
                    delta = minv[j]
                    next_col = j
            for j in range(width + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            col = next_col
            if p[col] == 0:
                break
        while True:
            previous = way[col]
            p[col] = p[previous]
            col = previous
            if col == 0:
                break
    result: list[int | None] = [None] * n
    for j in range(1, width + 1):
        if p[j] and j <= real_count and scores[p[j] - 1][j - 1] > 0:
            result[p[j] - 1] = j - 1
    return result


def _recommend_squads(
    players: Sequence[Mapping[str, Any]],
    attributes: Mapping[str, Mapping[str, float]],
    slots_definition: Sequence[Slot],
) -> dict[str, list[dict[str, Any]]]:
    """Build disjoint starter, rotation and development elevens."""
    available = list(players)
    squads: dict[str, list[dict[str, Any]]] = {}
    for squad_type in ("starter", "rotation", "development"):
        scores: list[list[float]] = []
        for _, eligible, _, _ in slots_definition:
            row: list[float] = []
            for player in available:
                primary = player.get("primary_position")
                others = player.get("other_positions") or []
                possible = [token for token in (primary, *others) if token in eligible]
                if not possible:
                    row.append(-1000000.0)
                    continue
                token = primary if primary in eligible else possible[0]
                group = config.POSITION_GROUPS[token]
                quality = metrics.positional_quality(attributes.get(str(player["player_id"]), {}), token, [group])
                if quality is None:
                    row.append(-1000000.0)
                    continue
                role = player.get("role")
                role_bonus = {
                    "starter": {"starter": 1.2, "rotation": 0.1, "development": -0.5},
                    "rotation": {"rotation": 1.2, "starter": -0.2, "development": 0.2},
                    "development": {"development": 1.0, "rotation": 0.1, "starter": -0.6},
                }[squad_type].get(role, 0.0)
                age = player.get("age")
                youth_bonus = max(0, 23 - age) * 0.5 if squad_type == "development" and isinstance(age, (int, float)) else 0.0
                score = quality + role_bonus + youth_bonus + (0.4 if token == primary else -0.7)
                row.append(score)
            scores.append(row)
        assigned = _assignment(scores)
        used: set[str] = set()
        slots: list[dict[str, Any]] = []
        for (slot, eligible, x, y), index in zip(slots_definition, assigned):
            if index is None:
                slots.append({"slot": slot, "x": x, "y": y, "player": None})
                continue
            player = available[index]
            token = player["primary_position"] if player["primary_position"] in eligible else next(
                token for token in player["other_positions"] if token in eligible
            )
            used.add(str(player["player_id"]))
            slots.append({"slot": slot, "x": x, "y": y, "player": {
                "player_id": player["player_id"], "name": player["name"], "age": player["age"],
                "position": token, "primary": token == player["primary_position"],
                "recommendation_score": round(scores[len(slots)][index], 2),
            }})
        squads[squad_type] = slots
        available = [player for player in available if str(player["player_id"]) not in used]
    return squads


def recommend(
    players: Sequence[Mapping[str, Any]],
    attributes: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Build recommendations for every supported formation.

    ``formation`` and ``squads`` retain the original API contract for older
    clients.  New clients use ``formations`` to switch locally without another
    request.
    """
    formations = [
        {
            "id": formation_id,
            "label": formation_id,
            "squads": _recommend_squads(players, attributes, slots),
        }
        for formation_id, slots in FORMATIONS.items()
    ]
    default = next(item for item in formations if item["id"] == DEFAULT_FORMATION)
    return {
        "formation": DEFAULT_FORMATION,
        "squads": default["squads"],
        "formations": formations,
    }
