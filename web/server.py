"""로컬 전용 웹 서버 — 표준 라이브러리만 사용.

``python scripts/serve.py`` 로 띄우고 브라우저에서 http://127.0.0.1:8765 를 연다.

설계 원칙
---------
* **127.0.0.1에만 바인딩한다.** 내 PC 밖에서는 접근할 수 없다.
* Flask/FastAPI를 쓰지 않는다. 설치 없이 바로 돌아가는 편이 낫다.
* 외부 CDN을 쓰지 않는다. 인터넷이 없어도 동작해야 한다.
* 업로드 경로는 ``data/raw/`` 안으로만 쓴다 (경로 탈출 차단).

API
---
====== =========================== =================================
GET    /api/overview               DB 전체 요약
GET    /api/squad?date=            한 시점의 스쿼드 전체
GET    /api/player?id=             선수 상세 이력
GET    /api/files                  불러올 수 있는 HTML 목록
POST   /api/inspect                {path} → import 전 미리보기
POST   /api/upload?filename=       파일 본문 업로드 → 미리보기
POST   /api/import                 {path, game_date} → 실제 import
POST   /api/role                   {player_id, game_date, role}
POST   /api/origin                 {player_id, origin}
POST   /api/recompute              지표 재계산
====== =========================== =================================
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import traceback
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import config, database, importer, utils  # noqa: E402
from web import api  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: 기본 경로. 실제로 쓰이는 값은 :class:`Handler` 의 클래스 속성이라
#: 테스트에서 임시 디렉터리로 바꿔 끼울 수 있다 (프로젝트 data/ 오염 방지).
DEFAULT_RAW_DIR = PROJECT_ROOT / config.DEFAULT_RAW_DIR
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"

#: 업로드 파일명에서 허용할 문자. 경로 구분자와 ``..`` 를 막는다.
_SAFE_NAME = re.compile(r"[^0-9A-Za-z가-힣._-]")

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
"""FM export는 보통 1MB 미만이다. 넉넉히 잡되 무한정은 받지 않는다."""

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class ApiError(Exception):
    """클라이언트에게 그대로 보여줄 오류."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def safe_filename(name: str) -> str:
    """업로드된 파일명을 안전한 형태로 바꾼다.

    경로 구분자를 제거하므로 ``../../etc/passwd`` 같은 이름이 들어와도
    ``etcpasswd`` 가 된다.
    """
    stem = Path(name).name or "upload.html"
    cleaned = _SAFE_NAME.sub("_", stem)
    if not cleaned.lower().endswith(".html"):
        cleaned += ".html"
    return cleaned


def resolve_export_path(raw: str, data_dir: Path) -> Path:
    """클라이언트가 준 경로가 ``data_dir`` 안인지 확인한다.

    존재 여부보다 **경계 검사를 먼저** 한다. 밖을 가리키는 경로는 그 파일이
    실제로 있든 없든 403이어야 하고, 404/403 차이로 파일 존재를 알려주지
    않는 편이 낫다.

    Raises:
        ApiError: data/ 바깥을 가리키거나 파일이 없을 때.
    """
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.resolve()

    try:
        path.relative_to(data_dir.resolve())
    except ValueError as exc:
        raise ApiError("data/ 폴더 안의 파일만 열 수 있습니다.", 403) from exc
    if not path.is_file():
        raise ApiError(f"파일이 없습니다: {raw}", 404)
    return path


# ---------------------------------------------------------------------------
# 핸들러 본체
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """정적 파일 + JSON API를 함께 다루는 핸들러."""

    server_version = "FM24Tracker/0.1"

    #: 클래스 속성이라 테스트에서 통째로 바꿔 끼울 수 있다.
    db_path: str = config.DEFAULT_DB_PATH
    raw_dir: Path = DEFAULT_RAW_DIR
    data_dir: Path = DEFAULT_DATA_DIR

    # -- 공통 ------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        """기본 로그가 너무 시끄러워서 요약만 남긴다."""
        if self.path.startswith("/api/"):
            sys.stderr.write(f"  {self.command} {self.path}\n")

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            raise ApiError("파일이 너무 큽니다.", 413)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict[str, Any]:
        body = self._read_body()
        if not body:
            return {}
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(f"요청 본문을 읽을 수 없습니다: {exc}") from exc
        if not isinstance(payload, dict):
            raise ApiError("요청 본문은 객체여야 합니다.")
        return payload

    def _query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def _param(self, name: str, default: str | None = None) -> str | None:
        values = self._query().get(name)
        return values[0] if values else default

    def _connect(self) -> "closing[sqlite3.Connection]":
        """요청 하나짜리 DB 연결.

        ``with sqlite3.connect(...)`` 는 트랜잭션만 관리하고 **연결을 닫지
        않는다.** 그대로 두면 요청마다 연결이 새고, Windows에서는 DB 파일이
        잠긴 채로 남는다. 그래서 :func:`contextlib.closing` 으로 감싼다.
        """
        conn = database.connect(self.db_path)
        database.ensure_schema(conn)
        return closing(conn)

    # -- 라우팅 ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        route = urlparse(self.path).path
        try:
            if route.startswith("/api/"):
                handler = GET_ROUTES.get(route)
                if handler is None:
                    raise ApiError(f"알 수 없는 API: {route}", 404)
                self._send_json(handler(self))
            else:
                self._serve_static(route)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 - 서버가 죽지 않게
            traceback.print_exc()
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        try:
            handler = POST_ROUTES.get(route)
            if handler is None:
                raise ApiError(f"알 수 없는 API: {route}", 404)
            self._send_json(handler(self))
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    # -- 정적 파일 -------------------------------------------------------
    def _serve_static(self, route: str) -> None:
        relative = "index.html" if route in ("/", "") else route.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.is_file():
            self.send_error(404)
            return

        body = target.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# API 구현
# ---------------------------------------------------------------------------


def _api_overview(handler: Handler) -> dict[str, Any]:
    with handler._connect() as conn:
        return api.overview(conn)


def _api_squad(handler: Handler) -> dict[str, Any]:
    with handler._connect() as conn:
        return api.squad(conn, handler._param("date"))


def _api_player(handler: Handler) -> dict[str, Any]:
    player_id = handler._param("id")
    if not player_id:
        raise ApiError("id 파라미터가 필요합니다.")
    with handler._connect() as conn:
        detail = api.player_detail(conn, player_id)
    if detail is None:
        raise ApiError(f"선수를 찾을 수 없습니다: {player_id}", 404)
    return detail


def _api_bundle(handler: Handler) -> dict[str, Any]:
    """Firestore 동기화용 전체 묶음. 브라우저가 받아서 직접 올린다."""
    with handler._connect() as conn:
        return api.export_bundle(conn)


def _api_files(handler: Handler) -> dict[str, Any]:
    with handler._connect() as conn:
        return {"files": api.list_export_files(conn, [handler.raw_dir, handler.data_dir])}


def _api_inspect(handler: Handler) -> dict[str, Any]:
    payload = handler._read_json()
    path = resolve_export_path(str(payload.get("path") or ""), handler.data_dir)
    return api.inspect_export(path)


def _api_upload(handler: Handler) -> dict[str, Any]:
    """파일 본문을 그대로 받아 ``data/raw/`` 에 저장하고 미리보기를 돌려준다.

    multipart 대신 raw 본문을 쓰는 이유: 브라우저에서 ``file.arrayBuffer()``
    로 그냥 보내면 되고, 표준 라이브러리로 multipart를 파싱할 필요가 없다.
    """
    name = safe_filename(handler._param("filename") or "upload.html")
    body = handler._read_body()
    if not body:
        raise ApiError("빈 파일입니다.")

    handler.raw_dir.mkdir(parents=True, exist_ok=True)
    target = handler.raw_dir / name
    target.write_bytes(body)

    result = api.inspect_export(target)
    result["saved_as"] = target.name
    return result


def _api_import(handler: Handler) -> dict[str, Any]:
    payload = handler._read_json()
    path = resolve_export_path(str(payload.get("path") or ""), handler.data_dir)
    raw_date = str(payload.get("game_date") or "").strip()
    if not raw_date:
        raise ApiError("게임 내 날짜(game_date)가 필요합니다.")
    try:
        date_iso = utils.parse_game_date(raw_date).isoformat()
    except ValueError as exc:
        raise ApiError(str(exc)) from exc

    with handler._connect() as conn:
        summary = importer.import_export(conn, path, date_iso)

    return {
        "game_date": summary.game_date,
        "imported": summary.imported,
        "new_players": summary.new_players,
        "existing_players": summary.existing_players,
        "with_previous": summary.with_previous,
        "growth_calculated": summary.growth_calculated,
        "replaced": summary.replaced,
        "signed": summary.signed_players,
        "youth": summary.youth_players,
        "roles_auto": summary.roles_auto,
        "fallback_ids": summary.fallback_ids,
        "columns": summary.columns_total,
        "duplicate_headers": summary.duplicate_headers,
        "warnings": summary.warnings,
    }


def _api_role(handler: Handler) -> dict[str, Any]:
    payload = handler._read_json()
    player_id = str(payload.get("player_id") or "")
    role = str(payload.get("role") or "")
    game_date = str(payload.get("game_date") or "")
    if not player_id or not game_date:
        raise ApiError("player_id와 game_date가 필요합니다.")
    if role not in config.SQUAD_ROLES:
        raise ApiError(f"역할은 {', '.join(config.SQUAD_ROLES)} 중 하나여야 합니다.")

    with handler._connect() as conn:
        database.upsert_role(conn, player_id, game_date, role, source="manual")
        conn.commit()
    return {"ok": True, "player_id": player_id, "game_date": game_date, "role": role}


def _api_origin(handler: Handler) -> dict[str, Any]:
    payload = handler._read_json()
    player_id = str(payload.get("player_id") or "")
    origin = str(payload.get("origin") or "")
    if not player_id:
        raise ApiError("player_id가 필요합니다.")
    if origin not in config.PLAYER_ORIGINS:
        raise ApiError(f"출신은 {', '.join(config.PLAYER_ORIGINS)} 중 하나여야 합니다.")

    with handler._connect() as conn:
        existing = database.get_player_origin(conn, player_id)
        database.set_player_origin(
            conn,
            player_id,
            origin,
            signed_from=existing["signed_from"] if existing else None,
            signed_fee=existing["signed_fee"] if existing else None,
            joined_date=existing["joined_date"] if existing else None,
            manual=True,
        )
        conn.commit()
    return {"ok": True, "player_id": player_id, "origin": origin}


def _api_positions(handler: Handler) -> dict[str, Any]:
    payload = handler._read_json()
    player_id = str(payload.get("player_id") or "")
    primary = payload.get("primary_position")
    others = payload.get("other_positions")
    allowed = set(config.POSITION_GROUPS)
    if not isinstance(primary, str) or primary not in allowed:
        raise ApiError("올바른 주 포지션을 선택하세요.")
    if not isinstance(others, list) or any(not isinstance(value, str) or value not in allowed for value in others):
        raise ApiError("가능 포지션 목록이 올바르지 않습니다.")
    if primary in others or len(others) != len(set(others)):
        raise ApiError("주 포지션과 가능 포지션은 중복될 수 없습니다.")
    with handler._connect() as conn:
        if not player_id or database.get_player(conn, player_id) is None:
            raise ApiError("선수를 찾을 수 없습니다.", 404)
        database.set_player_positions(conn, player_id, primary, others)
        conn.commit()
        importer.recompute_all(conn)
    return {"ok": True, "player_id": player_id, "primary_position": primary, "other_positions": others}


def _api_recompute(handler: Handler) -> dict[str, Any]:
    with handler._connect() as conn:
        return importer.recompute_all(conn)


GET_ROUTES: dict[str, Callable[[Handler], Any]] = {
    "/api/overview": _api_overview,
    "/api/squad": _api_squad,
    "/api/player": _api_player,
    "/api/files": _api_files,
    "/api/bundle": _api_bundle,
}

POST_ROUTES: dict[str, Callable[[Handler], Any]] = {
    "/api/inspect": _api_inspect,
    "/api/upload": _api_upload,
    "/api/import": _api_import,
    "/api/role": _api_role,
    "/api/origin": _api_origin,
    "/api/positions": _api_positions,
    "/api/recompute": _api_recompute,
}


def is_already_running(host: str, port: int) -> bool:
    """그 주소에서 이미 우리 서버가 돌고 있는지 확인한다.

    바탕화면 아이콘을 두 번 눌렀을 때 "포트 사용 중" 오류를 내는 대신
    기존 창을 그냥 쓰게 하기 위한 것. 다른 프로그램이 같은 포트를 쓰고
    있을 수도 있으므로, 응답 헤더로 우리 서버인지까지 확인한다.
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/overview", timeout=2) as response:
            return response.headers.get("Server", "").startswith("FM24Tracker")
    except (urllib.error.URLError, OSError):
        return False


def serve(host: str = "127.0.0.1", port: int = 8765, db_path: str | None = None) -> bool:
    """서버를 띄운다 (Ctrl+C로 종료).

    Args:
        host: 바인딩 주소. 기본은 로컬 전용.
        port: 포트.
        db_path: SQLite 경로.

    Returns:
        실제로 서버를 띄웠으면 True, 포트를 쓸 수 없어 못 띄웠으면 False.
    """
    Handler.db_path = db_path or config.DEFAULT_DB_PATH
    utils.enable_utf8_stdout()

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"포트 {port} 을(를) 쓸 수 없습니다: {exc}")
        if is_already_running(host, port):
            print("이미 FM24 Tracker가 실행 중입니다. 브라우저만 열겠습니다.")
        else:
            print(f"다른 프로그램이 쓰고 있는 것 같습니다. --port 로 다른 번호를 주세요.")
        return False

    browse_host = "localhost" if host == "127.0.0.1" else host
    with httpd:
        print(f"FM24 Tracker  →  http://{browse_host}:{port}")
        print(f"  DB: {Handler.db_path}")
        print("  이 창을 닫거나 Ctrl+C 를 누르면 종료됩니다.\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n종료합니다.")
    return True
