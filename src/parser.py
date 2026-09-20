"""FM24 HTML export 파서.

``pandas.read_html`` 을 쓰지 않는다. FM export는 컬럼이 300~400개이고 **같은
이름의 헤더가 여러 번 등장**하기 때문에(`구단` 2개, `최적 역할` 5개, `잠재력`
3개 …), 이름만으로 컬럼을 고르면 export 설정이 바뀔 때 조용히 엉뚱한 값이
들어간다. 그래서 이 모듈은 직접 테이블을 읽고:

1. 중복 헤더에 ``__2``, ``__3`` 접미를 붙여 **모든 컬럼을 보존**하고,
2. 정규화 필드는 :data:`src.config.FIELD_SPECS` 의 후보 헤더로 찾되,
   후보가 여러 개면 **값의 모양을 보고** 고른다(`구단` → 팀 이름 vs 1군/U18).

파싱 결과는 두 갈래로 나온다:

* ``PlayerRecord.raw``    — 384개 컬럼 전부, 원본 문자열 그대로
* ``PlayerRecord.fields`` — 정규화된 값들 (없으면 None)

표준 라이브러리만으로 동작한다. ``lxml`` 이 설치돼 있으면 자동으로 더 빠른
경로를 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config, utils

__all__ = [
    "ParsedTable",
    "PlayerRecord",
    "ParseResult",
    "dedupe_headers",
    "extract_first_table",
    "parse_export",
    "suggest_attribute_columns",
]


# ---------------------------------------------------------------------------
# 1단계: HTML에서 첫 번째 테이블 꺼내기
# ---------------------------------------------------------------------------


@dataclass
class ParsedTable:
    """HTML에서 읽어낸 표 하나.

    Attributes:
        headers: 중복 제거된 헤더. ``raw`` 딕셔너리의 키가 된다.
        original_headers: 중복 제거 전 헤더 (진단용).
        rows: 각 행의 셀 문자열 목록. 길이는 headers와 맞춰져 있다.
        duplicates: ``{헤더: 등장 횟수}`` — 2 이상인 것만.
    """

    headers: list[str]
    original_headers: list[str]
    rows: list[list[str]]
    duplicates: dict[str, int] = field(default_factory=dict)

    def column(self, index: int) -> list[str]:
        """``index`` 번째 컬럼의 전체 값을 세로로 뽑는다."""
        return [row[index] for row in self.rows]

    def __len__(self) -> int:
        return len(self.rows)


class _FirstTableParser(HTMLParser):
    """문서에서 **첫 번째** ``<table>`` 만 읽는 표준 라이브러리 파서.

    중첩 테이블에 대비해 깊이를 추적하고, 바깥 테이블이 닫히면 즉시 수집을
    멈춘다. ``<br>`` 은 줄바꿈으로 바꿔 셀 안의 여러 줄을 잃지 않는다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.header_row_index: int | None = None
        self._depth = 0
        self._done = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._row_has_th = False

    # -- 태그 -------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
                self._row_has_th = False
            elif tag in ("td", "th"):
                self._cell = []
                if tag == "th":
                    self._row_has_th = True
            elif tag == "br" and self._cell is not None:
                self._cell.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "br" and self._cell is not None and not self._done:
            self._cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag == "table":
            if self._depth == 1:
                self._flush_row()
                self._done = True
            self._depth = max(0, self._depth - 1)
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                self._row.append("".join(self._cell))
                self._cell = None
            elif tag == "tr":
                self._flush_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None and not self._done:
            self._cell.append(data)

    # -- 내부 -------------------------------------------------------------
    def _flush_row(self) -> None:
        if self._row:
            if self._row_has_th and self.header_row_index is None:
                self.header_row_index = len(self.rows)
            self.rows.append(self._row)
        self._row = None
        self._cell = None


def _read_html_text(path: Path, encoding: str | None = None) -> str:
    """인코딩 후보를 순서대로 시도하며 HTML을 텍스트로 읽는다.

    Args:
        path: HTML 파일 경로.
        encoding: 강제할 인코딩. None이면 :data:`config.ENCODING_CANDIDATES` 순회.

    Raises:
        UnicodeDecodeError: 모든 후보가 실패했을 때.
    """
    data = path.read_bytes()
    candidates = (encoding,) if encoding else config.ENCODING_CANDIDATES
    last_error: UnicodeDecodeError | None = None
    for candidate in candidates:
        try:
            return data.decode(candidate)
        except UnicodeDecodeError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _extract_rows_lxml(text: str) -> tuple[list[list[str]], int] | None:
    """lxml이 있으면 그걸로 첫 테이블을 읽는다. 없으면 None.

    Returns:
        ``(행 목록, 헤더 행 인덱스)``. 테이블이나 행이 없으면 None.
    """
    try:
        from lxml import html as lxml_html  # type: ignore[import-not-found]
    except ImportError:
        return None
    tree = lxml_html.fromstring(text)
    tables = tree.xpath("//table")
    if not tables:
        return None
    table = tables[0]
    for br in table.xpath(".//br"):
        br.tail = "\n" + (br.tail or "")

    rows: list[list[str]] = []
    header_index: int | None = None
    for tr in table.xpath(".//tr"):
        cells = tr.xpath("./td | ./th")
        if not cells:
            continue
        if header_index is None and any(cell.tag == "th" for cell in cells):
            header_index = len(rows)
        rows.append([cell.text_content() for cell in cells])
    if not rows:
        return None
    return rows, header_index or 0


def dedupe_headers(headers: Sequence[str]) -> tuple[list[str], dict[str, int]]:
    """중복 헤더에 ``__2``, ``__3`` … 접미를 붙여 고유하게 만든다.

    첫 번째 등장은 이름을 그대로 유지하므로, 중복이 없는 컬럼의 키는
    export 판이 바뀌어도 안정적이다.

    Args:
        headers: 원본 헤더 목록.

    Returns:
        ``(고유 헤더 목록, {헤더: 등장 횟수})``. 두 번째 값은 2 이상인 것만 담는다.

    Example:
        >>> dedupe_headers(["구단", "나이", "구단"])
        (['구단', '나이', '구단__2'], {'구단': 2})
    """
    counts: dict[str, int] = {}
    unique: list[str] = []
    for raw_header in headers:
        name = utils.clean_text(raw_header) or "unnamed"
        counts[name] = counts.get(name, 0) + 1
        unique.append(name if counts[name] == 1 else f"{name}__{counts[name]}")
    duplicates = {name: count for name, count in counts.items() if count > 1}
    return unique, duplicates


def extract_first_table(path: str | Path, encoding: str | None = None) -> ParsedTable:
    """HTML 파일의 첫 번째 테이블을 :class:`ParsedTable` 로 읽는다.

    Args:
        path: FM24가 export한 HTML 경로.
        encoding: 강제 인코딩 (기본: 자동 판별).

    Returns:
        헤더가 중복 제거된 표.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: 테이블이나 데이터 행이 없을 때.
    """
    path = Path(path)
    text = _read_html_text(path, encoding)

    extracted = _extract_rows_lxml(text)
    if extracted is not None:
        rows, header_index = extracted
    else:
        stdlib_parser = _FirstTableParser()
        stdlib_parser.feed(text)
        stdlib_parser.close()
        rows = stdlib_parser.rows
        header_index = stdlib_parser.header_row_index or 0

    if not rows:
        raise ValueError(f"HTML에서 테이블을 찾지 못했습니다: {path}")

    original_headers = [utils.clean_text(cell) for cell in rows[header_index]]
    headers, duplicates = dedupe_headers(original_headers)
    width = len(headers)

    data_rows: list[list[str]] = []
    for row in rows[header_index + 1 :]:
        cells = [utils.clean_text(cell) for cell in row]
        if len(cells) < width:  # 짧은 행은 빈칸으로 채운다
            cells.extend([""] * (width - len(cells)))
        elif len(cells) > width:  # 긴 행은 잘라낸다 (헤더가 기준)
            cells = cells[:width]
        if any(cells):
            data_rows.append(cells)

    if not data_rows:
        raise ValueError(f"테이블에 데이터 행이 없습니다: {path}")

    return ParsedTable(
        headers=headers,
        original_headers=original_headers,
        rows=data_rows,
        duplicates=duplicates,
    )


# ---------------------------------------------------------------------------
# 2단계: 컬럼 찾기 (중복 헤더 해소 포함)
# ---------------------------------------------------------------------------


def _candidate_indices(table: ParsedTable, header: str) -> list[int]:
    """원본 헤더 이름이 ``header`` 와 정확히 일치하는 컬럼 인덱스들."""
    return [i for i, name in enumerate(table.original_headers) if name == header]


def resolve_column(
    table: ParsedTable,
    headers: Sequence[str],
    disambiguator: str | None,
    warnings: list[str] | None = None,
    label: str = "",
) -> int | None:
    """후보 헤더 목록으로 컬럼 하나를 고른다.

    후보 헤더는 앞에 있을수록 우선한다. 같은 이름이 여러 번 등장하면
    ``disambiguator`` 로 각 컬럼의 값을 채점해 가장 높은 것을 고른다.
    판별기가 지정됐는데 어느 컬럼도 점수를 못 얻으면 **아무것도 고르지 않는다**
    (틀린 값을 넣느니 NULL이 낫다).

    Args:
        table: 대상 표.
        headers: 찾을 헤더 후보들.
        disambiguator: :data:`config.DISAMBIGUATORS` 의 키. None이면 첫 컬럼.
        warnings: 경고를 모을 리스트 (선택).
        label: 경고 메시지에 쓸 필드 이름.

    Returns:
        컬럼 인덱스. 못 찾으면 None.
    """
    for header in headers:
        indices = _candidate_indices(table, header)
        if not indices:
            continue
        if len(indices) == 1 and disambiguator is None:
            return indices[0]

        if disambiguator is None:
            if warnings is not None:
                warnings.append(
                    f"'{header}' 컬럼이 {len(indices)}개 있는데 판별 기준이 없어 "
                    f"첫 번째를 사용합니다 (필드: {label or header})"
                )
            return indices[0]

        scorer = config.DISAMBIGUATORS.get(disambiguator)
        if scorer is None:
            if warnings is not None:
                warnings.append(f"알 수 없는 판별기 '{disambiguator}' (필드: {label})")
            return indices[0]

        scored = [(scorer(table.column(i)), i) for i in indices]
        best_score, best_index = max(scored, key=lambda pair: pair[0])
        if best_score <= 0.0:
            return None
        return best_index
    return None


# ---------------------------------------------------------------------------
# 3단계: 값 변환
# ---------------------------------------------------------------------------


def _convert(kind: str, name: str, raw: str) -> dict[str, Any]:
    """``kind`` 에 따라 셀 문자열을 컬럼 딕셔너리로 바꾼다.

    여러 컬럼으로 퍼지는 kind(money, appearances 등)는 ``config.KIND_COLUMNS``
    의 접미 규칙과 짝이 맞아야 한다.
    """
    if kind == "text":
        text = utils.clean_text(raw)
        return {name: None if utils.is_missing(text) else text}
    if kind == "int":
        return {name: utils.parse_int(raw)}
    if kind == "float":
        return {name: utils.parse_float(raw)}
    if kind == "percent":
        return {name: utils.parse_percent(raw)}
    if kind == "length_cm":
        return {name: utils.parse_length_cm(raw)}
    if kind == "mass_kg":
        return {name: utils.parse_mass_kg(raw)}
    if kind == "distance_km":
        return {name: utils.parse_distance_km(raw)}
    if kind == "date":
        return {name: utils.date_to_iso(utils.parse_date(raw))}
    if kind == "birth":
        born, age = utils.parse_birth_date(raw)
        return {name: utils.date_to_iso(born), f"{name}_age_label": age}
    if kind == "appearances":
        parsed = utils.parse_appearances(raw)
        if parsed is None:
            return {f"{name}_raw": None, f"{name}_starts": None, f"{name}_subs": None}
        return {
            f"{name}_raw": parsed.raw,
            f"{name}_starts": parsed.starts,
            f"{name}_subs": parsed.subs,
        }
    if kind == "money":
        money = utils.parse_money(raw)
        if money is None:
            return {f"{name}_raw": None, f"{name}_low": None, f"{name}_high": None, name: None}
        return {
            f"{name}_raw": money.raw,
            f"{name}_low": money.low,
            f"{name}_high": money.high,
            name: money.value,
        }
    if kind == "wage":
        wage = utils.parse_wage(raw)
        if wage is None:
            return {f"{name}_raw": None, f"{name}_amount": None, f"{name}_period": None}
        return {f"{name}_raw": wage.raw, f"{name}_amount": wage.amount, f"{name}_period": wage.period}
    # 모르는 kind는 원본 문자열로 보존한다.
    return {name: utils.clean_text(raw) or None}


# ---------------------------------------------------------------------------
# 4단계: 선수 레코드 만들기
# ---------------------------------------------------------------------------


@dataclass
class PlayerRecord:
    """한 선수의 한 시점 스냅샷.

    Attributes:
        player_id: export의 ID 컬럼 값. 없으면 이름 기반 fallback.
        id_source: ``"export"`` 또는 ``"fallback"``.
        fields: 정규화된 컬럼 딕셔너리 (snapshots 테이블 컬럼과 1:1).
        attributes: ``{canonical 능력치 키: 값}``. 1~20 범위만 담긴다.
        attribute_groups: ``{canonical 키: 그룹명}``.
        raw: 중복 제거된 헤더 전부 → 원본 문자열.
    """

    player_id: str
    id_source: str
    fields: dict[str, Any]
    attributes: dict[str, float]
    attribute_groups: dict[str, str]
    raw: dict[str, str]

    @property
    def name(self) -> str:
        """표시용 이름. 없으면 player_id를 쓴다."""
        return self.fields.get("name") or self.player_id


@dataclass
class ParseResult:
    """:func:`parse_export` 의 결과.

    Attributes:
        players: 파싱된 선수 레코드.
        table: 원본 표 (진단/탐색용).
        warnings: 사람이 읽어야 할 경고 메시지.
        resolved_fields: ``{필드명: 사용한 원본 컬럼 인덱스}``.
        unmapped_headers: 정규화 필드/능력치 어디에도 안 붙은 헤더.
    """

    players: list[PlayerRecord]
    table: ParsedTable
    warnings: list[str] = field(default_factory=list)
    resolved_fields: dict[str, int] = field(default_factory=dict)
    unmapped_headers: list[str] = field(default_factory=list)


def parse_export(path: str | Path, encoding: str | None = None) -> ParseResult:
    """FM24 HTML export를 선수 레코드 목록으로 파싱한다.

    Args:
        path: HTML 경로.
        encoding: 강제 인코딩 (기본: 자동).

    Returns:
        :class:`ParseResult`.

    Raises:
        FileNotFoundError, ValueError: 테이블을 못 읽었을 때.
    """
    table = extract_first_table(path, encoding)
    warnings: list[str] = []

    if table.duplicates:
        summary = ", ".join(f"{name}×{count}" for name, count in sorted(table.duplicates.items()))
        warnings.append(f"중복 헤더 {len(table.duplicates)}종 발견: {summary}")

    # --- 정규화 필드 컬럼 찾기 -------------------------------------------
    resolved: dict[str, int] = {}
    for spec in config.FIELD_SPECS:
        index = resolve_column(table, spec.headers, spec.disambiguator, warnings, spec.name)
        if index is not None:
            resolved[spec.name] = index

    # --- 능력치 컬럼 찾기 -------------------------------------------------
    attribute_columns: dict[str, tuple[int, str]] = {}  # key -> (index, group)
    for header, (key, group) in config.attribute_lookup().items():
        index = resolve_column(table, (header,), "attribute_like", warnings, f"attr:{key}")
        if index is not None:
            attribute_columns[key] = (index, group)

    missing_attrs = set(config.attribute_keys()) - set(attribute_columns)
    if missing_attrs:
        warnings.append(
            f"능력치 컬럼 {len(missing_attrs)}개를 찾지 못했습니다: "
            f"{', '.join(sorted(missing_attrs))} (config.ATTRIBUTE_GROUPS 확인)"
        )

    # --- 선수 ID 컬럼 -----------------------------------------------------
    id_index = resolve_column(table, config.PLAYER_ID_HEADERS, "non_empty", warnings, "player_id")
    if id_index is None:
        warnings.append(
            "⚠ ID 컬럼을 찾지 못했습니다. 이름+생일+국적 해시를 임시 ID로 씁니다. "
            "이름이 바뀌면 다른 선수로 잡히므로, FM export 설정에 'ID' 컬럼을 추가하세요."
        )

    # --- 행 → 레코드 ------------------------------------------------------
    players: list[PlayerRecord] = []
    seen_ids: dict[str, str] = {}
    for row in table.rows:
        raw = dict(zip(table.headers, row))

        fields: dict[str, Any] = {}
        for spec in config.FIELD_SPECS:
            index = resolved.get(spec.name)
            cell = row[index] if index is not None else ""
            fields.update(_convert(spec.kind, spec.name, cell))

        attributes: dict[str, float] = {}
        groups: dict[str, str] = {}
        low, high = config.ATTRIBUTE_VALUE_RANGE
        for key, (index, group) in attribute_columns.items():
            value = utils.parse_int(row[index])
            if value is not None and low <= value <= high:
                attributes[key] = float(value)
                groups[key] = group

        if id_index is not None and not utils.is_missing(row[id_index]):
            player_id = utils.clean_text(row[id_index])
            id_source = "export"
        else:
            player_id = utils.fallback_player_id(
                [str(fields.get(name) or "") for name in config.FALLBACK_KEY_FIELDS]
            )
            id_source = "fallback"

        display_name = fields.get("name") or player_id
        if player_id in seen_ids:
            warnings.append(
                f"중복 player_id {player_id}: '{seen_ids[player_id]}' 와 '{display_name}'. "
                "뒤에 나온 행이 앞의 행을 덮어씁니다."
            )
        seen_ids[player_id] = display_name

        players.append(
            PlayerRecord(
                player_id=player_id,
                id_source=id_source,
                fields=fields,
                attributes=attributes,
                attribute_groups=groups,
                raw=raw,
            )
        )

    used_indices = set(resolved.values()) | {index for index, _ in attribute_columns.values()}
    if id_index is not None:
        used_indices.add(id_index)
    unmapped = [table.headers[i] for i in range(len(table.headers)) if i not in used_indices]

    return ParseResult(
        players=players,
        table=table,
        warnings=warnings,
        resolved_fields=resolved,
        unmapped_headers=unmapped,
    )


# ---------------------------------------------------------------------------
# 탐색 도구
# ---------------------------------------------------------------------------


def suggest_attribute_columns(table: ParsedTable, threshold: float = 0.9) -> list[tuple[str, float]]:
    """능력치일 가능성이 높은데 :data:`config.ATTRIBUTE_GROUPS` 에 없는 컬럼을 찾는다.

    값이 대부분 1~20 정수인 컬럼을 후보로 본다. 출전 수처럼 우연히 범위에
    들어오는 컬럼도 섞이므로, **사람이 보고 판단해서** config에 추가할 것.

    Args:
        table: 대상 표.
        threshold: 1~20 정수 비율의 하한.

    Returns:
        ``(헤더, 비율)`` 목록. 비율 내림차순.
    """
    known = set(config.attribute_lookup())
    scorer = config.DISAMBIGUATORS["attribute_like"]
    suggestions: list[tuple[str, float]] = []
    for index, header in enumerate(table.original_headers):
        if header in known:
            continue
        score = scorer(table.column(index))
        if score >= threshold:
            suggestions.append((table.headers[index], round(score, 3)))
    return sorted(suggestions, key=lambda pair: pair[1], reverse=True)


def iter_raw_values(players: Iterable[PlayerRecord], header: str) -> list[str]:
    """여러 레코드에서 특정 raw 헤더의 값만 뽑는다 (탐색용)."""
    return [player.raw.get(header, "") for player in players]
