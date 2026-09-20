"""로컬 웹 UI를 띄우는 CLI.

사용 예::

    python scripts/serve.py
    python scripts/serve.py --port 9000 --no-browser

띄운 뒤 브라우저에서 http://127.0.0.1:8765 를 연다.
내 PC에서만 접근할 수 있다(127.0.0.1 바인딩).
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

import _bootstrap  # noqa: F401  (sys.path 설정)

from src import config
from web import server


def main(argv: list[str] | None = None) -> int:
    """엔트리 포인트."""
    parser = argparse.ArgumentParser(
        description="FM24 Tracker 로컬 웹 UI를 띄웁니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", "-p", type=int, default=8765, help="포트 (기본 8765)")
    parser.add_argument("--db", default=config.DEFAULT_DB_PATH, help="SQLite 파일 경로")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="바인딩 주소. 기본은 로컬 전용이며, 바꾸면 같은 네트워크에 노출된다.",
    )
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    args = parser.parse_args(argv)

    # 브라우저에는 localhost로 안내한다. 바인딩은 127.0.0.1 그대로지만,
    # Firebase Auth의 기본 승인 도메인에 localhost는 있고 127.0.0.1은 없어서
    # 숫자 주소로 열면 Firestore 동기화 로그인이 막힌다.
    browse_host = "localhost" if args.host == "127.0.0.1" else args.host
    url = f"http://{browse_host}:{args.port}"

    # 이미 떠 있으면(아이콘을 두 번 눌렀다든지) 새로 띄우지 않고 그 창을 쓴다.
    if server.is_already_running(args.host, args.port):
        print(f"FM24 Tracker가 이미 실행 중입니다  →  {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    if not args.no_browser:
        # 서버가 뜨기 전에 열면 연결 거부가 뜨므로 약간 늦춘다.
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    started = server.serve(host=args.host, port=args.port, db_path=args.db)
    return 0 if started else 1


if __name__ == "__main__":
    sys.exit(main())
