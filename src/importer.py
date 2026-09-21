"""import 파이프라인 — HTML 한 개를 DB에 누적하는 전체 흐름.

CLI(:mod:`scripts.update`)는 인자만 받고 실제 작업은 여기로 넘긴다. 그래야
테스트에서 CLI 없이 파이프라인을 돌려볼 수 있다.

흐름::

    HTML 읽기 → 선수 레코드 파싱 → players upsert → snapshots 저장
    → 능력치 저장 → 직전 스냅샷 대비 성장 계산 → 파생 지표 계산 → 요약
"""

from __future__ import annotations

import csv
import datetime as _dt
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import config, database, growth as growth_mod, metrics as metrics_mod, parser, utils

__all__ = [
    "ImportSummary",
    "import_export",
    "load_roles_csv",
    "apply_roles",
    "recompute_all",
    "classify_origin",
    "detect_parent_club",
]


def detect_parent_club(records: Sequence[Any]) -> str | None:
    """export에 담긴 스쿼드의 모(母)구단을 추정한다.

    임대 나간 선수는 `구단` 이 임대처로 찍히므로, 현재 구단을 그대로
    "우리 팀"으로 보면 안 된다. export는 보통 내 스쿼드 전체이고 임대 선수는
    소수이므로 **가장 많이 등장하는 구단**을 모구단으로 본다.

    Args:
        records: 파싱된 :class:`src.parser.PlayerRecord` 목록.

    Returns:
        가장 흔한 구단 이름. 판단할 수 없으면 None.
    """
    # 과반이 아니면 "내 스쿼드"라고 보기 어렵다 (스카우트 결과 export 등).
    return utils.majority_club(record.fields.get("club") or "" for record in records)


def classify_origin(
    fields: Mapping[str, Any], parent_club: str | None = None
) -> tuple[str, str | None, float | None]:
    """스냅샷 필드에서 선수의 출신(영입/유스)을 판정한다.

    판정 규칙 (위에서부터 먼저 맞는 것):

    1. `최근 구단` 이 있고 **모구단과 다르다** → **signed** (자유이적 포함)
    2. `최근 이적료` 가 0보다 크다 → **signed**
    3. 그 외 → **youth**

    모구단과 비교하는 것이 핵심이다. 임대 나간 유스 선수는 `구단` 이
    임대처로 찍히기 때문에, 현재 구단과 비교하면 전부 영입으로 잘못 잡힌다.

    남은 한계: 영입 직후 임대를 보낸 선수는 `최근 구단` 이 모구단으로
    찍혀서 유스로 잘못 볼 수 있다(이적료가 있으면 규칙 2가 잡아준다).
    틀린 건 :func:`src.database.set_player_origin` 으로 고칠 수 있고,
    그 값은 이후 import가 덮어쓰지 않는다.

    Args:
        fields: 파싱된 정규화 필드.
        parent_club: :func:`detect_parent_club` 이 추정한 모구단.
            None이면 현재 구단과 비교한다(정확도가 떨어진다).

    Returns:
        ``(origin, signed_from, signed_fee)``.
    """
    reference = (parent_club or fields.get("club") or "").strip()
    previous = (fields.get("previous_club") or "").strip()
    fee = fields.get("last_transfer_fee")

    if previous and previous != reference:
        return "signed", previous, fee
    if fee is not None and fee > 0:
        return "signed", previous or None, fee
    return "youth", None, None


@dataclass
class ImportSummary:
    """import 한 번의 결과 요약.

    Attributes:
        game_date: 이번에 넣은 게임 내 날짜.
        source_file: 원본 HTML 경로.
        imported: 저장된 스냅샷 수.
        new_players: 처음 본 선수 수.
        existing_players: 이미 알고 있던 선수 수.
        with_previous: 이전 스냅샷이 있던 선수 수.
        growth_calculated: 성장이 실제로 계산된 선수 수.
        replaced: 같은 날짜 스냅샷을 덮어쓴 건수.
        fallback_ids: ID 컬럼이 없어 임시 ID를 쓴 선수 수.
        signed_players: 영입으로 판정된 선수 수.
        youth_players: 유스 출신으로 판정된 선수 수.
        roles_auto: FM `실제 출전 시간` 에서 자동으로 붙인 역할 라벨 수.
        columns_total: export의 전체 컬럼 수.
        duplicate_headers: 중복된 헤더 종류 수.
        unmapped_headers: 정규화하지 않고 raw로만 보관한 컬럼 수.
        warnings: 사람이 읽어야 할 경고.
    """

    game_date: str
    source_file: str
    imported: int = 0
    new_players: int = 0
    existing_players: int = 0
    with_previous: int = 0
    growth_calculated: int = 0
    replaced: int = 0
    fallback_ids: int = 0
    signed_players: int = 0
    youth_players: int = 0
    roles_auto: int = 0
    columns_total: int = 0
    duplicate_headers: int = 0
    unmapped_headers: int = 0
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        """CLI에 출력할 요약 문자열."""
        lines = [
            f"Game date: {self.game_date}",
            f"Source: {self.source_file}",
            "",
            f"Imported: {self.imported} players",
            f"New players: {self.new_players}",
            f"Existing players: {self.existing_players}",
            f"Players with previous snapshot: {self.with_previous}",
            f"Growth calculated: {self.growth_calculated}",
            "",
            f"Signed (영입): {self.signed_players}   Youth (유스): {self.youth_players}",
            f"Roles auto-labelled from FM: {self.roles_auto}",
        ]
        if self.replaced:
            lines.append(f"Overwritten (same game date): {self.replaced}")
        if self.fallback_ids:
            lines.append(f"⚠ Fallback IDs used: {self.fallback_ids}")
        lines += [
            "",
            f"Columns: {self.columns_total} "
            f"(duplicated names: {self.duplicate_headers}, kept raw only: {self.unmapped_headers})",
        ]
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines += [f"  - {w}" for w in self.warnings]
        return "\n".join(lines)


def import_export(
    conn: sqlite3.Connection,
    html_path: str | Path,
    game_date: _dt.date | str,
    *,
    encoding: str | None = None,
    compute_metrics: bool = True,
) -> ImportSummary:
    """HTML export 하나를 DB에 누적한다.

    같은 ``(player_id, game_date)`` 가 이미 있으면 덮어쓴다. 컬럼을 추가한
    뒤 같은 날짜를 다시 뽑는 일이 잦기 때문이다.

    Args:
        conn: 열린 연결. 스키마는 이 함수가 보장한다.
        html_path: FM24 export 경로.
        game_date: **게임 내** 날짜. 현실 날짜가 아니다.
        encoding: 강제 인코딩.
        compute_metrics: 파생 지표까지 계산할지 여부.

    Returns:
        :class:`ImportSummary`.
    """
    if isinstance(game_date, str):
        game_date = utils.parse_game_date(game_date)
    date_iso = game_date.isoformat()
    html_path = Path(html_path)

    database.ensure_schema(conn)
    result = parser.parse_export(html_path, encoding)

    summary = ImportSummary(
        game_date=date_iso,
        source_file=str(html_path),
        columns_total=len(result.table.headers),
        duplicate_headers=len(result.table.duplicates),
        unmapped_headers=len(result.unmapped_headers),
        warnings=list(result.warnings),
    )

    # --- 1단계: players + snapshots + attributes ------------------------
    parent_club = detect_parent_club(result.players)
    # 임대 판정은 나중에 조회할 때도 필요하므로 시점별로 남겨 둔다.
    database.set_parent_club(conn, date_iso, parent_club)
    if parent_club is None:
        summary.warnings.append(
            "모구단을 판단하지 못했습니다 (구단이 제각각인 export?). "
            "영입/유스 구분이 부정확할 수 있습니다."
        )

    needs_growth: list[str] = []
    for record in result.players:
        previous_date = database.previous_game_date(conn, record.player_id, date_iso)

        is_new = database.upsert_player(
            conn,
            record.player_id,
            date_iso,
            name=record.fields.get("name"),
            birth_date=record.fields.get("birth_date"),
            nationality=record.fields.get("nationality"),
            primary_position=record.fields.get("position"),
            id_source=record.id_source,
        )
        replaced = database.insert_snapshot(
            conn,
            record.player_id,
            date_iso,
            record.fields,
            record.raw,
            id_source=record.id_source,
            source_file=str(html_path),
        )
        database.replace_attributes(
            conn, record.player_id, date_iso, record.attributes, record.attribute_groups
        )

        # 영입/유스 판정. 손으로 고친 값이 있으면 set_player_origin이 건너뛴다.
        origin, signed_from, signed_fee = classify_origin(record.fields, parent_club)
        database.set_player_origin(
            conn,
            record.player_id,
            origin,
            signed_from=signed_from,
            signed_fee=signed_fee,
            joined_date=record.fields.get("contract_start"),
        )
        summary.signed_players += int(origin == "signed")
        summary.youth_players += int(origin == "youth")

        # FM의 `실제 출전 시간` → 운영 역할. 사람이 넣은 라벨은 덮지 않는다.
        fm_status = record.fields.get("actual_playing_time")
        role = config.PLAYING_TIME_ROLE_MAP.get(fm_status or "")
        if role and database.upsert_role(
            conn, record.player_id, date_iso, role, source="fm_auto", note=fm_status
        ):
            summary.roles_auto += 1

        summary.imported += 1
        summary.new_players += int(is_new)
        summary.existing_players += int(not is_new)
        summary.replaced += int(replaced)
        summary.fallback_ids += int(record.id_source == "fallback")
        if previous_date is not None:
            summary.with_previous += 1
            needs_growth.append(record.player_id)

    conn.commit()

    # --- 2단계: 성장 + 지표 ---------------------------------------------
    # 스냅샷이 전부 들어간 뒤에 계산해야 cohort 기준값(최대 출장시간)이 맞다.
    growth_by_player: dict[str, dict[str, float | None]] = {}
    for player_id in needs_growth:
        result_growth = growth_mod.compute_growth(conn, player_id, date_iso)
        if result_growth is None:
            continue
        growth_mod.persist(conn, result_growth)
        growth_by_player[player_id] = result_growth.aggregates
        summary.growth_calculated += 1

    if compute_metrics:
        cohort = metrics_mod.CohortStats.build(conn, date_iso)
        for record in result.players:
            metrics_mod.compute_and_store(
                conn,
                record.player_id,
                date_iso,
                cohort=cohort,
                growth_aggregates=growth_by_player.get(record.player_id, {}),
            )

    conn.commit()
    return summary


# ---------------------------------------------------------------------------
# 역할 라벨
# ---------------------------------------------------------------------------


def load_roles_csv(path: str | Path) -> list[dict[str, str]]:
    """``player_id, game_date, role`` 형식의 CSV를 읽는다.

    헤더가 있어야 하며, 컬럼 순서는 상관없다. ``note`` 컬럼은 선택.

    Args:
        path: CSV 경로.

    Returns:
        행 dict 목록.

    Raises:
        ValueError: 필수 컬럼이 없을 때.
    """
    rows: list[dict[str, str]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"player_id", "game_date", "role"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"roles CSV에 필수 컬럼이 없습니다: {', '.join(sorted(missing))}")
        for row in reader:
            if not (row.get("player_id") or "").strip():
                continue
            rows.append({key: (value or "").strip() for key, value in row.items()})
    return rows


def apply_roles(conn: sqlite3.Connection, rows: Iterable[dict[str, str]], source: str = "csv") -> tuple[int, list[str]]:
    """역할 라벨을 DB에 기록한다.

    Args:
        conn: 연결.
        rows: ``player_id`` / ``game_date`` / ``role`` 키를 가진 dict들.
        source: 출처 표시 (``"csv"`` / ``"cli"``).

    Returns:
        ``(적용 건수, 경고 목록)``.
    """
    applied = 0
    warnings: list[str] = []
    for row in rows:
        role = row["role"]
        if role not in config.SQUAD_ROLES:
            warnings.append(
                f"알 수 없는 역할 '{role}' (선수 {row['player_id']}). "
                f"허용: {', '.join(config.SQUAD_ROLES)}"
            )
            continue
        try:
            date_iso = utils.parse_game_date(row["game_date"]).isoformat()
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        if database.get_player(conn, row["player_id"]) is None:
            warnings.append(f"DB에 없는 player_id: {row['player_id']} (그래도 기록합니다)")
        database.upsert_role(conn, row["player_id"], date_iso, role, source, row.get("note"))
        applied += 1
    conn.commit()
    return applied, warnings


# ---------------------------------------------------------------------------
# 재계산
# ---------------------------------------------------------------------------


def recompute_all(conn: sqlite3.Connection, game_date: str | None = None) -> dict[str, int]:
    """저장된 스냅샷만으로 성장/지표를 전부 다시 계산한다.

    **HTML을 다시 import하지 않는다.** metrics.py의 공식을 바꿨거나
    config.ATTRIBUTE_GROUPS를 수정한 뒤에 부르면 된다.

    주의: config에 능력치를 새로 추가한 경우, 과거 스냅샷에는 그 능력치가
    저장돼 있지 않다(HTML을 다시 읽어야 채워진다). 이 함수는 이미 저장된
    능력치만 가지고 재계산한다.

    Args:
        conn: 연결.
        game_date: 특정 날짜만 재계산하려면 지정.

    Returns:
        ``{"snapshots": n, "growth": n, "metrics": n}``.
    """
    database.ensure_schema(conn)
    database.clear_derived(conn, game_date)

    keys = database.all_snapshot_keys(conn, game_date)
    counts = {"snapshots": len(keys), "growth": 0, "metrics": 0}

    cohort_cache: dict[str, metrics_mod.CohortStats] = {}
    for player_id, date_iso in keys:
        aggregates: dict[str, float | None] = {}
        result_growth = growth_mod.compute_growth(conn, player_id, date_iso)
        if result_growth is not None:
            growth_mod.persist(conn, result_growth)
            aggregates = result_growth.aggregates
            counts["growth"] += 1

        if date_iso not in cohort_cache:
            cohort_cache[date_iso] = metrics_mod.CohortStats.build(conn, date_iso)
        metrics_mod.compute_and_store(
            conn, player_id, date_iso, cohort=cohort_cache[date_iso], growth_aggregates=aggregates
        )
        counts["metrics"] += 1

    conn.commit()
    return counts
