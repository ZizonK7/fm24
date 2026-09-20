"""Position choices and 4-2-3-1 squad suggestions."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import config, metrics, utils

SLOTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GK", ("GK",)),
    ("RB", ("D(R)", "WB(R)")),
    ("RCB", ("D(C)",)),
    ("LCB", ("D(C)",)),
    ("LB", ("D(L)", "WB(L)")),
    ("RDM", ("DM", "DM(C)", "M(C)")),
    ("LDM", ("DM", "DM(C)", "M(C)")),
    ("RW", ("AM(R)", "M(R)")),
    ("AM", ("AM(C)",)),
    ("LW", ("AM(L)", "M(L)")),
    ("ST", ("ST", "ST(C)")),
)


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


def recommend(
    players: Sequence[Mapping[str, Any]],
    attributes: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Build disjoint starter, rotation and development elevens."""
    available = list(players)
    result: dict[str, Any] = {"formation": "4-2-3-1", "squads": {}}
    for squad_type in ("starter", "rotation", "development"):
        scores: list[list[float]] = []
        for _, eligible in SLOTS:
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
        for (slot, eligible), index in zip(SLOTS, assigned):
            if index is None:
                slots.append({"slot": slot, "player": None})
                continue
            player = available[index]
            token = player["primary_position"] if player["primary_position"] in eligible else next(
                token for token in player["other_positions"] if token in eligible
            )
            used.add(str(player["player_id"]))
            slots.append({"slot": slot, "player": {
                "player_id": player["player_id"], "name": player["name"], "age": player["age"],
                "position": token, "primary": token == player["primary_position"],
                "recommendation_score": round(scores[len(slots)][index], 2),
            }})
        result["squads"][squad_type] = slots
        available = [player for player in available if str(player["player_id"]) not in used]
    return result
