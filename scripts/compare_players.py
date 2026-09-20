"""여러 선수를 나란히 비교하는 CLI.

사용 예::

    python scripts/compare_players.py --ids 29221846 r-2002146684
    python scripts/compare_players.py --names "Trey Nyoni" "Michael Olise"
    python scripts/compare_players.py --ids 29221846 r-2002146684 --attributes
    python scripts/compare_players.py --ids 29221846 r-2002146684 --date 2027-03-01
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import config, database, utils

COLUMN_WIDTH = 16


def _fmt(value: object, digits: int = 2) -> str:
    """표에 넣을 짧은 문자열."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def resolve(conn: sqlite3.Connection, ids: list[str], names: list[str]) -> list[str]:
    """``--ids`` / ``--names`` 를 player_id 목록으로 바꾼다.

    이름이 여러 명과 일치하면 건너뛰고 경고한다(잘못된 선수를 조용히 비교하는
    것보다 낫다).
    """
    resolved: list[str] = []
    for player_id in ids:
        if database.get_player(conn, player_id) is None:
            print(f"⚠ player_id '{player_id}' 없음 — 건너뜁니다.")
            continue
        resolved.append(player_id)

    for name in names:
        matches = database.find_players_by_name(conn, name)
        if not matches:
            print(f"⚠ '{name}' 과 일치하는 선수 없음 — 건너뜁니다.")
        elif len(matches) > 1:
            found = ", ".join(row["player_id"] for row in matches)
            print(f"⚠ '{name}' 이 {len(matches)}명과 일치 ({found}) — --ids 로 지정하세요.")
        else:
            resolved.append(matches[0]["player_id"])
    return resolved


def _row(label: str, values: list[str], width: int = COLUMN_WIDTH) -> str:
    """라벨 + 값들을 한 줄로 정렬한다.

    한글은 터미널에서 두 칸을 차지하므로 :func:`src.utils.pad` 로 **표시 폭**
    기준 정렬을 한다. 폭을 넘는 값(긴 포지션 문자열 등)은 잘라낸다.
    """
    cells = "".join(utils.pad(value, width, "right") for value in values)
    return f"  {utils.pad(label, 24)}{cells}"


def print_table(conn: sqlite3.Connection, player_ids: list[str], date: str | None) -> None:
    """선수들의 기본 정보/활용도/지표를 표로 출력한다."""
    columns: list[dict[str, object]] = []
    for player_id in player_ids:
        game_date = (
            utils.parse_game_date(date).isoformat()
            if date
            else database.latest_game_date(conn, player_id)
        )
        snapshot = database.get_snapshot(conn, player_id, game_date) if game_date else None
        if snapshot is None:
            print(f"⚠ {player_id}: {date or '최신'} 스냅샷 없음 — 건너뜁니다.")
            continue
        columns.append(
            {
                "player_id": player_id,
                "game_date": game_date,
                "snapshot": dict(snapshot),
                "metrics": database.get_metrics(conn, player_id, game_date),
                "attributes": database.get_attributes(conn, player_id, game_date),
                "role": database.get_role(conn, player_id, game_date),
            }
        )

    if not columns:
        print("비교할 스냅샷이 없습니다.")
        return

    names = [str(column["snapshot"].get("name") or column["player_id"]) for column in columns]
    print(_row("", names))
    print("  " + "-" * (24 + COLUMN_WIDTH * len(columns) - 2))

    def snap(key: str, digits: int = 2) -> list[str]:
        return [_fmt(column["snapshot"].get(key), digits) for column in columns]

    def met(key: str, digits: int = 2) -> list[str]:
        return [_fmt(column["metrics"].get(key), digits) for column in columns]

    print(_row("game_date", [str(column["game_date"]) for column in columns]))
    print(_row("age", snap("age", 0)))
    print(_row("position", snap("position")))
    print(_row("club", snap("club")))
    print(_row("squad_level", snap("squad_level")))
    print(_row("squad_role", [_fmt(column["role"]) for column in columns]))
    print()
    print(_row("minutes", snap("minutes", 0)))
    print(_row("appearances", snap("appearances_raw")))
    print(_row("goals", snap("goals", 0)))
    print(_row("assists", snap("assists", 0)))
    print(_row("avg_rating", snap("avg_rating")))
    print()
    print(_row("quality", met("quality")))
    print(_row("ceiling", met("ceiling")))
    print(_row("usage", met("usage")))
    print(_row("growth (/yr)", met("growth")))
    print(_row("talent_score", met("talent_score")))
    print(_row("balance_score", met("balance_score")))
    print(_row("floor_score", met("floor_score")))
    print()
    print(_row("total growth", met("attribute_growth_total", 0)))
    for group in config.ATTRIBUTE_GROUPS:
        print(_row(f"  {group}_growth", met(f"{group}_growth", 0)))


def print_attribute_table(conn: sqlite3.Connection, player_ids: list[str], date: str | None) -> None:
    """능력치를 그룹별로 나란히 출력한다."""
    labels = config.attribute_labels()
    data: list[tuple[str, dict[str, float]]] = []
    for player_id in player_ids:
        game_date = (
            utils.parse_game_date(date).isoformat()
            if date
            else database.latest_game_date(conn, player_id)
        )
        if game_date is None:
            continue
        player = database.get_player(conn, player_id)
        name = (player["name"] if player else player_id) or player_id
        data.append((name, database.get_attributes(conn, player_id, game_date)))

    if not data:
        return

    print()
    print(_row("attribute", [name for name, _ in data]))
    print("  " + "-" * (24 + COLUMN_WIDTH * len(data) - 2))
    for group, mapping in config.ATTRIBUTE_GROUPS.items():
        printed_group = False
        for key in mapping.values():
            values = [attributes.get(key) for _, attributes in data]
            if all(value is None for value in values):
                continue
            if not printed_group:
                print(f"  [{group}]")
                printed_group = True
            print(_row(f"  {labels.get(key, key)}", [_fmt(value, 0) for value in values]))


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    arg_parser = argparse.ArgumentParser(
        description="선수 여러 명을 같은 시점 기준으로 비교합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    arg_parser.add_argument("--ids", nargs="*", default=[], help="player_id 목록 (권장)")
    arg_parser.add_argument("--names", nargs="*", default=[], help="선수 이름 목록")
    arg_parser.add_argument("--db", default=config.DEFAULT_DB_PATH, help="SQLite 파일 경로")
    arg_parser.add_argument("--date", help="비교 기준 게임 내 날짜 (기본: 각자의 최신)")
    arg_parser.add_argument("--attributes", action="store_true", help="능력치도 나란히 출력")
    args = arg_parser.parse_args(argv)

    if not args.ids and not args.names:
        arg_parser.error("--ids 또는 --names 중 하나는 있어야 합니다.")

    conn = database.connect(args.db)
    try:
        database.ensure_schema(conn)
        player_ids = resolve(conn, args.ids, args.names)
        if len(player_ids) < 2:
            print("비교하려면 최소 2명이 필요합니다.")
            return 1
        print_table(conn, player_ids, args.date)
        if args.attributes:
            print_attribute_table(conn, player_ids, args.date)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
