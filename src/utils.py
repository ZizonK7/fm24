"""FM24 export 셀 문자열을 파이썬 값으로 바꾸는 순수 함수 모음.

모든 파서는 **실패해도 예외를 던지지 않고 ``None`` 을 돌려준다.** FM export는
컬럼 수백 개에 표기가 제각각이라, 한 셀 때문에 import 전체가 죽으면 안 된다.
해석에 실패한 값은 호출부에서 raw 문자열 그대로 보존된다.

지원하는 표기 예시::

    "원1,800억"            → 180_000_000_000
    "원290억 - 원440억"    → (29_000_000_000, 44_000_000_000)
    "연봉 원377억"         → Wage(amount=37_700_000_000, period="annual")
    "27 (4)"               → Appearances(starts=27, subs=4)
    "2001년/12월/12일 (25세)" → (date(2001, 12, 12), 25)
    "86%"                  → 86.0
    "184 cm"               → 184.0
    "370.2km"              → 370.2
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sys
import unicodedata as _unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Any, Iterable, NamedTuple, Sequence

from . import config

__all__ = [
    "Appearances",
    "Money",
    "Wage",
    "clean_text",
    "display_width",
    "enable_utf8_stdout",
    "pad",
    "fallback_player_id",
    "infer_date_from_filename",
    "is_missing",
    "majority_club",
    "parse_appearances",
    "parse_birth_date",
    "parse_date",
    "parse_distance_km",
    "parse_float",
    "parse_game_date",
    "parse_int",
    "parse_length_cm",
    "parse_mass_kg",
    "parse_money",
    "parse_percent",
    "parse_position_tokens",
    "parse_wage",
    "position_groups",
    "safe_mean",
    "safe_stdev",
]


# ---------------------------------------------------------------------------
# 텍스트 정리
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    """셀 원본 문자열을 비교/저장에 쓸 수 있게 정리한다.

    * FM 인라인 마크업 ``|c:disabled|형제|/c|`` → ``(형제)``
    * NBSP를 보통 공백으로
    * 연속 공백/줄바꿈을 공백 하나로 축약 후 strip

    Args:
        value: 임의의 셀 값. ``None`` 이면 빈 문자열.

    Returns:
        정리된 문자열.
    """
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").replace("​", "")
    text = config.FM_INLINE_MARKUP.sub(lambda m: f"({m.group(1).strip()})", text)
    # 짝이 맞지 않는 잔여 마크업 제거
    text = re.sub(r"\|/?c(?::[^|]*)?\|", " ", text)
    return _WHITESPACE.sub(" ", text).strip()


def is_missing(value: Any) -> bool:
    """FM이 '값 없음'으로 내보낸 셀인지 판정한다."""
    return clean_text(value) in config.MISSING_TOKENS


def _prepare(value: Any) -> str | None:
    """공통 전처리: 정리 후 결측이면 None."""
    text = clean_text(value)
    return None if text in config.MISSING_TOKENS else text


# ---------------------------------------------------------------------------
# 숫자
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?")


def parse_float(value: Any) -> float | None:
    """문자열에서 첫 번째 숫자를 float으로 뽑는다. 천 단위 콤마를 허용한다."""
    text = _prepare(value)
    if text is None:
        return None
    match = _NUMBER.search(text)
    if match is None:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    """문자열에서 첫 번째 정수를 뽑는다. ``"12.7"`` 은 12로 내림하지 않고 None."""
    number = parse_float(value)
    if number is None:
        return None
    if number != int(number):
        return None
    return int(number)


def parse_percent(value: Any) -> float | None:
    """``"86%"`` → ``86.0``. 0~100 스케일을 유지한다(0~1 아님).

    ``%`` 기호가 없는 순수 숫자도 그대로 받아들인다.
    """
    text = _prepare(value)
    if text is None:
        return None
    return parse_float(text.replace("%", ""))


def parse_length_cm(value: Any) -> float | None:
    """``"184 cm"`` → ``184.0``. ``m`` 단위로 오면 cm로 환산한다."""
    text = _prepare(value)
    if text is None:
        return None
    number = parse_float(text)
    if number is None:
        return None
    lowered = text.lower()
    if "cm" not in lowered and re.search(r"\bm\b", lowered) and number < 3:
        return number * 100
    return number


def parse_mass_kg(value: Any) -> float | None:
    """``"71 kg"`` → ``71.0``. ``lbs`` 로 오면 kg로 환산한다."""
    text = _prepare(value)
    if text is None:
        return None
    number = parse_float(text)
    if number is None:
        return None
    if "lb" in text.lower():
        return round(number * 0.45359237, 2)
    return number


def parse_distance_km(value: Any) -> float | None:
    """``"370.2km"`` → ``370.2``. ``m`` 단위면 km로 환산한다."""
    text = _prepare(value)
    if text is None:
        return None
    number = parse_float(text)
    if number is None:
        return None
    lowered = text.lower()
    if "km" not in lowered and re.search(r"\d\s*m\b", lowered):
        return number / 1000
    return number


# ---------------------------------------------------------------------------
# 출전 기록
# ---------------------------------------------------------------------------


class Appearances(NamedTuple):
    """``"27 (4)"`` 를 분해한 결과.

    Attributes:
        raw: 원본 문자열.
        starts: 선발 출전 수.
        subs: 교체 출전 수. 괄호가 없으면 0.
    """

    raw: str
    starts: int | None
    subs: int


_APPEARANCES = re.compile(r"^(\d+)\s*(?:\(\s*(\d+)\s*\))?$")


def parse_appearances(value: Any) -> Appearances | None:
    """FM 출전 수 표기를 (선발, 교체)로 분해한다.

    ``"27 (4)"`` → starts 27 / subs 4, ``"35"`` → starts 35 / subs 0.
    형식이 다르면 ``starts=None`` 이고 raw만 채워진 결과를 돌려준다.
    """
    text = _prepare(value)
    if text is None:
        return None
    match = _APPEARANCES.match(text)
    if match is None:
        return Appearances(raw=text, starts=None, subs=0)
    starts = int(match.group(1))
    subs = int(match.group(2)) if match.group(2) else 0
    return Appearances(raw=text, starts=starts, subs=subs)


# ---------------------------------------------------------------------------
# 금액
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Money:
    """한국어 금액 표기의 파싱 결과.

    단일 금액이면 ``low == high == value``. 범위 표기면 ``value`` 는 중간값이다.

    Attributes:
        raw: 원본 문자열.
        low: 범위 하한(원).
        high: 범위 상한(원).
        value: 대표값(원). 단일값이거나 범위의 중간값.
    """

    raw: str
    low: float | None
    high: float | None
    value: float | None


_MONEY_TOKEN = re.compile(r"([\d,]+(?:\.\d+)?)\s*([조억만천])?")
_RANGE_SPLIT = re.compile(r"\s+[-~–—]\s+")


def _parse_money_amount(text: str) -> float | None:
    """``"원1조 2,000억"`` 같은 단일 금액을 원 단위 숫자로 바꾼다."""
    total = 0.0
    found = False
    for number_text, unit in _MONEY_TOKEN.findall(text):
        try:
            number = float(number_text.replace(",", ""))
        except ValueError:
            continue
        total += number * config.MONEY_UNITS.get(unit, 1)
        found = True
    return total if found else None


def parse_money(value: Any) -> Money | None:
    """몸값/이적료 표기를 파싱한다.

    ``"원1,800억"`` 은 단일값, ``"원290억 - 원440억"`` 은 범위로 해석한다.
    숫자를 하나도 못 찾으면 ``low/high/value`` 가 모두 ``None`` 인 Money를
    돌려준다(원본은 남는다).
    """
    text = _prepare(value)
    if text is None:
        return None
    parts = _RANGE_SPLIT.split(text)
    amounts = [amount for amount in (_parse_money_amount(part) for part in parts) if amount is not None]
    if not amounts:
        return Money(raw=text, low=None, high=None, value=None)
    if len(amounts) == 1:
        only = amounts[0]
        return Money(raw=text, low=only, high=only, value=only)
    low, high = min(amounts), max(amounts)
    return Money(raw=text, low=low, high=high, value=(low + high) / 2)


@dataclass(frozen=True)
class Wage:
    """급료 표기의 파싱 결과.

    Attributes:
        raw: 원본 문자열.
        amount: 금액(원).
        period: ``"annual"`` / ``"weekly"`` / ``"monthly"`` 등. 표기가 없으면 None.
    """

    raw: str
    amount: float | None
    period: str | None


def parse_wage(value: Any) -> Wage | None:
    """``"연봉 원377억"`` → ``Wage(amount=3.77e10, period="annual")``."""
    text = _prepare(value)
    if text is None:
        return None
    period: str | None = None
    for label, key in config.WAGE_PERIODS.items():
        if text.startswith(label):
            period = key
            text_without_label = text[len(label) :].strip()
            break
    else:
        text_without_label = text
    return Wage(raw=text, amount=_parse_money_amount(text_without_label), period=period)


# ---------------------------------------------------------------------------
# 날짜
# ---------------------------------------------------------------------------

_KOREAN_DATE = re.compile(r"(\d{4})\s*년\s*/?\s*(\d{1,2})\s*월\s*/?\s*(\d{1,2})\s*일")
_ISO_DATE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_AGE_SUFFIX = re.compile(r"\(\s*(\d{1,2})\s*세\s*\)")


def parse_date(value: Any) -> _dt.date | None:
    """``"2025년/6월/20일"`` 또는 ``"2025-06-20"`` 을 :class:`datetime.date` 로."""
    text = _prepare(value)
    if text is None:
        return None
    for pattern in (_KOREAN_DATE, _ISO_DATE):
        match = pattern.search(text)
        if match:
            try:
                return _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            except ValueError:
                return None
    return None


def parse_birth_date(value: Any) -> tuple[_dt.date | None, int | None]:
    """``"2001년/12월/12일 (25세)"`` → ``(date(2001,12,12), 25)``.

    괄호 안 나이는 export 시점의 FM 내부 나이다. 스냅샷 날짜와 함께
    저장해두면 나중에 검증용으로 쓸 수 있다.
    """
    text = _prepare(value)
    if text is None:
        return None, None
    age_match = _AGE_SUFFIX.search(text)
    age = int(age_match.group(1)) if age_match else None
    return parse_date(text), age


def parse_game_date(value: str) -> _dt.date:
    """CLI로 받은 게임 내 날짜를 파싱한다. 실패하면 :class:`ValueError`.

    여기서만은 예외를 던진다. 날짜는 스냅샷의 키라서, 조용히 틀린 값이
    들어가면 누적 데이터 전체가 망가지기 때문이다.
    """
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(
            f"게임 날짜를 해석할 수 없습니다: {value!r} "
            "(예: 2027-03-01 또는 2027년/3월/1일)"
        )
    return parsed


#: 파일명에서 날짜를 뽑을 때 쓰는 패턴. 앞에 있을수록 먼저 시도한다.
_FILENAME_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<!\d)(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"), "YYYYMMDD"),
    (re.compile(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"), "YYMMDD"),
)


def infer_date_from_filename(name: str, century: int = 2000) -> _dt.date | None:
    """파일 이름에서 게임 내 날짜를 추측한다.

    ``270424.html`` → 2027-04-24, ``2027-04-24.html`` → 2027-04-24.
    세이브 날짜를 파일명으로 쓰는 습관을 그대로 받아쓰기 위한 것이라,
    **추측일 뿐이고 반드시 사람이 확인해야 한다.**

    Args:
        name: 파일명 또는 경로.
        century: 두 자리 연도에 더할 기준 연도.

    Returns:
        추측한 날짜. 확신이 없으면 None.

    Example:
        >>> infer_date_from_filename("270424.html")
        datetime.date(2027, 4, 24)
        >>> infer_date_from_filename("players.html") is None
        True
    """
    stem = _Path(name).stem
    for pattern, kind in _FILENAME_DATE_PATTERNS:
        match = pattern.search(stem)
        if match is None:
            continue
        year, month, day = (int(part) for part in match.groups())
        if kind == "YYMMDD":
            year += century
        try:
            return _dt.date(year, month, day)
        except ValueError:
            continue  # 13월 같은 값 — 다음 패턴을 시도한다
    return None


def date_to_iso(value: _dt.date | None) -> str | None:
    """``date`` 를 ``YYYY-MM-DD`` 문자열로. SQLite에는 이 형식으로 저장한다."""
    return value.isoformat() if value is not None else None


def days_between(earlier: str | None, later: str | None) -> int | None:
    """ISO 날짜 문자열 두 개 사이의 일수. 하나라도 없으면 None."""
    start, end = parse_date(earlier), parse_date(later)
    if start is None or end is None:
        return None
    return (end - start).days


# ---------------------------------------------------------------------------
# 기타
# ---------------------------------------------------------------------------


_POSITION_CHUNK = re.compile(r"^([A-Z/]+)(?:\s*\(\s*([RLC]+)\s*\))?$")


def parse_position_tokens(text: Any) -> set[str]:
    """FM 포지션 문자열을 개별 포지션 토큰으로 쪼갠다.

    ``/`` 로 묶인 기본 포지션과 괄호 안의 측면 문자를 모두 전개한다.

    Args:
        text: ``"DM, M (C), AM (LC)"`` 같은 FM 포지션 표기.

    Returns:
        ``{"DM", "M(C)", "AM(L)", "AM(C)"}`` 형태의 토큰 집합.
        해석할 수 없는 조각은 조용히 버린다.

    Example:
        >>> sorted(parse_position_tokens("M/AM (RC)"))
        ['AM(C)', 'AM(R)', 'M(C)', 'M(R)']
        >>> sorted(parse_position_tokens("GK"))
        ['GK']
    """
    cleaned = clean_text(text)
    if not cleaned or cleaned in config.MISSING_TOKENS:
        return set()

    tokens: set[str] = set()
    for chunk in cleaned.split(","):
        match = _POSITION_CHUNK.match(chunk.strip())
        if match is None:
            continue
        bases, sides = match.group(1).split("/"), match.group(2)
        for base in bases:
            if not base:
                continue
            if sides:
                tokens.update(f"{base}({side})" for side in sides)
            else:
                tokens.add(base)
    return tokens


def position_groups(text: Any) -> list[str]:
    """포지션 문자열이 속하는 비교 그룹들 (CB, DM, W …).

    :data:`src.config.POSITION_GROUPS` 에 없는 토큰은 무시한다.

    Returns:
        중복 없는 그룹 이름 목록. 알 수 없으면 빈 목록.
    """
    groups: list[str] = []
    for token in sorted(parse_position_tokens(text)):
        group = config.POSITION_GROUPS.get(token)
        if group is not None and group not in groups:
            groups.append(group)
    return groups


def fallback_player_id(parts: Sequence[str]) -> str:
    """ID 컬럼이 없을 때 쓰는 대체 식별자를 만든다.

    이름+생일+국적 해시라서 **이름이 바뀌면 다른 선수로 잡힌다.** 임시방편일
    뿐이므로 호출부에서 반드시 경고를 출력해야 한다.

    Args:
        parts: 해시에 넣을 문자열들 (순서가 의미를 가진다).

    Returns:
        ``fb-`` 접두가 붙은 12자리 식별자.
    """
    joined = "|".join(clean_text(part) for part in parts)
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]
    return f"fb-{digest}"


def majority_club(names: Iterable[str]) -> str | None:
    """구단 이름 목록에서 **과반을 차지하는** 이름을 고른다.

    export 한 장의 모(母)구단을 찾는 데 쓴다. 임대 나간 선수는 `구단` 이
    임대처로 찍히지만 소수이므로, 과반인 이름이 우리 팀이다.

    과반을 요구하는 이유: 스카우트 결과처럼 구단이 제각각인 export를
    "우리 스쿼드"로 오인하지 않기 위해서다.

    Args:
        names: 구단 이름들. 빈 문자열과 None은 무시한다.

    Returns:
        과반 구단 이름. 과반이 없으면 None.
    """
    counter = Counter(cleaned for name in names if (cleaned := clean_text(name)))
    if not counter:
        return None
    club, count = counter.most_common(1)[0]
    return club if count * 2 > sum(counter.values()) else None


def safe_mean(values: Iterable[float]) -> float | None:
    """빈 목록이면 None을 돌려주는 평균."""
    items = [v for v in values if v is not None]
    return sum(items) / len(items) if items else None


def safe_stdev(values: Iterable[float]) -> float | None:
    """표본이 2개 미만이면 None을 돌려주는 표준편차(모집단 기준)."""
    items = [v for v in values if v is not None]
    if len(items) < 2:
        return None
    mean = sum(items) / len(items)
    variance = sum((v - mean) ** 2 for v in items) / len(items)
    return variance**0.5


def display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글/한자는 두 칸으로 센다.

    ``len()`` 으로 표를 정렬하면 한국어 값이 섞였을 때 열이 어긋난다.
    """
    return sum(2 if _unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int, align: str = "left") -> str:
    """표시 폭 기준으로 문자열을 채우거나 자른다.

    Args:
        text: 대상 문자열.
        width: 목표 표시 폭.
        align: ``"left"`` 또는 ``"right"``.

    Returns:
        표시 폭이 정확히 ``width`` 인 문자열. 넘치면 ``…`` 로 줄인다.
    """
    current = display_width(text)
    if current > width:
        clipped = ""
        used = 0
        for ch in text:
            step = 2 if _unicodedata.east_asian_width(ch) in "WF" else 1
            if used + step > width - 1:
                break
            clipped += ch
            used += step
        text = clipped + "…"
        current = used + 1
    filler = " " * (width - current)
    return text + filler if align == "left" else filler + text


def enable_utf8_stdout() -> None:
    """Windows 콘솔에서 한글이 깨지지 않도록 stdout/stderr를 UTF-8로 바꾼다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # 리다이렉트된 스트림 등
                pass
