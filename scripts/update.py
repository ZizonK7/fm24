"""FM24 HTML export를 DB에 누적하는 CLI.

사용 예::

    python scripts/update.py --file data/players.html --date 2027-03-01
    python scripts/update.py --file data/players.html            # 날짜를 물어본다
    python scripts/update.py --file data/players.html --date 2027-03-01 --roles roles.csv
    python scripts/update.py --file data/players.html --date 2027-03-01 \
        --player 29221846 --role starter

``--date`` 는 **게임 내 날짜**다. 현실 날짜를 넣지 않도록 주의할 것.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import config, database, importer, parser, utils


def build_arg_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    arg_parser = argparse.ArgumentParser(
        description="FM24 HTML export를 게임 내 날짜별 스냅샷으로 누적 저장합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    arg_parser.add_argument("--file", "-f", required=True, help="FM24 export HTML 경로")
    arg_parser.add_argument(
        "--date",
        "-d",
        help="게임 내 날짜 (예: 2027-03-01). 생략하면 실행 중에 물어봅니다.",
    )
    arg_parser.add_argument("--db", default=config.DEFAULT_DB_PATH, help="SQLite 파일 경로")
    arg_parser.add_argument("--encoding", help="HTML 인코딩 강제 지정 (기본: 자동 판별)")
    arg_parser.add_argument("--roles", help="역할 라벨 CSV (player_id, game_date, role)")
    arg_parser.add_argument("--player", help="--role 과 함께 쓸 player_id")
    arg_parser.add_argument(
        "--role",
        choices=config.SQUAD_ROLES,
        help="--player 선수에게 이번 날짜의 운영 역할을 지정",
    )
    arg_parser.add_argument(
        "--no-metrics", action="store_true", help="파생 지표 계산을 건너뜁니다"
    )
    arg_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DB에 쓰지 않고 파싱 결과만 확인합니다",
    )
    arg_parser.add_argument(
        "--show-unmapped",
        action="store_true",
        help="정규화하지 않고 raw로만 보관 중인 컬럼 목록을 출력",
    )
    arg_parser.add_argument(
        "--suggest-attributes",
        action="store_true",
        help="능력치로 보이는데 config에 없는 컬럼을 제안",
    )
    return arg_parser


def prompt_for_date(filename: str | None = None) -> str:
    """``--date`` 가 없을 때 게임 내 날짜를 물어본다.

    파일명이 ``270424.html`` 처럼 날짜로 보이면 그걸 기본값으로 제시한다.
    그냥 Enter를 치면 그 값을 쓴다.
    """
    suggestion = utils.infer_date_from_filename(filename or "")
    print("FM 세이브의 게임 내 날짜를 입력하세요 (예: 2027-03-01).")
    if suggestion is not None:
        print(f"  파일명에서 추측: {suggestion.isoformat()}  (Enter = 사용)")

    while True:
        answer = input("game_date > ").strip()
        if not answer and suggestion is not None:
            return suggestion.isoformat()
        try:
            return utils.parse_game_date(answer).isoformat()
        except ValueError as exc:
            print(f"  {exc}")


def run_dry_run(args: argparse.Namespace, date_iso: str) -> int:
    """DB를 건드리지 않고 파싱 결과만 보여준다."""
    result = parser.parse_export(args.file, args.encoding)
    print(f"Game date: {date_iso}  (dry-run — DB에 쓰지 않음)")
    print(f"Rows: {len(result.players)}")
    print(f"Columns: {len(result.table.headers)}")
    print(f"Duplicate header names: {len(result.table.duplicates)}")
    print(f"Normalised fields resolved: {len(result.resolved_fields)}/{len(config.FIELD_SPECS)}")

    fallback = sum(1 for p in result.players if p.id_source == "fallback")
    print(f"Fallback IDs: {fallback}")

    if result.players:
        sample = result.players[0]
        print(f"\nSample player: {sample.name} (ID {sample.player_id})")
        print(f"  attributes parsed: {len(sample.attributes)}")
        for key in ("age", "position", "club", "squad_level", "minutes", "value_raw", "wage_raw"):
            print(f"  {key}: {sample.fields.get(key)}")

    _print_extras(args, result)
    for warning in result.warnings:
        print(f"  - {warning}")
    return 0


def _print_extras(args: argparse.Namespace, result: parser.ParseResult) -> None:
    """``--show-unmapped`` / ``--suggest-attributes`` 출력."""
    if args.show_unmapped:
        print(f"\nRaw-only columns ({len(result.unmapped_headers)}):")
        for header in result.unmapped_headers:
            print(f"  {header}")
    if args.suggest_attributes:
        suggestions = parser.suggest_attribute_columns(result.table)
        print(f"\n능력치 후보 (config.ATTRIBUTE_GROUPS 미등록, {len(suggestions)}개):")
        for header, score in suggestions:
            print(f"  {header}  (1~20 정수 비율 {score:.0%})")


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    date_iso = (
        utils.parse_game_date(args.date).isoformat()
        if args.date
        else prompt_for_date(args.file)
    )

    if args.dry_run:
        return run_dry_run(args, date_iso)

    conn = database.connect(args.db)
    try:
        added = database.ensure_schema(conn)
        if added:
            print(f"Schema updated: added {len(added)} column(s) — {', '.join(added)}\n")

        summary = importer.import_export(
            conn,
            args.file,
            date_iso,
            encoding=args.encoding,
            compute_metrics=not args.no_metrics,
        )
        print(summary.render())

        # --- 역할 라벨 ---------------------------------------------------
        if args.roles:
            applied, warnings = importer.apply_roles(conn, importer.load_roles_csv(args.roles))
            print(f"\nRoles applied from CSV: {applied}")
            for warning in warnings:
                print(f"  - {warning}")

        if args.role and args.player:
            applied, warnings = importer.apply_roles(
                conn,
                [{"player_id": args.player, "game_date": date_iso, "role": args.role}],
                source="cli",
            )
            print(f"\nRole set: {args.player} → {args.role} ({applied} applied)")
            for warning in warnings:
                print(f"  - {warning}")
        elif bool(args.role) != bool(args.player):
            print("\n⚠ --role 과 --player 는 함께 써야 합니다. 역할을 기록하지 않았습니다.")

        if args.show_unmapped or args.suggest_attributes:
            _print_extras(args, parser.parse_export(args.file, args.encoding))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
