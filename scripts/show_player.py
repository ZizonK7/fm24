"""선수 한 명의 최신 스냅샷과 성장 내역을 출력하는 CLI.

사용 예::

    python scripts/show_player.py --name "Trey Nyoni"
    python scripts/show_player.py --id 29221846
    python scripts/show_player.py --id 29221846 --date 2027-03-01
    python scripts/show_player.py --id 29221846 --history
    python scripts/show_player.py --id 29221846 --attributes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import config, database, growth as growth_mod, metrics as metrics_mod, utils


def _fmt(value: object, digits: int = 2, dash: str = "-") -> str:
    """숫자는 자릿수를 맞추고, None은 ``-`` 로 표시한다."""
    if value is None:
        return dash
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _signed(value: float) -> str:
    """``+1`` / ``-2`` / ``+0`` 형태로."""
    return f"{value:+.0f}" if value == int(value) else f"{value:+.1f}"


def resolve_player(conn: sqlite3.Connection, player_id: str | None, name: str | None) -> str | None:
    """``--id`` 또는 ``--name`` 으로 player_id를 확정한다.

    이름은 primary key가 아니므로 동명이인이 나올 수 있다. 그 경우 후보를
    출력하고 None을 돌려준다.
    """
    if player_id:
        if database.get_player(conn, player_id) is None:
            print(f"player_id '{player_id}' 를 DB에서 찾지 못했습니다.")
            return None
        return player_id

    matches = database.find_players_by_name(conn, name or "")
    if not matches:
        print(f"'{name}' 과 일치하는 선수가 없습니다.")
        return None
    if len(matches) > 1:
        print(f"'{name}' 에 {len(matches)}명이 일치합니다. --id 로 지정하세요:\n")
        for row in matches:
            print(
                f"  {row['player_id']:>12}  {row['name']}  "
                f"({row['nationality']}, {row['primary_position']}, "
                f"{row['first_seen_date']} ~ {row['last_seen_date']})"
            )
        return None
    return matches[0]["player_id"]


def print_snapshot(conn: sqlite3.Connection, player_id: str, game_date: str) -> None:
    """한 시점의 신원/활용도/지표를 출력한다."""
    snapshot = database.get_snapshot(conn, player_id, game_date)
    if snapshot is None:
        print(f"{game_date} 스냅샷이 없습니다.")
        return

    print(f"Game date: {game_date}")
    print()
    print(f"Age: {_fmt(snapshot['age'], 0)}")
    print(f"Position: {_fmt(snapshot['position'])}")
    club = _fmt(snapshot["club"])
    squad = snapshot["squad_level"]
    print(f"Club: {club}" + (f" ({squad})" if squad else ""))
    print(f"Value: {_fmt(snapshot['value_raw'])}")
    print(f"Wage: {_fmt(snapshot['wage_raw'])}")
    print(f"Contract: {_fmt(snapshot['contract_start'])} ~ {_fmt(snapshot['contract_expiry'])}")

    role = database.get_role(conn, player_id, game_date)
    if role:
        print(f"Squad role: {role}")
    if snapshot["agreed_playing_time"]:
        print(f"Agreed playing time: {snapshot['agreed_playing_time']}")

    metrics = database.get_metrics(conn, player_id, game_date)

    print()
    print("Usage:")
    print(f"  Appearances: {_fmt(snapshot['appearances_raw'])}")
    print(f"  Starts: {_fmt(snapshot['appearances_starts'], 0)}")
    print(f"  Subs: {_fmt(snapshot['appearances_subs'], 0)}")
    print(f"  Minutes: {_fmt(snapshot['minutes'], 0)}")
    print(f"  Goals / Assists: {_fmt(snapshot['goals'], 0)} / {_fmt(snapshot['assists'], 0)}")
    print(f"  Avg rating: {_fmt(snapshot['avg_rating'])}")
    print(f"  Usage score: {_fmt(metrics.get('usage'))}   (스쿼드 내 최대 출장시간 대비)")
    print(f"  Start share: {_fmt(metrics.get('usage_start_share'))}")

    print()
    print(f"Metrics ({metrics_mod.FORMULA_VERSION} placeholder):")
    for name in ("quality", "ceiling", "growth", "talent_score", "balance_score", "floor_score"):
        print(f"  {name}: {_fmt(metrics.get(name))}")


def print_growth(conn: sqlite3.Connection, player_id: str, game_date: str) -> None:
    """직전 스냅샷 대비 능력치 변화를 출력한다."""
    rows = database.get_growth(conn, player_id, game_date)
    if not rows:
        previous = database.previous_game_date(conn, player_id, game_date)
        print()
        if previous is None:
            print("Growth: 이전 스냅샷이 없습니다 (첫 관측).")
        else:
            print(f"Growth: {previous} 스냅샷과 비교 가능한 능력치가 없습니다.")
        return

    previous_date = rows[0]["prev_game_date"]
    days = utils.days_between(previous_date, game_date)
    labels = config.attribute_labels()

    print()
    span = f"{previous_date} → {game_date}"
    print(f"Growth since previous snapshot ({span}" + (f", {days}일)" if days is not None else ")") + ":")

    changed = [row for row in rows if (row["delta"] or 0) != 0]
    if not changed:
        print("  (변화 없음)")
    for row in changed:
        label = f"{labels.get(row['attribute'], row['attribute'])} ({row['attribute']})"
        print(
            f"  {utils.pad(label, 28)} {_signed(row['delta']):>3}"
            f"   [{_fmt(row['previous'], 0)} → {_fmt(row['current'], 0)}]"
        )

    metrics = database.get_metrics(conn, player_id, game_date)
    total = metrics.get("attribute_growth_total")
    print()
    print(f"Total attribute growth: {_signed(total) if total is not None else '-'}")
    for group in config.ATTRIBUTE_GROUPS:
        value = metrics.get(f"{group}_growth")
        if value is not None:
            print(f"  {group}: {_signed(value)}")
    print(f"  attributes compared: {_fmt(metrics.get('attributes_compared'), 0)}")


def print_attributes(conn: sqlite3.Connection, player_id: str, game_date: str) -> None:
    """능력치 전체를 그룹별로 출력한다."""
    attributes = database.get_attributes(conn, player_id, game_date)
    if not attributes:
        print("\n저장된 능력치가 없습니다.")
        return
    labels = config.attribute_labels()
    print()
    print("Attributes:")
    for group, mapping in config.ATTRIBUTE_GROUPS.items():
        present = [(key, attributes[key]) for key in mapping.values() if key in attributes]
        if not present:
            continue
        print(f"  [{group}]")
        for key, value in sorted(present, key=lambda pair: -pair[1]):
            print(f"    {utils.pad(labels.get(key, key), 14)}{value:>3.0f}")


def print_history(conn: sqlite3.Connection, player_id: str) -> None:
    """전체 스냅샷 이력을 한 줄씩 출력한다."""
    dates = database.snapshot_dates(conn, player_id)
    print()
    print(f"History ({len(dates)} snapshots):")
    header = f"  {'date':<12} {'age':>3} {'mins':>6} {'apps':>8} {'quality':>8} {'usage':>6} {'growth':>7}"
    print(header)
    for date_iso in dates:
        snapshot = database.get_snapshot(conn, player_id, date_iso)
        metrics = database.get_metrics(conn, player_id, date_iso)
        print(
            f"  {date_iso:<12} "
            f"{_fmt(snapshot['age'], 0):>3} "
            f"{_fmt(snapshot['minutes'], 0):>6} "
            f"{_fmt(snapshot['appearances_raw']):>8} "
            f"{_fmt(metrics.get('quality')):>8} "
            f"{_fmt(metrics.get('usage')):>6} "
            f"{_fmt(metrics.get('attribute_growth_total'), 0):>7}"
        )

    if len(dates) >= 2:
        overall = growth_mod.growth_between(conn, player_id, dates[0], dates[-1])
        if overall:
            print()
            print(f"Total growth {dates[0]} → {dates[-1]}: {_signed(overall.total)}")


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    arg_parser = argparse.ArgumentParser(
        description="선수 한 명의 스냅샷과 성장 내역을 봅니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = arg_parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", "-n", help="선수 이름 (동명이인이면 후보를 보여줍니다)")
    group.add_argument("--id", dest="player_id", help="player_id (권장)")
    arg_parser.add_argument("--db", default=config.DEFAULT_DB_PATH, help="SQLite 파일 경로")
    arg_parser.add_argument("--date", help="볼 스냅샷의 게임 내 날짜 (기본: 최신)")
    arg_parser.add_argument("--attributes", action="store_true", help="능력치 전체 출력")
    arg_parser.add_argument("--history", action="store_true", help="스냅샷 이력 전체 출력")
    args = arg_parser.parse_args(argv)

    conn = database.connect(args.db)
    try:
        database.ensure_schema(conn)
        player_id = resolve_player(conn, args.player_id, args.name)
        if player_id is None:
            return 1

        player = database.get_player(conn, player_id)
        game_date = (
            utils.parse_game_date(args.date).isoformat()
            if args.date
            else database.latest_game_date(conn, player_id)
        )
        if game_date is None:
            print(f"{player['name']} 의 스냅샷이 없습니다.")
            return 1

        print(f"{player['name']}   (ID {player_id})")
        if player["id_source"] == "fallback":
            print("⚠ 이 선수는 export에 ID가 없어 이름 기반 임시 ID를 쓰고 있습니다.")
        print()
        print("Latest snapshot:" if not args.date else "Snapshot:")
        print_snapshot(conn, player_id, game_date)
        print_growth(conn, player_id, game_date)

        if args.attributes:
            print_attributes(conn, player_id, game_date)
        if args.history:
            print_history(conn, player_id)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
