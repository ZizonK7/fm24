"""로컬 웹 UI가 쓰는 데이터 조회/가공 계층.

HTTP와 분리해 둔 이유: 여기 있는 함수들은 커넥션만 있으면 되므로
서버 없이 테스트할 수 있다. :mod:`web.server` 는 이걸 JSON으로 감싸기만 한다.

모든 함수는 JSON으로 바로 직렬화 가능한 dict/list만 돌려준다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

from src import config, database, formation, growth as growth_mod, metrics as metrics_mod, utils

__all__ = [
    "overview",
    "squad",
    "player_detail",
    "list_export_files",
    "inspect_export",
    "export_bundle",
]


def _num(value: Any, digits: int = 2) -> float | None:
    """JSON에 넣기 좋게 반올림한다. 숫자가 아니면 None."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 전체 현황
# ---------------------------------------------------------------------------


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """DB 전체 요약. 첫 화면에서 쓴다."""
    dates = database.distinct_game_dates(conn)
    origins = database.origins_by_player(conn)
    players = conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
    return {
        "dates": dates,
        "latest_date": dates[-1] if dates else None,
        "players": players,
        "signed": sum(1 for row in origins.values() if row["origin"] == "signed"),
        "youth": sum(1 for row in origins.values() if row["origin"] == "youth"),
        "snapshots": conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"],
        "formula_version": metrics_mod.FORMULA_VERSION,
        "roles": list(config.SQUAD_ROLES),
        "position_groups": config.POSITION_GROUP_LABELS,
        "position_options": list(config.POSITION_GROUPS),
        "can_compare_growth": len(dates) >= 2,
    }


# ---------------------------------------------------------------------------
# 스쿼드 목록
# ---------------------------------------------------------------------------


def squad(conn: sqlite3.Connection, game_date: str | None = None) -> dict[str, Any]:
    """한 시점의 전체 스쿼드를 화면용 행으로 만든다.

    필터(영입만 보기, 나이 제한 등)는 클라이언트가 한다. 42명 규모라
    전부 넘겨도 부담이 없고, 필터를 바꿀 때마다 왕복하지 않아도 된다.

    Args:
        conn: 연결.
        game_date: 볼 시점. None이면 가장 최근.

    Returns:
        ``{"game_date": ..., "players": [...]}``.
    """
    if game_date is None:
        dates = database.distinct_game_dates(conn)
        if not dates:
            return {"game_date": None, "players": []}
        game_date = dates[-1]

    snapshots = database.cohort_snapshots(conn, game_date)
    all_metrics = database.metrics_by_player(conn, game_date)
    roles = database.roles_by_player(conn, game_date)
    origins = database.origins_by_player(conn)
    growth_rows = database.growth_by_player(conn, game_date)
    manual_positions = database.positions_by_player(conn)

    players: list[dict[str, Any]] = []
    for row in snapshots:
        player_id = row["player_id"]
        m = all_metrics.get(player_id, {})
        origin = origins.get(player_id)
        role = roles.get(player_id)
        group = m.get("best_position_group")
        primary, others = formation.choices(row["position"], manual_positions.get(player_id))

        players.append(
            {
                "player_id": player_id,
                "name": row["name"],
                "age": row["age"],
                "position": row["position"],
                "primary_position": primary,
                "other_positions": others,
                "manual_positions": player_id in manual_positions,
                "group": group,
                "group_label": config.POSITION_GROUP_LABELS.get(group or "", group),
                "club": row["club"],
                "squad_level": row["squad_level"],
                "origin": origin["origin"] if origin else None,
                "signed_from": origin["signed_from"] if origin else None,
                "signed_fee": _num(origin["signed_fee"], 0) if origin else None,
                "manual_origin": bool(origin["manual_origin"]) if origin else False,
                "role": role["role"] if role else None,
                "role_source": role["source"] if role else None,
                "fm_status": row["actual_playing_time"],
                # 실력과 자리
                "quality": _num(m.get("quality")),
                "ceiling": _num(m.get("ceiling")),
                "rank": _num(m.get("position_rank"), 0),
                "depth": _num(m.get("position_depth"), 0),
                "starter_gap": _num(m.get("starter_gap")),
                # 출전
                "minutes": row["minutes"],
                "appearances": row["appearances_raw"],
                "starts": row["appearances_starts"],
                "subs": row["appearances_subs"],
                "goals": row["goals"],
                "assists": row["assists"],
                "avg_rating": _num(row["avg_rating"]),
                "usage": _num(m.get("usage")),
                # 성장 (직전 스냅샷 대비)
                "growth_total": _num(m.get("attribute_growth_total"), 1),
                "growth_per_year": _num(m.get("attribute_growth_per_year"), 1),
                "growth_days": _num(m.get("growth_days"), 0),
                "improved": [
                    {"attribute": g["attribute"], "delta": g["delta"]}
                    for g in growth_rows.get(player_id, [])[:5]
                ],
                # 유망주 성향
                "talent_score": _num(m.get("talent_score")),
                "balance_score": _num(m.get("balance_score")),
                "value": _num(row["value"], 0),
                "value_raw": row["value_raw"],
                "wage_raw": row["wage_raw"],
            }
        )

    players.sort(key=lambda p: (-(p["quality"] or 0), p["name"] or ""))
    return {
        "game_date": game_date,
        "players": players,
        "recommendation": formation.recommend(players, database.attributes_by_player(conn, game_date)),
    }


# ---------------------------------------------------------------------------
# 선수 상세
# ---------------------------------------------------------------------------


def player_detail(conn: sqlite3.Connection, player_id: str) -> dict[str, Any] | None:
    """한 선수의 전체 이력.

    시점별 quality / starter_gap / usage 추이와 능력치 변화를 함께 담는다.
    "정말 성장하고 있나, 주전에 가까워지고 있나" 를 한 화면에서 보기 위한 것.
    """
    player = database.get_player(conn, player_id)
    if player is None:
        return None

    origin = database.get_player_origin(conn, player_id)
    manual_positions = database.get_player_positions(conn, player_id)
    dates = database.snapshot_dates(conn, player_id)
    labels = config.attribute_labels()

    timeline: list[dict[str, Any]] = []
    for date_iso in dates:
        snapshot = database.get_snapshot(conn, player_id, date_iso)
        if snapshot is None:
            continue
        m = database.get_metrics(conn, player_id, date_iso)
        deltas = database.get_growth(conn, player_id, date_iso)
        timeline.append(
            {
                "game_date": date_iso,
                "age": snapshot["age"],
                "club": snapshot["club"],
                "squad_level": snapshot["squad_level"],
                "position": snapshot["position"],
                "role": database.get_role(conn, player_id, date_iso),
                "fm_status": snapshot["actual_playing_time"],
                "minutes": snapshot["minutes"],
                "appearances": snapshot["appearances_raw"],
                "goals": snapshot["goals"],
                "assists": snapshot["assists"],
                "avg_rating": _num(snapshot["avg_rating"]),
                "value_raw": snapshot["value_raw"],
                "quality": _num(m.get("quality")),
                "ceiling": _num(m.get("ceiling")),
                "usage": _num(m.get("usage")),
                "rank": _num(m.get("position_rank"), 0),
                "depth": _num(m.get("position_depth"), 0),
                "starter_gap": _num(m.get("starter_gap")),
                "group": m.get("best_position_group"),
                "growth_total": _num(m.get("attribute_growth_total"), 1),
                "prev_game_date": deltas[0]["prev_game_date"] if deltas else None,
                "changes": [
                    {
                        "attribute": row["attribute"],
                        "label": labels.get(row["attribute"], row["attribute"]),
                        "group": row["attr_group"],
                        "previous": row["previous"],
                        "current": row["current"],
                        "delta": row["delta"],
                    }
                    for row in deltas
                    if row["delta"] != 0
                ],
            }
        )

    # 통산 성장 (첫 스냅샷 → 마지막 스냅샷)
    overall: dict[str, Any] | None = None
    if len(dates) >= 2:
        result = growth_mod.growth_between(conn, player_id, dates[0], dates[-1])
        if result is not None:
            overall = {
                "from": dates[0],
                "to": dates[-1],
                "days": result.aggregates.get("growth_days"),
                "total": result.total,
                "per_year": _num(result.aggregates.get("attribute_growth_per_year"), 1),
                "by_group": {
                    group: result.aggregates.get(f"{group}_growth")
                    for group in config.ATTRIBUTE_GROUPS
                },
                "changes": [
                    {
                        "attribute": d.attribute,
                        "label": labels.get(d.attribute, d.attribute),
                        "group": d.group,
                        "previous": d.previous,
                        "current": d.current,
                        "delta": d.delta,
                    }
                    for d in result.changed()
                ],
            }

    latest = dates[-1] if dates else None
    attributes = database.get_attributes(conn, player_id, latest) if latest else {}
    latest_snapshot = database.get_snapshot(conn, player_id, latest) if latest else None
    primary, others = formation.choices(latest_snapshot["position"] if latest_snapshot else None, manual_positions)
    grouped: dict[str, list[dict[str, Any]]] = {}
    core_keys: set[str] = set()
    best_group: str | None = None
    if latest:
        latest_metrics = database.get_metrics(conn, player_id, latest)
        best_group = latest_metrics.get("best_position_group")
        core_keys = set(config.CORE_ATTRIBUTES.get(str(best_group), ()))

    for group_name, mapping in config.ATTRIBUTE_GROUPS.items():
        # 필드 플레이어의 GK 능력치는 전부 1~3이라 화면만 어지럽힌다.
        if group_name == "goalkeeping" and best_group != "GK":
            continue
        entries = [
            {
                "attribute": key,
                "label": labels.get(key, key),
                "value": attributes[key],
                "core": key in core_keys,
            }
            for key in mapping.values()
            if key in attributes
        ]
        if entries:
            grouped[group_name] = sorted(entries, key=lambda e: -e["value"])

    return {
        "player": {
            "player_id": player_id,
            "name": player["name"],
            "birth_date": player["birth_date"],
            "nationality": player["nationality"],
            "position": player["primary_position"],
            "primary_position": primary,
            "other_positions": others,
            "manual_positions": manual_positions is not None,
            "first_seen_date": player["first_seen_date"],
            "last_seen_date": player["last_seen_date"],
            "id_source": player["id_source"],
        },
        "origin": {
            "origin": origin["origin"] if origin else None,
            "signed_from": origin["signed_from"] if origin else None,
            "signed_fee": _num(origin["signed_fee"], 0) if origin else None,
            "joined_date": origin["joined_date"] if origin else None,
            "manual": bool(origin["manual_origin"]) if origin else False,
        },
        "timeline": timeline,
        "overall_growth": overall,
        "attributes": grouped,
    }


# ---------------------------------------------------------------------------
# 파일 불러오기
# ---------------------------------------------------------------------------


def list_export_files(conn: sqlite3.Connection, directories: list[Path]) -> list[dict[str, Any]]:
    """불러올 수 있는 HTML 파일 목록.

    파일명에서 날짜를 추측해 기본값으로 제시하고, 이미 들어간 날짜인지도
    알려준다(같은 날짜를 또 넣으면 덮어쓰기 때문).
    """
    known = set(database.distinct_game_dates(conn))
    seen: set[Path] = set()
    files: list[dict[str, Any]] = []

    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.html")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            inferred = utils.infer_date_from_filename(path.name)
            iso = inferred.isoformat() if inferred else None
            files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_kb": round(path.stat().st_size / 1024),
                    "inferred_date": iso,
                    "already_imported": iso in known if iso else False,
                }
            )
    return files


def export_bundle(conn: sqlite3.Connection) -> dict[str, Any]:
    """Firestore에 올릴 전체 묶음을 만든다.

    호스팅된 페이지(pfkfks.org/fm24)는 읽기 전용이라, 화면이 필요로 하는
    것을 미리 계산해 그대로 담는다. Firestore에서 조인을 할 수 없으므로
    비정규화가 맞다.

    **원본(raw_json)은 넣지 않는다.** 384컬럼 원본은 로컬 SQLite에만 두고,
    Firestore에는 화면에 쓰는 것만 올린다 — 문서 크기(1MB)도 아끼고,
    공개 프로젝트에 세이브 원본을 통째로 올릴 이유도 없다.

    Returns:
        ``{"overview": …, "snapshots": {날짜: …}, "players": {id: …}}``.
    """
    dates = database.distinct_game_dates(conn)
    snapshots = {date_iso: squad(conn, date_iso) for date_iso in dates}

    player_ids = [row["player_id"] for row in conn.execute("SELECT player_id FROM players")]
    players = {}
    for player_id in player_ids:
        detail = player_detail(conn, player_id)
        if detail is not None:
            players[player_id] = detail

    return {
        "overview": overview(conn),
        "snapshots": snapshots,
        "players": players,
    }


def inspect_export(path: Path) -> dict[str, Any]:
    """import 전에 파일을 훑어본다 (DB는 건드리지 않는다).

    몇 명인지, 컬럼이 몇 개인지, 날짜 추측값이 뭔지를 보여주고 사용자가
    확인한 뒤에 실제 import를 하게 한다.
    """
    from src import parser  # 순환 import 방지를 위해 지역 import

    result = parser.parse_export(path)
    inferred = utils.infer_date_from_filename(path.name)

    ages = [p.fields.get("age") for p in result.players]
    ages = [a for a in ages if a is not None]

    return {
        "path": str(path),
        "name": path.name,
        "inferred_date": inferred.isoformat() if inferred else None,
        "players": len(result.players),
        "columns": len(result.table.headers),
        "duplicate_headers": len(result.table.duplicates),
        "resolved_fields": len(result.resolved_fields),
        "total_fields": len(config.FIELD_SPECS),
        "attributes_found": len(result.players[0].attributes) if result.players else 0,
        "fallback_ids": sum(1 for p in result.players if p.id_source == "fallback"),
        "youngest": min(ages) if ages else None,
        "oldest": max(ages) if ages else None,
        "sample": [p.name for p in result.players[:8]],
        "warnings": result.warnings,
    }
