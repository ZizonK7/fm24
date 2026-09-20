"""저장된 스냅샷만으로 성장/파생지표를 다시 계산하는 CLI.

``src/metrics.py`` 의 공식을 바꾸거나 ``src/config.py`` 의 능력치 그룹을
수정한 뒤에 실행한다. **HTML을 다시 import할 필요가 없다.**

사용 예::

    python scripts/recompute.py
    python scripts/recompute.py --date 2027-03-01
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import config, database, importer, metrics, utils


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    utils.enable_utf8_stdout()
    arg_parser = argparse.ArgumentParser(
        description="원본 스냅샷은 그대로 두고 파생 지표만 다시 계산합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    arg_parser.add_argument("--db", default=config.DEFAULT_DB_PATH, help="SQLite 파일 경로")
    arg_parser.add_argument("--date", help="특정 게임 내 날짜만 재계산")
    args = arg_parser.parse_args(argv)

    date_iso = utils.parse_game_date(args.date).isoformat() if args.date else None

    conn = database.connect(args.db)
    try:
        counts = importer.recompute_all(conn, date_iso)
        scope = date_iso or "all dates"
        print(f"Recomputed with formula {metrics.FORMULA_VERSION} ({scope})")
        print(f"  snapshots processed: {counts['snapshots']}")
        print(f"  growth recalculated: {counts['growth']}")
        print(f"  metrics recalculated: {counts['metrics']}")
        dates = database.distinct_game_dates(conn)
        if dates:
            print(f"  game dates in db: {', '.join(dates)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
