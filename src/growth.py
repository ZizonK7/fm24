"""스냅샷 간 능력치 변화 계산.

원본 스냅샷은 건드리지 않는다. 여기서 나오는 값은 전부 ``growth_deltas`` 와
``derived_metrics`` 에만 들어가므로, 계산 방식을 바꾸면 HTML을 다시 import할
필요 없이 :mod:`scripts.recompute` 만 돌리면 된다.

두 가지 쓰임새가 있다:

* :func:`compute_growth` — 직전 스냅샷 대비 (import할 때마다 자동 실행)
* :func:`growth_between` — 임의의 두 시점 사이 (예: 영입 시점 → 2시즌 뒤)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import config, database, utils

__all__ = ["AttributeDelta", "GrowthResult", "compute_growth", "growth_between"]


@dataclass(frozen=True)
class AttributeDelta:
    """능력치 하나의 변화.

    Attributes:
        attribute: canonical 능력치 키 (예: ``"passing"``).
        group: technical / mental / physical / goalkeeping.
        previous: 이전 시점 값.
        current: 현재 시점 값.
        delta: ``current - previous``.
    """

    attribute: str
    group: str | None
    previous: float
    current: float
    delta: float


@dataclass
class GrowthResult:
    """두 시점 사이의 성장 계산 결과.

    Attributes:
        player_id: 선수 식별자.
        game_date: 비교 기준이 되는 나중 시점.
        prev_game_date: 비교 대상인 이전 시점.
        deltas: 능력치별 변화 (변화량 내림차순).
        aggregates: 집계 지표 ``{이름: 값}``.
    """

    player_id: str
    game_date: str
    prev_game_date: str
    deltas: list[AttributeDelta] = field(default_factory=list)
    aggregates: dict[str, float | None] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """전체 능력치 변화량 합."""
        return sum(delta.delta for delta in self.deltas)

    def changed(self) -> list[AttributeDelta]:
        """실제로 값이 변한 능력치만."""
        return [delta for delta in self.deltas if delta.delta != 0]

    def as_rows(self) -> list[dict[str, Any]]:
        """``database.store_growth_deltas`` 에 넣을 dict 목록으로 변환."""
        return [
            {
                "player_id": self.player_id,
                "game_date": self.game_date,
                "prev_game_date": self.prev_game_date,
                "attribute": delta.attribute,
                "attr_group": delta.group,
                "previous": delta.previous,
                "current": delta.current,
                "delta": delta.delta,
            }
            for delta in self.deltas
        ]


def _group_of(attribute: str) -> str | None:
    """canonical 키가 속한 그룹 이름."""
    for group, mapping in config.ATTRIBUTE_GROUPS.items():
        if attribute in mapping.values():
            return group
    return None


def _aggregate(deltas: list[AttributeDelta], days: int | None) -> dict[str, float | None]:
    """성장 집계 지표를 만든다.

    그룹별 합계 이름은 ``<group>_growth`` 로 :data:`config.ATTRIBUTE_GROUPS`
    에서 자동 생성되므로, 그룹을 추가하면 지표도 따라 생긴다.
    """
    values = [delta.delta for delta in deltas]
    aggregates: dict[str, float | None] = {
        "attribute_growth_total": sum(values) if values else None,
        "attribute_growth_mean": utils.safe_mean(values),
        "attribute_growth_max": max(values) if values else None,
        "attributes_compared": float(len(values)) if values else None,
        "attributes_improved": float(sum(1 for v in values if v > 0)) if values else None,
        "attributes_declined": float(sum(1 for v in values if v < 0)) if values else None,
        "growth_days": float(days) if days is not None else None,
    }

    for group in config.ATTRIBUTE_GROUPS:
        group_values = [delta.delta for delta in deltas if delta.group == group]
        aggregates[f"{group}_growth"] = sum(group_values) if group_values else None

    # 기간으로 정규화한 성장 속도. 출전시간 대비 성장을 보려면 이 값을
    # usage와 함께 쓰면 된다.
    total = aggregates["attribute_growth_total"]
    if total is not None and days:
        aggregates["attribute_growth_per_year"] = total * 365.0 / days
    else:
        aggregates["attribute_growth_per_year"] = None

    return aggregates


def growth_between(
    conn: sqlite3.Connection,
    player_id: str,
    start_date: str,
    end_date: str,
) -> GrowthResult | None:
    """임의의 두 시점 사이의 능력치 변화를 계산한다.

    양쪽 시점에 **모두 존재하는** 능력치만 비교한다. config에 능력치를
    나중에 추가한 경우 과거 스냅샷에는 그 값이 없을 수 있기 때문이다.

    Args:
        conn: 연결.
        player_id: 선수 식별자.
        start_date: 이전 시점 (ISO).
        end_date: 나중 시점 (ISO).

    Returns:
        :class:`GrowthResult`. 비교 가능한 능력치가 하나도 없으면 None.
    """
    previous = database.get_attributes(conn, player_id, start_date)
    current = database.get_attributes(conn, player_id, end_date)
    shared = sorted(set(previous) & set(current))
    if not shared:
        return None

    deltas = [
        AttributeDelta(
            attribute=key,
            group=_group_of(key),
            previous=previous[key],
            current=current[key],
            delta=current[key] - previous[key],
        )
        for key in shared
    ]
    deltas.sort(key=lambda d: (-d.delta, d.attribute))

    days = utils.days_between(start_date, end_date)
    return GrowthResult(
        player_id=player_id,
        game_date=end_date,
        prev_game_date=start_date,
        deltas=deltas,
        aggregates=_aggregate(deltas, days),
    )


def compute_growth(conn: sqlite3.Connection, player_id: str, game_date: str) -> GrowthResult | None:
    """직전 스냅샷 대비 성장을 계산한다.

    Args:
        conn: 연결.
        player_id: 선수 식별자.
        game_date: 이번 스냅샷 날짜.

    Returns:
        :class:`GrowthResult`. 이전 스냅샷이 없으면 None.
    """
    previous_date = database.previous_game_date(conn, player_id, game_date)
    if previous_date is None:
        return None
    return growth_between(conn, player_id, previous_date, game_date)


def persist(conn: sqlite3.Connection, result: GrowthResult) -> None:
    """성장 결과를 ``growth_deltas`` 에 저장한다 (집계는 metrics 쪽에서)."""
    database.store_growth_deltas(conn, result.as_rows())
