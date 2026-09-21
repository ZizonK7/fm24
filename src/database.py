"""SQLite 저장소.

테이블 구성과 그 이유
---------------------

``players``
    선수 1명당 1행. 이름/생일 같은 거의 안 변하는 정보와 관측 범위.

``snapshots``
    ``(player_id, game_date)`` 당 1행. 정규화된 주요 필드 + **원본 384컬럼 전부**
    를 담은 ``raw_json``. 컬럼 구성은 :data:`src.config.FIELD_SPECS` 에서
    자동 생성되므로, config에 필드를 추가하고 :func:`ensure_schema` 를 다시
    부르면 ``ALTER TABLE ADD COLUMN`` 이 알아서 실행된다.

``snapshot_attributes``
    능력치는 넓은(wide) 컬럼 대신 ``(player_id, game_date, attribute, value)``
    긴(long) 형태로 저장한다. 능력치를 새로 추가해도 스키마 변경이 없고,
    성장 계산이 단순한 self-join으로 끝난다.

``growth_deltas`` / ``derived_metrics``
    **원본과 완전히 분리된 파생 데이터.** 계산식을 바꾸면 이 두 테이블만
    지우고 다시 계산하면 된다 (``scripts/recompute.py``). HTML 재import는
    절대 필요하지 않다.

``squad_roles``
    starter / rotation / development 운영 라벨. 능력치에서 자동 판정하지
    않고 CSV나 CLI로 사람이 넣는다.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import config, utils

__all__ = [
    "SCHEMA_VERSION",
    "connect",
    "ensure_schema",
    "upsert_player",
    "insert_snapshot",
    "replace_attributes",
    "previous_game_date",
    "latest_game_date",
    "get_snapshot",
    "get_attributes",
    "find_players_by_name",
    "get_player",
    "store_growth_deltas",
    "store_metrics",
    "upsert_role",
    "snapshot_dates",
    "cohort_snapshots",
    "departed_snapshots",
    "parent_club",
    "set_parent_club",
]

SCHEMA_VERSION = 1

#: snapshots 테이블의 고정 컬럼 (정규화 필드보다 앞에 온다).
_FIXED_SNAPSHOT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("player_id", "TEXT NOT NULL"),
    ("game_date", "TEXT NOT NULL"),
)

_TRAILING_SNAPSHOT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("raw_json", "TEXT NOT NULL"),
    ("id_source", "TEXT"),
    ("source_file", "TEXT"),
    ("imported_at", "TEXT"),
)


def _now() -> str:
    """현실 시각 ISO 문자열. 게임 내 날짜와 혼동하지 말 것."""
    return _dt.datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 연결 / 스키마
# ---------------------------------------------------------------------------


def connect(db_path: str | Path = config.DEFAULT_DB_PATH) -> sqlite3.Connection:
    """SQLite에 연결한다. 상위 디렉터리가 없으면 만든다.

    Args:
        db_path: DB 파일 경로. ``:memory:`` 도 받는다.

    Returns:
        ``row_factory`` 가 :class:`sqlite3.Row` 로 설정된 연결.
    """
    path = Path(db_path)
    if str(db_path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _snapshot_ddl_columns() -> list[tuple[str, str]]:
    """snapshots 테이블의 전체 컬럼 목록 (고정 + config 파생 + 꼬리)."""
    return [*_FIXED_SNAPSHOT_COLUMNS, *config.snapshot_columns(), *_TRAILING_SNAPSHOT_COLUMNS]


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    """테이블을 만들고, config에 새로 생긴 필드가 있으면 컬럼을 추가한다.

    여러 번 불러도 안전하다(idempotent).

    Args:
        conn: 열린 연결.

    Returns:
        이번 호출에서 새로 추가된 snapshots 컬럼 이름들.
    """
    columns_sql = ",\n    ".join(f'"{name}" {sqltype}' for name, sqltype in _snapshot_ddl_columns())

    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS players (
            player_id        TEXT PRIMARY KEY,
            name             TEXT,
            birth_date       TEXT,
            nationality      TEXT,
            primary_position TEXT,
            first_seen_date  TEXT,
            last_seen_date   TEXT,
            id_source        TEXT,
            created_at       TEXT,
            updated_at       TEXT
        );

        /* "내가 데려온 선수" 를 가려내기 위한 출신 정보.
           origin 은 import 때 자동 판정되지만 set_player_origin 으로
           언제든 손으로 고칠 수 있다 (manual_origin = 1 이면 자동 판정이
           다시 덮어쓰지 않는다). */
        CREATE TABLE IF NOT EXISTS player_origin (
            player_id     TEXT PRIMARY KEY,
            origin        TEXT,
            signed_from   TEXT,
            signed_fee    REAL,
            joined_date   TEXT,
            manual_origin INTEGER DEFAULT 0,
            updated_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS player_positions (
            player_id TEXT PRIMARY KEY REFERENCES players(player_id),
            primary_position TEXT NOT NULL,
            other_positions TEXT NOT NULL,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            {columns_sql},
            UNIQUE (player_id, game_date)
        );

        CREATE TABLE IF NOT EXISTS snapshot_attributes (
            player_id  TEXT NOT NULL,
            game_date  TEXT NOT NULL,
            attribute  TEXT NOT NULL,
            attr_group TEXT,
            value      REAL,
            PRIMARY KEY (player_id, game_date, attribute)
        );

        CREATE TABLE IF NOT EXISTS growth_deltas (
            player_id      TEXT NOT NULL,
            game_date      TEXT NOT NULL,
            prev_game_date TEXT NOT NULL,
            attribute      TEXT NOT NULL,
            attr_group     TEXT,
            previous       REAL,
            current        REAL,
            delta          REAL,
            PRIMARY KEY (player_id, game_date, attribute)
        );

        CREATE TABLE IF NOT EXISTS derived_metrics (
            player_id       TEXT NOT NULL,
            game_date       TEXT NOT NULL,
            metric          TEXT NOT NULL,
            value_num       REAL,
            value_text      TEXT,
            formula_version TEXT,
            computed_at     TEXT,
            PRIMARY KEY (player_id, game_date, metric)
        );

        CREATE TABLE IF NOT EXISTS squad_roles (
            player_id  TEXT NOT NULL,
            game_date  TEXT NOT NULL,
            role       TEXT NOT NULL,
            source     TEXT,
            note       TEXT,
            updated_at TEXT,
            PRIMARY KEY (player_id, game_date)
        );

        /* 시점별 모(母)구단. 임대 나간 선수는 `구단` 이 임대처로 찍히므로,
           "우리 팀" 이 어디였는지 시점마다 기억해 둬야 판별할 수 있다. */
        CREATE TABLE IF NOT EXISTS squad_meta (
            game_date   TEXT PRIMARY KEY,
            parent_club TEXT,
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS schema_meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_date   ON snapshots (game_date);
        CREATE INDEX IF NOT EXISTS idx_snapshots_player ON snapshots (player_id, game_date);
        CREATE INDEX IF NOT EXISTS idx_attr_player      ON snapshot_attributes (player_id, game_date);
        CREATE INDEX IF NOT EXISTS idx_attr_name        ON snapshot_attributes (attribute);
        CREATE INDEX IF NOT EXISTS idx_growth_player    ON growth_deltas (player_id, game_date);
        CREATE INDEX IF NOT EXISTS idx_metrics_name     ON derived_metrics (metric, game_date);
        CREATE INDEX IF NOT EXISTS idx_players_name     ON players (name);
        """
    )

    added = _migrate_snapshot_columns(conn)

    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()
    return added


def get_player_positions(conn: sqlite3.Connection, player_id: str) -> tuple[str, list[str]] | None:
    row = conn.execute(
        "SELECT primary_position, other_positions FROM player_positions WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    return (row["primary_position"], json.loads(row["other_positions"])) if row else None


def positions_by_player(conn: sqlite3.Connection) -> dict[str, tuple[str, list[str]]]:
    return {
        row["player_id"]: (row["primary_position"], json.loads(row["other_positions"]))
        for row in conn.execute("SELECT * FROM player_positions")
    }


def set_player_positions(
    conn: sqlite3.Connection, player_id: str, primary: str, others: Sequence[str]
) -> None:
    conn.execute(
        "INSERT INTO player_positions (player_id, primary_position, other_positions, updated_at)"
        " VALUES (?, ?, ?, ?) ON CONFLICT(player_id) DO UPDATE SET"
        " primary_position=excluded.primary_position, other_positions=excluded.other_positions,"
        " updated_at=excluded.updated_at",
        (player_id, primary, json.dumps(list(others)), _now()),
    )


def _migrate_snapshot_columns(conn: sqlite3.Connection) -> list[str]:
    """config에는 있는데 테이블에는 없는 snapshots 컬럼을 추가한다.

    기존 데이터는 그대로 두고 새 컬럼만 NULL로 붙인다. 컬럼을 **지우지는
    않는다** — 원본 보존이 우선이다.
    """
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
    added: list[str] = []
    for name, sqltype in _snapshot_ddl_columns():
        if name in existing:
            continue
        # NOT NULL 제약은 ALTER로 붙일 수 없으므로 떼어낸다.
        plain_type = sqltype.replace("NOT NULL", "").strip() or "TEXT"
        conn.execute(f'ALTER TABLE snapshots ADD COLUMN "{name}" {plain_type}')
        added.append(name)
    return added


# ---------------------------------------------------------------------------
# 쓰기
# ---------------------------------------------------------------------------


def upsert_player(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    *,
    name: str | None,
    birth_date: str | None,
    nationality: str | None,
    primary_position: str | None,
    id_source: str = "export",
) -> bool:
    """players 행을 만들거나 갱신한다.

    ``first_seen_date`` / ``last_seen_date`` 는 항상 최소/최대로 갱신되므로,
    과거 날짜의 export를 나중에 넣어도 범위가 올바르게 유지된다.

    Args:
        conn: 연결.
        player_id: 선수 식별자.
        game_date: 이번 스냅샷의 게임 내 날짜 (ISO).
        name, birth_date, nationality, primary_position: 최신 값.
        id_source: ``"export"`` 또는 ``"fallback"``.

    Returns:
        새로 만든 선수면 True, 기존 선수면 False.
    """
    existing = conn.execute(
        "SELECT first_seen_date, last_seen_date FROM players WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    now = _now()

    if existing is None:
        conn.execute(
            "INSERT INTO players (player_id, name, birth_date, nationality, primary_position,"
            " first_seen_date, last_seen_date, id_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (player_id, name, birth_date, nationality, primary_position,
             game_date, game_date, id_source, now, now),
        )
        return True

    first_seen = min(existing["first_seen_date"] or game_date, game_date)
    last_seen = max(existing["last_seen_date"] or game_date, game_date)
    # 이름/포지션은 '가장 최근 관측'만 반영한다. 과거 export를 뒤늦게
    # 넣더라도 최신 상태가 덮이지 않도록 last_seen 갱신일 때만 바꾼다.
    if game_date >= (existing["last_seen_date"] or ""):
        conn.execute(
            "UPDATE players SET name = ?, birth_date = ?, nationality = ?,"
            " primary_position = ?, first_seen_date = ?, last_seen_date = ?,"
            " id_source = ?, updated_at = ? WHERE player_id = ?",
            (name, birth_date, nationality, primary_position, first_seen, last_seen,
             id_source, now, player_id),
        )
    else:
        conn.execute(
            "UPDATE players SET first_seen_date = ?, last_seen_date = ?, updated_at = ?"
            " WHERE player_id = ?",
            (first_seen, last_seen, now, player_id),
        )
    return False


def insert_snapshot(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    fields: Mapping[str, Any],
    raw: Mapping[str, str],
    *,
    id_source: str = "export",
    source_file: str | None = None,
) -> bool:
    """스냅샷 1건을 저장한다. 같은 ``(player_id, game_date)`` 는 덮어쓴다.

    같은 날짜를 다시 import하는 것은 흔한 작업(컬럼 추가 후 재수집)이므로
    에러 대신 갱신으로 처리한다.

    Args:
        fields: 정규화 필드. 테이블에 없는 키는 무시된다.
        raw: 원본 헤더 → 원본 문자열. JSON으로 통째 저장된다.

    Returns:
        같은 날짜의 스냅샷을 덮어썼으면 True.
    """
    known = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
    payload: dict[str, Any] = {
        "player_id": player_id,
        "game_date": game_date,
        "raw_json": json.dumps(raw, ensure_ascii=False),
        "id_source": id_source,
        "source_file": source_file,
        "imported_at": _now(),
    }
    for key, value in fields.items():
        if key in known and key not in payload:
            payload[key] = value

    replaced = conn.execute(
        "SELECT 1 FROM snapshots WHERE player_id = ? AND game_date = ?",
        (player_id, game_date),
    ).fetchone() is not None

    columns = ", ".join(f'"{name}"' for name in payload)
    placeholders = ", ".join("?" for _ in payload)
    conn.execute(
        f"INSERT OR REPLACE INTO snapshots ({columns}) VALUES ({placeholders})",
        tuple(payload.values()),
    )
    return replaced


def replace_attributes(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    attributes: Mapping[str, float],
    groups: Mapping[str, str],
) -> None:
    """해당 시점의 능력치를 통째로 교체한다."""
    conn.execute(
        "DELETE FROM snapshot_attributes WHERE player_id = ? AND game_date = ?",
        (player_id, game_date),
    )
    conn.executemany(
        "INSERT INTO snapshot_attributes (player_id, game_date, attribute, attr_group, value)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (player_id, game_date, key, groups.get(key), value)
            for key, value in attributes.items()
        ],
    )


def store_growth_deltas(conn: sqlite3.Connection, rows: Sequence[Mapping[str, Any]]) -> None:
    """성장 delta를 저장한다. 같은 키는 덮어쓴다."""
    conn.executemany(
        "INSERT OR REPLACE INTO growth_deltas"
        " (player_id, game_date, prev_game_date, attribute, attr_group, previous, current, delta)"
        " VALUES (:player_id, :game_date, :prev_game_date, :attribute, :attr_group,"
        " :previous, :current, :delta)",
        list(rows),
    )


def store_metrics(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    metrics: Mapping[str, Any],
    formula_version: str,
) -> None:
    """파생 지표를 저장한다. 숫자면 value_num, 아니면 value_text에 들어간다."""
    now = _now()
    rows = []
    for metric, value in metrics.items():
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            rows.append((player_id, game_date, metric, float(value), None, formula_version, now))
        else:
            rows.append((player_id, game_date, metric, None, str(value), formula_version, now))
    conn.executemany(
        "INSERT OR REPLACE INTO derived_metrics"
        " (player_id, game_date, metric, value_num, value_text, formula_version, computed_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


def set_player_origin(
    conn: sqlite3.Connection,
    player_id: str,
    origin: str,
    *,
    signed_from: str | None = None,
    signed_fee: float | None = None,
    joined_date: str | None = None,
    manual: bool = False,
) -> bool:
    """선수의 출신(영입/유스)을 기록한다.

    사람이 직접 지정한 값(``manual=True``)은 이후의 자동 판정이 덮어쓰지
    않는다. FM 데이터만으로는 애매한 경우(임대 복귀, 유스 승격 직후 이적료
    표기 등)를 사람이 바로잡을 수 있게 하기 위한 것이다.

    Args:
        origin: ``"signed"`` / ``"youth"`` / ``"unknown"``.
        manual: 사람이 직접 지정한 값인지.

    Returns:
        실제로 기록했으면 True. 수동 값이 있어 건너뛰었으면 False.
    """
    existing = conn.execute(
        "SELECT manual_origin FROM player_origin WHERE player_id = ?", (player_id,)
    ).fetchone()
    if existing is not None and existing["manual_origin"] and not manual:
        return False

    conn.execute(
        "INSERT OR REPLACE INTO player_origin"
        " (player_id, origin, signed_from, signed_fee, joined_date, manual_origin, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (player_id, origin, signed_from, signed_fee, joined_date, int(manual), _now()),
    )
    return True


def get_player_origin(conn: sqlite3.Connection, player_id: str) -> sqlite3.Row | None:
    """선수의 출신 정보 행."""
    return conn.execute(
        "SELECT * FROM player_origin WHERE player_id = ?", (player_id,)
    ).fetchone()


#: 자동 판정이 덮어쓰면 안 되는 역할 출처 (사람이 직접 넣은 것들).
MANUAL_ROLE_SOURCES: frozenset[str] = frozenset({"manual", "csv", "cli"})


def upsert_role(
    conn: sqlite3.Connection,
    player_id: str,
    game_date: str,
    role: str,
    source: str = "manual",
    note: str | None = None,
) -> bool:
    """운영 역할 라벨(starter/rotation/development/fringe)을 기록한다.

    FM의 `실제 출전 시간` 에서 자동 판정한 라벨(``source="fm_auto"``)은
    **사람이 직접 넣은 라벨을 덮어쓰지 않는다.** 자동 판정이 마음에 안 들어
    손으로 고친 것을 다음 import가 되돌리면 안 되기 때문이다.

    Returns:
        실제로 기록했으면 True, 수동 라벨이 있어 건너뛰었으면 False.
    """
    if source not in MANUAL_ROLE_SOURCES:
        existing = conn.execute(
            "SELECT source FROM squad_roles WHERE player_id = ? AND game_date = ?",
            (player_id, game_date),
        ).fetchone()
        if existing is not None and existing["source"] in MANUAL_ROLE_SOURCES:
            return False

    conn.execute(
        "INSERT OR REPLACE INTO squad_roles (player_id, game_date, role, source, note, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (player_id, game_date, role, source, note, _now()),
    )
    return True


def clear_derived(conn: sqlite3.Connection, game_date: str | None = None) -> None:
    """파생 테이블을 비운다. 원본(snapshots)은 건드리지 않는다.

    Args:
        game_date: 특정 날짜만 지우려면 지정. None이면 전체.
    """
    if game_date is None:
        conn.execute("DELETE FROM growth_deltas")
        conn.execute("DELETE FROM derived_metrics")
    else:
        conn.execute("DELETE FROM growth_deltas WHERE game_date = ?", (game_date,))
        conn.execute("DELETE FROM derived_metrics WHERE game_date = ?", (game_date,))


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------


def get_player(conn: sqlite3.Connection, player_id: str) -> sqlite3.Row | None:
    """players 행 하나."""
    return conn.execute("SELECT * FROM players WHERE player_id = ?", (player_id,)).fetchone()


def find_players_by_name(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """이름으로 선수를 찾는다. 정확히 일치 우선, 없으면 부분 일치.

    이름은 primary key가 아니므로 결과가 여러 개일 수 있다. 호출부에서
    동명이인을 사용자에게 보여줘야 한다.
    """
    exact = conn.execute(
        "SELECT * FROM players WHERE name = ? COLLATE NOCASE ORDER BY name", (name,)
    ).fetchall()
    if exact:
        return exact
    return conn.execute(
        "SELECT * FROM players WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
        (f"%{name}%",),
    ).fetchall()


def snapshot_dates(conn: sqlite3.Connection, player_id: str) -> list[str]:
    """해당 선수의 모든 스냅샷 날짜 (오름차순)."""
    return [
        row["game_date"]
        for row in conn.execute(
            "SELECT game_date FROM snapshots WHERE player_id = ? ORDER BY game_date",
            (player_id,),
        )
    ]


def latest_game_date(conn: sqlite3.Connection, player_id: str) -> str | None:
    """가장 최근 스냅샷 날짜."""
    row = conn.execute(
        "SELECT MAX(game_date) AS d FROM snapshots WHERE player_id = ?", (player_id,)
    ).fetchone()
    return row["d"] if row else None


def previous_game_date(conn: sqlite3.Connection, player_id: str, game_date: str) -> str | None:
    """``game_date`` 직전 스냅샷의 날짜. 없으면 None."""
    row = conn.execute(
        "SELECT MAX(game_date) AS d FROM snapshots WHERE player_id = ? AND game_date < ?",
        (player_id, game_date),
    ).fetchone()
    return row["d"] if row else None


def get_snapshot(conn: sqlite3.Connection, player_id: str, game_date: str) -> sqlite3.Row | None:
    """특정 시점 스냅샷."""
    return conn.execute(
        "SELECT * FROM snapshots WHERE player_id = ? AND game_date = ?",
        (player_id, game_date),
    ).fetchone()


def get_attributes(conn: sqlite3.Connection, player_id: str, game_date: str) -> dict[str, float]:
    """특정 시점 능력치 ``{키: 값}``."""
    return {
        row["attribute"]: row["value"]
        for row in conn.execute(
            "SELECT attribute, value FROM snapshot_attributes"
            " WHERE player_id = ? AND game_date = ?",
            (player_id, game_date),
        )
    }


def get_growth(conn: sqlite3.Connection, player_id: str, game_date: str) -> list[sqlite3.Row]:
    """특정 시점의 성장 delta 행들 (변화량 내림차순)."""
    return conn.execute(
        "SELECT * FROM growth_deltas WHERE player_id = ? AND game_date = ?"
        " ORDER BY delta DESC, attribute",
        (player_id, game_date),
    ).fetchall()


def get_metrics(conn: sqlite3.Connection, player_id: str, game_date: str) -> dict[str, Any]:
    """특정 시점의 파생 지표 ``{이름: 값}``."""
    result: dict[str, Any] = {}
    for row in conn.execute(
        "SELECT metric, value_num, value_text FROM derived_metrics"
        " WHERE player_id = ? AND game_date = ?",
        (player_id, game_date),
    ):
        result[row["metric"]] = row["value_num"] if row["value_num"] is not None else row["value_text"]
    return result


def get_role(conn: sqlite3.Connection, player_id: str, game_date: str) -> str | None:
    """해당 날짜 이전(포함)의 가장 최근 역할 라벨."""
    row = conn.execute(
        "SELECT role FROM squad_roles WHERE player_id = ? AND game_date <= ?"
        " ORDER BY game_date DESC LIMIT 1",
        (player_id, game_date),
    ).fetchone()
    return row["role"] if row else None


def cohort_snapshots(conn: sqlite3.Connection, game_date: str) -> list[sqlite3.Row]:
    """같은 날짜의 전체 스쿼드 스냅샷. usage 정규화의 분모로 쓴다."""
    return conn.execute("SELECT * FROM snapshots WHERE game_date = ?", (game_date,)).fetchall()


def set_parent_club(conn: sqlite3.Connection, game_date: str, parent_club: str | None) -> None:
    """그 시점의 모구단을 기록한다. import가 판정한 값을 넣는다."""
    conn.execute(
        "INSERT INTO squad_meta (game_date, parent_club, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(game_date) DO UPDATE SET"
        " parent_club=excluded.parent_club, updated_at=excluded.updated_at",
        (game_date, parent_club, _now()),
    )


def parent_club(conn: sqlite3.Connection, game_date: str) -> str | None:
    """그 시점의 모구단. 기록이 없으면 스냅샷에서 계산해 채워 넣는다.

    이 테이블이 생기기 전에 넣은 데이터도 다시 import하지 않고 쓸 수 있도록,
    없으면 저장된 `구단` 들의 과반으로 역산한다 (import 때와 같은 규칙).

    Returns:
        모구단 이름. 과반 구단이 없으면 None.
    """
    row = conn.execute(
        "SELECT parent_club FROM squad_meta WHERE game_date = ?", (game_date,)
    ).fetchone()
    if row is not None:
        return row["parent_club"]

    found = utils.majority_club(
        r["club"] for r in conn.execute(
            "SELECT club FROM snapshots WHERE game_date = ?", (game_date,)
        )
    )
    set_parent_club(conn, game_date, found)
    conn.commit()
    return found


def departed_snapshots(conn: sqlite3.Connection, game_date: str) -> list[sqlite3.Row]:
    """그 시점 명단에서 **사라진** 선수들의 마지막 스냅샷.

    이전에는 있었는데 이 날짜에는 없는 선수다. 방출·이적·계약만료를 FM
    데이터로 구분할 수는 없고, 여기서는 "없어졌다" 는 사실만 본다.
    export를 일부만 뽑은 경우에도 똑같이 사라진 것으로 보이므로, 화면에서는
    판정 근거(그 시점 명단에 없음)를 함께 보여주는 편이 좋다.

    Returns:
        선수당 한 행. 각자의 **마지막** 스냅샷이며 날짜는 제각각이다.
    """
    return conn.execute(
        "SELECT s.* FROM snapshots s"
        " JOIN (SELECT player_id, MAX(game_date) AS d FROM snapshots"
        "        WHERE game_date < ? GROUP BY player_id) last"
        "   ON last.player_id = s.player_id AND last.d = s.game_date"
        " WHERE s.player_id NOT IN (SELECT player_id FROM snapshots WHERE game_date = ?)",
        (game_date, game_date),
    ).fetchall()


def attributes_by_player(conn: sqlite3.Connection, game_date: str) -> dict[str, dict[str, float]]:
    """해당 날짜 전체 선수의 능력치를 한 번에 읽는다.

    포지션 그룹별 순위를 매기려면 스쿼드 전원의 능력치가 필요하다.
    선수마다 따로 조회하면 N+1 질의가 되므로 한 번에 가져온다.
    """
    result: dict[str, dict[str, float]] = {}
    for row in conn.execute(
        "SELECT player_id, attribute, value FROM snapshot_attributes WHERE game_date = ?",
        (game_date,),
    ):
        result.setdefault(row["player_id"], {})[row["attribute"]] = row["value"]
    return result


def all_snapshot_keys(conn: sqlite3.Connection, game_date: str | None = None) -> list[tuple[str, str]]:
    """``(player_id, game_date)`` 전체 목록. 재계산 루프용."""
    if game_date is None:
        rows = conn.execute("SELECT player_id, game_date FROM snapshots ORDER BY game_date, player_id")
    else:
        rows = conn.execute(
            "SELECT player_id, game_date FROM snapshots WHERE game_date = ? ORDER BY player_id",
            (game_date,),
        )
    return [(row["player_id"], row["game_date"]) for row in rows]


def distinct_game_dates(conn: sqlite3.Connection) -> list[str]:
    """DB에 들어있는 모든 게임 날짜 (오름차순)."""
    return [row["game_date"] for row in conn.execute(
        "SELECT DISTINCT game_date FROM snapshots ORDER BY game_date"
    )]


def metrics_by_player(conn: sqlite3.Connection, game_date: str) -> dict[str, dict[str, Any]]:
    """해당 날짜의 모든 파생 지표를 ``{player_id: {metric: value}}`` 로 읽는다.

    선수 목록 화면처럼 전원의 지표가 필요한 곳에서 N+1 질의를 피하기 위한 것.
    """
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT player_id, metric, value_num, value_text FROM derived_metrics"
        " WHERE game_date = ?",
        (game_date,),
    ):
        value = row["value_num"] if row["value_num"] is not None else row["value_text"]
        result.setdefault(row["player_id"], {})[row["metric"]] = value
    return result


def roles_by_player(conn: sqlite3.Connection, game_date: str) -> dict[str, sqlite3.Row]:
    """해당 날짜 기준 각 선수의 가장 최근 역할 라벨."""
    rows = conn.execute(
        "SELECT r.* FROM squad_roles r"
        " JOIN (SELECT player_id, MAX(game_date) AS d FROM squad_roles"
        "        WHERE game_date <= ? GROUP BY player_id) latest"
        "   ON latest.player_id = r.player_id AND latest.d = r.game_date",
        (game_date,),
    ).fetchall()
    return {row["player_id"]: row for row in rows}


def origins_by_player(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """전체 선수의 출신 정보."""
    return {row["player_id"]: row for row in conn.execute("SELECT * FROM player_origin")}


def growth_by_player(conn: sqlite3.Connection, game_date: str) -> dict[str, list[sqlite3.Row]]:
    """해당 날짜의 성장 delta를 선수별로 묶어서 읽는다 (변화가 있는 것만)."""
    result: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        "SELECT * FROM growth_deltas WHERE game_date = ? AND delta != 0"
        " ORDER BY delta DESC, attribute",
        (game_date,),
    ):
        result.setdefault(row["player_id"], []).append(row)
    return result


def first_and_last_attributes(
    conn: sqlite3.Connection, player_id: str
) -> tuple[str | None, str | None]:
    """선수의 첫 스냅샷과 마지막 스냅샷 날짜. 통산 성장 계산에 쓴다."""
    row = conn.execute(
        "SELECT MIN(game_date) AS first, MAX(game_date) AS last FROM snapshots"
        " WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    return (row["first"], row["last"]) if row else (None, None)


def raw_value(row: sqlite3.Row, header: str) -> str | None:
    """스냅샷 행의 ``raw_json`` 에서 원본 컬럼 하나를 꺼낸다.

    정규화하지 않은 FM 필드를 임시로 들여다볼 때 쓴다.
    """
    try:
        payload = json.loads(row["raw_json"])
    except (TypeError, ValueError, KeyError):
        return None
    return payload.get(header)
