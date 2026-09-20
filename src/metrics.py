"""파생 지표 (Quality / Ceiling / Growth / Usage) — **1차 초안**.

여기 있는 공식은 전부 자리표시자다. 목적은 "지금 정확한 수치를 뽑는 것"이
아니라, **나중에 공식을 갈아끼워도 원본 데이터를 다시 import하지 않게**
계산부를 격리해두는 것이다.

계산식을 바꾸는 방법
--------------------
1. 아래 함수 본문을 고친다 (또는 새 함수를 ``@metric`` 으로 등록한다).
2. :data:`FORMULA_VERSION` 을 올린다.
3. ``python scripts/recompute.py`` 를 돌린다.

``snapshots`` 테이블은 전혀 건드리지 않는다.

네 축의 현재 정의
-----------------
====== ===================================================================
축     v0.1 정의
====== ===================================================================
quality 포지션군(필드/GK) 핵심 능력치의 단순 평균 (1~20 스케일)
ceiling quality + 나이에 따른 성장 여력 가산. **매우 거친 근사.**
        FM export에 잠재력(PA) 컬럼이 비어 있어 관측값이 없다.
growth  직전 스냅샷 대비 능력치 총 변화량을 연 단위로 환산
usage   같은 날짜 스쿼드 내 최대 출장시간 대비 비율 (0~1)
====== ===================================================================
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from . import config, database, growth as growth_mod, utils

__all__ = [
    "FORMULA_VERSION",
    "CohortStats",
    "MetricContext",
    "compute_metrics",
    "compute_and_store",
    "metric",
    "METRIC_FUNCTIONS",
    "EXPERIMENTAL_FUNCTIONS",
    "talent_score",
    "balance_score",
    "floor_score",
]

#: 계산식 버전. 공식을 바꾸면 올릴 것. derived_metrics에 함께 기록된다.
FORMULA_VERSION = "v0.1"

#: ceiling 계산용 자리표시자 상수.
PEAK_AGE = 24
"""성장이 대체로 멈춘다고 가정하는 나이. 실제 데이터가 쌓이면 이 값을 검증할 것."""

CEILING_GROWTH_PER_YEAR = 0.35
"""PEAK_AGE까지 남은 1년당 기대 능력치 상승폭(포인트). 순전히 추정치."""

BALANCE_LAMBDA = 0.5
"""balance_score에서 능력치 편차에 주는 벌점 계수."""

TALENT_TOP_N = 3
"""talent_score에서 볼 상위 능력치 개수."""


# ---------------------------------------------------------------------------
# 계산 입력
# ---------------------------------------------------------------------------


def positional_quality(
    attributes: Mapping[str, float], position: Any, groups: Sequence[str] | None = None
) -> float | None:
    """포지션에 맞는 핵심 능력치만 평균한다 (1~20 스케일).

    "주전으로 쓸 만한가" 는 포지션마다 다른 질문이다. 센터백에게 `결정` 은
    거의 의미가 없고 스트라이커에게는 전부다. 그래서 전체 평균이 아니라
    :data:`src.config.CORE_ATTRIBUTES` 의 그룹별 목록만 본다.

    여러 포지션을 소화하는 선수는 **가장 점수가 높은 포지션** 기준이다
    (그 선수가 가장 잘 하는 자리로 평가하는 것이 타당하다).

    Args:
        attributes: ``{canonical 키: 값}``.
        position: FM 포지션 문자열.
        groups: 직접 지정할 그룹 목록. None이면 position에서 뽑는다.

    Returns:
        평균값. 포지션을 알 수 없거나 해당 능력치가 하나도 없으면,
        전체 능력치 평균으로 물러선다(GK는 GK 능력치 제외).
    """
    candidates = list(groups) if groups is not None else utils.position_groups(position)
    scores: list[float] = []
    for group in candidates:
        wanted = config.CORE_ATTRIBUTES.get(group, ())
        values = [attributes[key] for key in wanted if key in attributes]
        if values:
            scores.append(sum(values) / len(values))
    if scores:
        return max(scores)

    # 포지션을 못 읽었을 때의 대비책: 필드/GK 그룹 전체 평균.
    is_keeper = any(token in str(position or "") for token in config.GOALKEEPER_TOKENS)
    fallback_groups = config.KEEPER_GROUPS if is_keeper else config.OUTFIELD_GROUPS
    wanted_keys = set(config.attribute_keys(fallback_groups))
    return utils.safe_mean([v for k, v in attributes.items() if k in wanted_keys])


@dataclass
class CohortStats:
    """같은 게임 날짜의 스쿼드 전체에서 뽑은 비교 기준값.

    usage도 "주전감"도 절대값이 아니라 **이 스쿼드 안에서의 상대적 위치**다.
    그래서 같은 시점 동료 전원을 기준으로 삼는다.

    Attributes:
        max_minutes: 스쿼드 내 최대 출장시간.
        max_starts: 스쿼드 내 최대 선발 수.
        size: 스냅샷에 포함된 선수 수.
        group_quality: ``{포지션 그룹: [(player_id, quality), …]}``.
            quality 내림차순으로 정렬돼 있다.
    """

    max_minutes: float = 0.0
    max_starts: float = 0.0
    size: int = 0
    group_quality: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, rows: Sequence[Mapping[str, Any]]) -> "CohortStats":
        """스냅샷 행들에서 출장시간 기준값만 집계한다 (포지션 순위 제외)."""
        minutes = [float(r["minutes"]) for r in rows if r["minutes"] is not None]
        starts = [
            float(r["appearances_starts"])
            for r in rows
            if r["appearances_starts"] is not None
        ]
        return cls(
            max_minutes=max(minutes) if minutes else 0.0,
            max_starts=max(starts) if starts else 0.0,
            size=len(rows),
        )

    @classmethod
    def build(cls, conn: sqlite3.Connection, game_date: str) -> "CohortStats":
        """DB에서 해당 날짜의 전체 비교 기준을 만든다 (포지션 순위 포함)."""
        rows = database.cohort_snapshots(conn, game_date)
        stats = cls.from_rows(rows)
        all_attributes = database.attributes_by_player(conn, game_date)
        manual_positions = database.positions_by_player(conn)

        for row in rows:
            player_id = row["player_id"]
            attributes = all_attributes.get(player_id, {})
            if not attributes:
                continue
            manual = manual_positions.get(player_id)
            position = ", ".join((manual[0], *manual[1])) if manual else row["position"]
            for group in utils.position_groups(position):
                quality = positional_quality(attributes, position, [group])
                if quality is not None:
                    stats.group_quality.setdefault(group, []).append((player_id, quality))

        for members in stats.group_quality.values():
            members.sort(key=lambda pair: -pair[1])
        return stats

    def rank_in(self, group: str, player_id: str) -> int | None:
        """그룹 내 순위 (1 = 최고). 그룹에 없으면 None."""
        for index, (other, _) in enumerate(self.group_quality.get(group, []), start=1):
            if other == player_id:
                return index
        return None

    def depth_of(self, group: str) -> int:
        """그 그룹을 소화할 수 있는 스쿼드 내 인원 수."""
        return len(self.group_quality.get(group, []))

    def best_other(self, group: str, player_id: str) -> float | None:
        """자기 자신을 뺀 그룹 내 최고 quality. 경쟁자가 없으면 None."""
        for other, quality in self.group_quality.get(group, []):
            if other != player_id:
                return quality
        return None


@dataclass
class MetricContext:
    """지표 함수 하나가 필요로 하는 모든 입력.

    Attributes:
        player_id: 선수 식별자.
        game_date: 게임 내 날짜 (ISO).
        snapshot: snapshots 행을 dict로 만든 것.
        attributes: ``{canonical 능력치 키: 값}``.
        growth: 직전 스냅샷 대비 성장 집계 (없으면 빈 dict).
        cohort: 같은 날짜 스쿼드 기준값.
    """

    player_id: str
    game_date: str
    snapshot: Mapping[str, Any]
    attributes: Mapping[str, float]
    growth: Mapping[str, float | None] = field(default_factory=dict)
    cohort: CohortStats = field(default_factory=CohortStats)

    @property
    def is_goalkeeper(self) -> bool:
        """포지션 문자열에 GK가 들어 있으면 골키퍼로 본다."""
        position = self.position
        return any(token in position for token in config.GOALKEEPER_TOKENS)

    @property
    def position(self) -> str:
        """FM 포지션 문자열."""
        return str(self.snapshot.get("effective_position") or self.snapshot.get("position") or "")

    @property
    def groups(self) -> list[str]:
        """소화 가능한 포지션 그룹들 (CB, DM, W …)."""
        return utils.position_groups(self.position)

    @property
    def best_group(self) -> str | None:
        """가장 점수가 높게 나오는 포지션 그룹.

        여러 자리를 보는 선수는 **가장 잘 하는 자리** 기준으로 평가한다.
        """
        if self.snapshot.get("manual_primary_position"):
            return config.POSITION_GROUPS.get(str(self.snapshot["manual_primary_position"]))
        scored = [
            (positional_quality(self.attributes, self.position, [group]), group)
            for group in self.groups
        ]
        scored = [(score, group) for score, group in scored if score is not None]
        return max(scored)[1] if scored else None

    def core_attributes(self) -> dict[str, float]:
        """평가 대상이 되는 핵심 능력치.

        포지션 그룹을 알면 그 그룹의 핵심 능력치만, 모르면 필드/GK 전체를
        쓴다. talent/balance/floor 점수도 이 목록을 기준으로 계산된다 —
        센터백의 '드리블'이 높다고 유망하다고 볼 수는 없기 때문이다.
        """
        group = self.best_group
        if group is not None:
            wanted: set[str] = set(config.CORE_ATTRIBUTES.get(group, ()))
        else:
            fallback = config.KEEPER_GROUPS if self.is_goalkeeper else config.OUTFIELD_GROUPS
            wanted = set(config.attribute_keys(fallback))
        return {key: value for key, value in self.attributes.items() if key in wanted}

    def number(self, column: str) -> float | None:
        """스냅샷 컬럼을 float로. 값이 없거나 숫자가 아니면 None."""
        value = self.snapshot.get(column)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# 지표 레지스트리
# ---------------------------------------------------------------------------

MetricFunction = Callable[[MetricContext], float | None]

METRIC_FUNCTIONS: dict[str, MetricFunction] = {}
"""매 import마다 자동 계산되는 지표."""

EXPERIMENTAL_FUNCTIONS: dict[str, MetricFunction] = {}
"""영입 철학 실험용 지표. 기본 계산에는 포함되지만 해석은 아직 신뢰하지 말 것."""


def metric(name: str, *, experimental: bool = False) -> Callable[[MetricFunction], MetricFunction]:
    """지표 함수를 레지스트리에 등록하는 데코레이터.

    Args:
        name: derived_metrics.metric 에 저장될 이름.
        experimental: True면 :data:`EXPERIMENTAL_FUNCTIONS` 에 등록된다.
    """

    def decorator(func: MetricFunction) -> MetricFunction:
        target = EXPERIMENTAL_FUNCTIONS if experimental else METRIC_FUNCTIONS
        target[name] = func
        return func

    return decorator


# ---------------------------------------------------------------------------
# 네 축 (v0.1 자리표시자)
# ---------------------------------------------------------------------------


@metric("quality")
def quality_score(ctx: MetricContext) -> float | None:
    """현재 실력 — 포지션 핵심 능력치의 평균 (1~20 스케일).

    :data:`src.config.CORE_ATTRIBUTES` 의 포지션별 목록만 본다. 여러 자리를
    소화하면 가장 점수가 높은 자리 기준.

    한계: 목록 안에서는 아직 **가중치가 없다** (전부 동등 평균).
    """
    return positional_quality(ctx.attributes, ctx.position, [ctx.best_group]) if ctx.best_group else positional_quality(ctx.attributes, ctx.position)


@metric("ceiling")
def ceiling_score(ctx: MetricContext) -> float | None:
    """미래 도달 가능 수준 — **가장 거친 자리표시자.**

    FM export의 `잠재력` 컬럼이 비어 있어 PA를 직접 쓸 수 없다. 그래서
    현재 quality에 "PEAK_AGE까지 남은 햇수 × 연간 기대 상승폭"을 더한다.
    실제 성장 데이터가 몇 시즌 쌓이면 이 상수를 회귀로 대체할 것.
    """
    quality = quality_score(ctx)
    age = ctx.number("age")
    if quality is None:
        return None
    if age is None:
        return quality
    headroom = max(0.0, PEAK_AGE - age) * CEILING_GROWTH_PER_YEAR
    return min(20.0, quality + headroom)


@metric("growth")
def growth_score(ctx: MetricContext) -> float | None:
    """실제 성장 — 직전 스냅샷 대비 능력치 총 변화량의 연 환산값.

    스냅샷 간격이 제각각이어도 비교할 수 있도록 365일 기준으로 정규화한다.
    기간 정보가 없으면 총 변화량을 그대로 돌려준다.
    """
    per_year = ctx.growth.get("attribute_growth_per_year")
    if per_year is not None:
        return per_year
    return ctx.growth.get("attribute_growth_total")


@metric("usage")
def usage_score(ctx: MetricContext) -> float | None:
    """현재 활용도 — 같은 시점 스쿼드 최대 출장시간 대비 비율 (0~1).

    출장시간만 본다. 선발 비중, 경기 중요도, 대회 등급은 아직 반영하지
    않는다. 공식을 바꾸려면 이 함수만 고치면 된다.
    """
    minutes = ctx.number("minutes")
    if minutes is None:
        return None
    if ctx.cohort.max_minutes <= 0:
        return None
    return min(1.0, minutes / ctx.cohort.max_minutes)


@metric("usage_start_share")
def usage_start_share(ctx: MetricContext) -> float | None:
    """선발 출전 비중 — 전체 출전 중 선발이 차지하는 비율."""
    starts = ctx.number("appearances_starts")
    subs = ctx.number("appearances_subs")
    if starts is None:
        return None
    total = starts + (subs or 0.0)
    return starts / total if total > 0 else 0.0


@metric("minutes_per_appearance")
def minutes_per_appearance(ctx: MetricContext) -> float | None:
    """출전 1회당 평균 출장 시간. 교체로만 나오는 선수를 구분하는 데 쓴다."""
    minutes = ctx.number("minutes")
    starts = ctx.number("appearances_starts")
    subs = ctx.number("appearances_subs")
    if minutes is None or starts is None:
        return None
    total = starts + (subs or 0.0)
    return minutes / total if total > 0 else None


# ---------------------------------------------------------------------------
# "주전으로 쓸 만한가" — 절대 점수가 아니라 경쟁자 대비
# ---------------------------------------------------------------------------


@metric("best_position_group")
def best_position_group(ctx: MetricContext) -> Any:
    """평가 기준이 된 포지션 그룹 (CB, DM, W …). 문자열로 저장된다."""
    return ctx.best_group


@metric("position_rank")
def position_rank(ctx: MetricContext) -> float | None:
    """주 포지션 그룹 내 quality 순위. 1이면 그 자리 최고.

    "주전으로 쓸 만한가" 에 가장 직접적으로 답하는 값이다. 1~2위면 이미
    주전급, 뒤로 밀릴수록 자리가 막혀 있다는 뜻.
    """
    group = ctx.best_group
    if group is None:
        return None
    rank = ctx.cohort.rank_in(group, ctx.player_id)
    return float(rank) if rank is not None else None


@metric("position_depth")
def position_depth(ctx: MetricContext) -> float | None:
    """주 포지션 그룹을 소화할 수 있는 스쿼드 내 인원 수.

    순위만으로는 부족하다. 3명 중 3위와 9명 중 3위는 다르다.
    """
    group = ctx.best_group
    return float(ctx.cohort.depth_of(group)) if group is not None else None


@metric("starter_gap")
def starter_gap(ctx: MetricContext) -> float | None:
    """그 자리 최고 선수와의 quality 차이 (능력치 포인트).

    ``0 이상`` 이면 그 자리에서 이미 최고, 음수면 그만큼 모자라다.
    이 값이 스냅샷마다 0에 가까워지면 **주전으로 올라오고 있다**는 뜻이고,
    그게 이 시스템의 핵심 질문에 대한 답이다.
    """
    group = ctx.best_group
    if group is None:
        return None
    mine = positional_quality(ctx.attributes, ctx.position, [group])
    if mine is None:
        return None
    best = ctx.cohort.best_other(group, ctx.player_id)
    if best is None:
        return 0.0  # 경쟁자가 없다 = 그 자리 유일 자원
    return mine - best


# ---------------------------------------------------------------------------
# 실험용 — 유망주 영입 철학 (아직 모델에 강제 적용하지 않음)
# ---------------------------------------------------------------------------


def talent_score(attributes: Mapping[str, float], top_n: int = TALENT_TOP_N) -> float | None:
    """'특출난 유망주' 점수 — 상위 N개 능력치의 평균.

    전체는 아직 부족해도 특정 장점이 매우 뾰족한 선수를 높게 본다.

    Args:
        attributes: 대상 능력치.
        top_n: 볼 상위 능력치 개수.
    """
    values = sorted(attributes.values(), reverse=True)
    if not values:
        return None
    return utils.safe_mean(values[:top_n])


def balance_score(attributes: Mapping[str, float], lam: float = BALANCE_LAMBDA) -> float | None:
    """'균형형 유망주' 점수 — ``mean - lambda * std``.

    구멍 없이 고르게 괜찮은 선수를 높게 본다. 편차가 클수록 감점된다.
    """
    values = list(attributes.values())
    mean = utils.safe_mean(values)
    if mean is None:
        return None
    stdev = utils.safe_stdev(values) or 0.0
    return mean - lam * stdev


def floor_score(attributes: Mapping[str, float]) -> float | None:
    """'1군에 넣어도 안 무너지는' 하한 점수 — 핵심 능력치의 최솟값."""
    values = list(attributes.values())
    return min(values) if values else None


@metric("talent_score", experimental=True)
def _talent(ctx: MetricContext) -> float | None:
    return talent_score(ctx.core_attributes())


@metric("balance_score", experimental=True)
def _balance(ctx: MetricContext) -> float | None:
    return balance_score(ctx.core_attributes())


@metric("floor_score", experimental=True)
def _floor(ctx: MetricContext) -> float | None:
    return floor_score(ctx.core_attributes())


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------


def compute_metrics(ctx: MetricContext, include_experimental: bool = True) -> dict[str, Any]:
    """등록된 모든 지표를 계산한다.

    지표 하나가 실패해도 나머지는 계속 계산한다 — 한 공식의 버그가 import
    전체를 막으면 안 된다.

    Args:
        ctx: 계산 입력.
        include_experimental: 실험용 지표도 포함할지 여부.

    Returns:
        ``{지표 이름: 값}``. None인 지표도 키는 포함된다.
    """
    functions: dict[str, MetricFunction] = dict(METRIC_FUNCTIONS)
    if include_experimental:
        functions.update(EXPERIMENTAL_FUNCTIONS)

    results: dict[str, Any] = {}
    for name, func in functions.items():
        try:
            results[name] = func(ctx)
        except Exception as exc:  # noqa: BLE001 - 한 지표 실패가 전체를 막지 않게
            results[name] = None
            results[f"{name}__error"] = str(exc)

    # 성장 집계도 같은 테이블에 보관한다 (technical_growth 등).
    for name, value in ctx.growth.items():
        results.setdefault(name, value)
    return results


def build_context(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    cohort: CohortStats | None = None,
    growth_aggregates: Mapping[str, float | None] | None = None,
) -> MetricContext | None:
    """DB에서 :class:`MetricContext` 를 조립한다.

    Args:
        cohort: 미리 계산한 스쿼드 기준값. None이면 여기서 조회한다
            (루프에서는 한 번만 계산해 넘기는 편이 훨씬 빠르다).
        growth_aggregates: 미리 계산한 성장 집계. None이면 여기서 계산한다.

    Returns:
        :class:`MetricContext`. 스냅샷이 없으면 None.
    """
    snapshot = database.get_snapshot(conn, player_id, game_date)
    if snapshot is None:
        return None
    if cohort is None:
        cohort = CohortStats.build(conn, game_date)
    if growth_aggregates is None:
        result = growth_mod.compute_growth(conn, player_id, game_date)
        growth_aggregates = result.aggregates if result else {}

    snapshot_data = dict(snapshot)
    manual = database.get_player_positions(conn, player_id)
    if manual:
        snapshot_data["effective_position"] = ", ".join((manual[0], *manual[1]))
        snapshot_data["manual_primary_position"] = manual[0]
    return MetricContext(
        player_id=player_id,
        game_date=game_date,
        snapshot=snapshot_data,
        attributes=database.get_attributes(conn, player_id, game_date),
        growth=dict(growth_aggregates),
        cohort=cohort,
    )


def compute_and_store(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    cohort: CohortStats | None = None,
    growth_aggregates: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    """지표를 계산해 ``derived_metrics`` 에 저장한다.

    Returns:
        계산된 지표 dict. 스냅샷이 없으면 빈 dict.
    """
    ctx = build_context(conn, player_id, game_date, cohort, growth_aggregates)
    if ctx is None:
        return {}
    results = compute_metrics(ctx)
    database.store_metrics(conn, player_id, game_date, results, FORMULA_VERSION)
    return results
