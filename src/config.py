"""FM24 트래커의 모든 "바뀔 수 있는 것"을 모아둔 설정 모듈.

이 파일만 고치면 다음이 전부 따라온다:

* 어떤 한국어 컬럼을 어떤 정규화 필드로 볼 것인지 (:data:`FIELD_SPECS`)
* snapshots 테이블의 컬럼 구성 (FIELD_SPECS에서 DDL을 자동 생성한다)
* 능력치를 technical / mental / physical / goalkeeping 중 무엇으로 묶을지
  (:data:`ATTRIBUTE_GROUPS`)
* 결측값으로 볼 토큰 (:data:`MISSING_TOKENS`)

설계 원칙
---------
1. FM export 컬럼은 판(版)마다 이름이 바뀌거나 추가된다. 따라서 각 필드는
   후보 헤더를 **여러 개** 가질 수 있고, 하나도 못 찾으면 조용히 NULL이 된다
   (예외를 던지지 않는다).
2. 같은 이름의 컬럼이 여러 개 존재한다(`구단`, `시작`, `최적 역할` …).
   위치(index)로 고르면 export 설정이 바뀔 때 조용히 틀린 값이 들어가므로,
   **값의 모양을 보고** 고른다(:data:`DISAMBIGUATORS`).
3. 정규화하지 못한 컬럼도 전부 ``snapshots.raw_json`` 에 원본 문자열로 남는다.
   해석이 애매한 FM 필드는 억지로 파싱하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Final, Mapping, Sequence

# ---------------------------------------------------------------------------
# 일반 상수
# ---------------------------------------------------------------------------

#: 결측으로 취급할 셀 문자열. FM은 빈칸/"-"/"--"를 섞어서 내보낸다.
#: 주의: "없음", "미설정" 등은 '정보 없음'이 아니라 실제 의미가 있는 값이므로
#: 여기에 넣지 않는다.
MISSING_TOKENS: Final[frozenset[str]] = frozenset(
    {"", "-", "--", "---", "—", "–", "N/A", "n/a", "NA", "?", "미정"}
)

#: FM이 셀 안에 넣는 인라인 마크업. 예) ``Richard Olise |c:disabled|형제|/c|``
FM_INLINE_MARKUP: Final[re.Pattern[str]] = re.compile(r"\|c:[^|]*\|(.*?)\|/c\|")

#: 능력치로 인정할 값의 범위 (FM 능력치는 1~20).
ATTRIBUTE_VALUE_RANGE: Final[tuple[int, int]] = (1, 20)

#: HTML 읽을 때 순서대로 시도할 인코딩.
ENCODING_CANDIDATES: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1")

#: `구단` 컬럼이 두 개일 때, '소속 팀 레벨' 쪽을 식별하기 위한 값 집합.
SQUAD_LEVEL_VALUES: Final[frozenset[str]] = frozenset(
    {"1군", "2군", "B팀", "리저브", "예비", "유스", "U23", "U21", "U19", "U18", "U16"}
)

#: 운영상의 역할 라벨.
SQUAD_ROLES: Final[tuple[str, ...]] = ("starter", "rotation", "development", "fringe")

#: FM의 `실제 출전 시간` 컬럼 → 운영 역할.
#:
#: FM이 이미 스쿼드 내 지위를 판정해두고 있으므로 손으로 입력할 필요가 없다.
#: import할 때 자동으로 적용되며, **사람이 직접 넣은 라벨이 있으면 덮어쓰지
#: 않는다** (:func:`src.importer.apply_roles` 의 source가 manual/csv인 경우).
#:
#: 이 매핑이 마음에 안 들면 여기만 고치고 ``scripts/recompute.py`` 대신
#: ``update.py`` 를 다시 돌리면 된다.
PLAYING_TIME_ROLE_MAP: Final[Mapping[str, str]] = {
    "주전 선수": "starter",
    "중요 선수": "starter",
    "핵심 선수": "starter",
    "비주전 선수": "rotation",
    "뛰어난 후보": "rotation",
    "선수단 선수": "rotation",
    "후보": "rotation",
    "어린 선수": "development",
    "미래의 유망주": "development",
    "눈부신 유망주": "development",
    "비상 후보": "fringe",
    "잉여 자원": "fringe",
}

#: 선수 출신 구분. "내가 데려온 선수" 를 가려내기 위한 것.
PLAYER_ORIGINS: Final[tuple[str, ...]] = ("signed", "youth", "unknown")

#: `최적 역할` 컬럼이 여러 개일 때, 역할명(전진형 플레이메이커)과
#: 임무(지원/공격/수비)를 구분하기 위한 임무 값 집합.
DUTY_VALUES: Final[frozenset[str]] = frozenset(
    {"지원", "공격", "수비", "자동", "정지", "협력"}
)

#: 한국어 금액 단위.
MONEY_UNITS: Final[Mapping[str, int]] = {
    "조": 10**12,
    "억": 10**8,
    "만": 10**4,
    "천": 10**3,
}

#: 급료 앞에 붙는 기간 표기 → 정규화된 키.
WAGE_PERIODS: Final[Mapping[str, str]] = {
    "연봉": "annual",
    "주급": "weekly",
    "월급": "monthly",
    "일급": "daily",
    "시급": "hourly",
}


# ---------------------------------------------------------------------------
# 능력치 그룹
# ---------------------------------------------------------------------------
#
# {그룹: {FM 한국어 헤더: 내부 canonical 키}}
#
# * 내부 키는 DB/코드에서 쓰는 이름이고, 한국어 헤더는 export에서 찾는 이름이다.
# * 확신이 없는 컬럼은 **일부러 넣지 않았다**. 그런 값도 raw_json에는 남으므로
#   나중에 여기에 한 줄 추가하고 `scripts/recompute.py` 를 돌리면 된다.
# * 중복 이름 컬럼(`스로인`, `PK`)은 어느 쪽이 능력치인지 export만 보고
#   단정할 수 없어 제외했다. 필요하면 DISAMBIGUATORS와 함께 추가할 것.
#
ATTRIBUTE_GROUPS: dict[str, dict[str, str]] = {
    "technical": {
        "코너": "corners",
        "크로스": "crossing",
        "돌파": "dribbling",
        "결정": "finishing",
        "트랩": "first_touch",
        "프리": "free_kicks",
        "헤더": "heading",
        "롱슛": "long_shots",
        "마크": "marking",
        "패스": "passing",
        # `PK` 는 두 번 등장한다. 능력치 쪽은 0이 없고(1~20), 다른 하나는
        # 페널티 득점 수라 0이 섞인다 → attribute_like 판별기가 갈라낸다.
        "PK": "penalty_taking",
        "기술": "technique",
        "태클": "tackling",
    },
    "mental": {
        "적극": "aggression",
        "예측": "anticipation",
        "대담": "bravery",
        "침착": "composure",
        "집중": "concentration",
        "판단": "decisions",
        "승부": "determination",
        "천재": "flair",
        "리더십": "leadership",
        "오프 더 볼": "off_the_ball",
        "위치": "positioning",
        "팀워크": "teamwork",
        "시야": "vision",
        "활동": "work_rate",
    },
    "physical": {
        "순간 속도": "acceleration",
        "민첩": "agility",
        "균형": "balance",
        "점프": "jumping_reach",
        "타고난 체력": "natural_fitness",
        "주력": "pace",
        "지구": "stamina",
        "몸싸움": "strength",
    },
    "goalkeeping": {
        "장악": "command_of_area",
        "기행": "eccentricity",
        "핸들": "handling",
        "1대1": "one_on_ones",
        "반사": "reflexes",
        "돌진하는 경향": "rushing_out",
        "펀칭 빈도": "punching_tendency",
        # 값 분포(골키퍼만 높고 필드 플레이어는 1~3)로 보아 GK 능력치가
        # 확실하지만, FM 원래 항목이 무엇인지는 확신이 없다. GK quality에만
        # 영향을 주므로 그룹만 맞춰 담아둔다.
        "조율": "gk_communication",
    },
}

#: 골키퍼 판정에 쓰는 포지션 토큰.
GOALKEEPER_TOKENS: Final[tuple[str, ...]] = ("GK",)


# ---------------------------------------------------------------------------
# 포지션 그룹
# ---------------------------------------------------------------------------
#
# FM 포지션 표기(`DM, M (C), AM (LC)`)를 토큰으로 쪼갠 뒤, 비교 단위가 되는
# 굵은 그룹으로 묶는다. "주전으로 쓸 만한가" 는 절대 점수가 아니라
# **같은 그룹의 경쟁자보다 나은가** 이므로 이 묶음이 비교의 기준이 된다.
#
# 한 선수는 여러 그룹에 속할 수 있다 (Nyoni → DM, CM, AM, W).
#
POSITION_GROUPS: Final[Mapping[str, str]] = {
    "GK": "GK",
    "D(C)": "CB",
    "D(R)": "FB",
    "D(L)": "FB",
    "WB(R)": "FB",
    "WB(L)": "FB",
    "WB(C)": "FB",
    "DM": "DM",
    "DM(C)": "DM",
    "DM(R)": "DM",
    "DM(L)": "DM",
    "M(C)": "CM",
    "M(R)": "WM",
    "M(L)": "WM",
    "AM(C)": "AM",
    "AM(R)": "W",
    "AM(L)": "W",
    "ST": "ST",
    "ST(C)": "ST",
    "ST(R)": "ST",
    "ST(L)": "ST",
}

#: 출력용 한국어 라벨.
POSITION_GROUP_LABELS: Final[Mapping[str, str]] = {
    "GK": "골키퍼",
    "CB": "센터백",
    "FB": "풀백/윙백",
    "DM": "수비형 MF",
    "CM": "중앙 MF",
    "WM": "측면 MF",
    "AM": "공격형 MF",
    "W": "윙어",
    "ST": "스트라이커",
}

#: 그룹별 핵심 능력치. quality 계산의 대상이 된다.
#:
#: 포지션마다 중요한 능력치가 다르므로(센터백의 `결정` 과 스트라이커의
#: `결정` 은 무게가 다르다) 그룹별로 목록을 둔다. **가중치는 아직 없다** —
#: 목록에 든 능력치를 동등하게 평균한다. 가중치가 필요해지면 값 부분을
#: ``{키: 가중치}`` 로 바꾸고 metrics.positional_quality 만 고치면 된다.
#:
#: 이 목록은 취향에 맞게 고쳐도 된다. 고친 뒤에는
#: ``python scripts/recompute.py`` 를 돌리면 과거 스냅샷까지 재계산된다.
CORE_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "GK": (
        "reflexes", "handling", "one_on_ones", "command_of_area", "gk_communication",
        "positioning", "concentration", "composure", "anticipation", "decisions",
    ),
    "CB": (
        "marking", "tackling", "heading", "positioning", "anticipation", "concentration",
        "bravery", "composure", "strength", "jumping_reach", "pace",
    ),
    "FB": (
        "tackling", "marking", "positioning", "anticipation", "concentration", "crossing",
        "first_touch", "pace", "acceleration", "stamina", "work_rate", "teamwork",
    ),
    "DM": (
        "tackling", "marking", "positioning", "anticipation", "concentration", "composure",
        "passing", "first_touch", "teamwork", "work_rate", "stamina", "strength",
    ),
    "CM": (
        "passing", "first_touch", "technique", "vision", "decisions", "composure",
        "anticipation", "off_the_ball", "teamwork", "work_rate", "stamina",
    ),
    "WM": (
        "crossing", "dribbling", "first_touch", "technique", "passing", "off_the_ball",
        "work_rate", "stamina", "pace", "acceleration",
    ),
    "AM": (
        "passing", "vision", "technique", "first_touch", "dribbling", "composure",
        "decisions", "off_the_ball", "flair", "long_shots", "anticipation",
    ),
    "W": (
        "dribbling", "technique", "first_touch", "crossing", "flair", "off_the_ball",
        "agility", "acceleration", "pace", "balance",
    ),
    "ST": (
        "finishing", "off_the_ball", "composure", "first_touch", "anticipation",
        "technique", "heading", "strength", "acceleration", "pace",
    ),
}


def attribute_lookup() -> dict[str, tuple[str, str]]:
    """``{한국어 헤더: (canonical 키, 그룹명)}`` 평면 맵을 만든다."""
    flat: dict[str, tuple[str, str]] = {}
    for group, mapping in ATTRIBUTE_GROUPS.items():
        for header, key in mapping.items():
            flat[header] = (key, group)
    return flat


def attribute_labels() -> dict[str, str]:
    """``{canonical 키: 한국어 헤더}`` — 출력용 라벨."""
    return {key: header for header, (key, _) in attribute_lookup().items()}


def attribute_keys(groups: Sequence[str] | None = None) -> list[str]:
    """지정한 그룹(기본: 전체)에 속한 canonical 능력치 키 목록."""
    selected = groups if groups is not None else list(ATTRIBUTE_GROUPS)
    keys: list[str] = []
    for group in selected:
        keys.extend(ATTRIBUTE_GROUPS.get(group, {}).values())
    return keys


#: 필드 플레이어 quality 계산에 쓰는 기본 그룹.
OUTFIELD_GROUPS: Final[tuple[str, ...]] = ("technical", "mental", "physical")
#: 골키퍼 quality 계산에 쓰는 기본 그룹.
KEEPER_GROUPS: Final[tuple[str, ...]] = ("goalkeeping", "mental", "physical")


# ---------------------------------------------------------------------------
# 중복 컬럼 판별기
# ---------------------------------------------------------------------------

Disambiguator = Callable[[Sequence[str]], float]
"""컬럼 하나의 전체 값 목록을 받아 0.0~1.0 점수를 돌려준다. 높을수록 그 필드에 적합."""

_DATE_LIKE = re.compile(r"\d{4}\s*년")
_INT_LIKE = re.compile(r"^\d{1,4}$")
_MONEY_LIKE = re.compile(r"원\s*[\d,]")


def _ratio(values: Sequence[str], predicate: Callable[[str], bool]) -> float:
    """비어있지 않은 값 중 ``predicate`` 를 만족하는 비율. 전부 비었으면 0.0."""
    populated = [v for v in values if v and v not in MISSING_TOKENS]
    if not populated:
        return 0.0
    return sum(1 for v in populated if predicate(v)) / len(populated)


def _score_squad_level(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: v in SQUAD_LEVEL_VALUES)


def _score_club_name(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: v not in SQUAD_LEVEL_VALUES)


def _score_date_like(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: bool(_DATE_LIKE.search(v)))


def _score_count_like(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: bool(_INT_LIKE.match(v.replace(",", ""))))


def _score_money_like(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: bool(_MONEY_LIKE.search(v)))


def _score_attribute_like(values: Sequence[str]) -> float:
    lo, hi = ATTRIBUTE_VALUE_RANGE

    def ok(v: str) -> bool:
        try:
            return lo <= int(v) <= hi
        except ValueError:
            return False

    return _ratio(values, ok)


def _score_non_empty(values: Sequence[str]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v and v not in MISSING_TOKENS) / len(values)


def _score_role_like(values: Sequence[str]) -> float:
    """역할명 컬럼: 임무(지원/공격)도 아니고 약어(AP, DLP)도 아닌 쪽."""
    return _ratio(values, lambda v: v not in DUTY_VALUES and not v.isupper())


def _score_duty_like(values: Sequence[str]) -> float:
    return _ratio(values, lambda v: v in DUTY_VALUES)


#: 이름으로 참조하는 판별기 레지스트리. FieldSpec.disambiguator 가 이 키를 쓴다.
DISAMBIGUATORS: Final[Mapping[str, Disambiguator]] = {
    "squad_level": _score_squad_level,
    "club_name": _score_club_name,
    "date_like": _score_date_like,
    "count_like": _score_count_like,
    "money_like": _score_money_like,
    "attribute_like": _score_attribute_like,
    "non_empty": _score_non_empty,
    "role_like": _score_role_like,
    "duty_like": _score_duty_like,
}


# ---------------------------------------------------------------------------
# 필드 정의
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """정규화 필드 하나의 정의.

    Attributes:
        name: 내부/DB에서 쓰는 canonical 이름.
        headers: export에서 찾을 한국어 헤더 후보들. 앞에 있을수록 우선.
        kind: 값 변환 방식. :mod:`src.parser` 의 변환기 키.
        disambiguator: 같은 헤더가 여러 개일 때 고를 기준(DISAMBIGUATORS 키).
            None이면 첫 번째 것을 쓴다.
        note: 사람을 위한 메모. 코드 동작에는 영향이 없다.
    """

    name: str
    headers: tuple[str, ...]
    kind: str = "text"
    disambiguator: str | None = None
    note: str = ""


# kind 별로 만들어지는 SQL 컬럼: (접미사, 타입) 목록.
# 접미사가 ""이면 필드 이름이 그대로 컬럼명이 된다.
# 여기 접미사는 parser._convert 가 만드는 키와 반드시 짝이 맞아야 한다.
KIND_COLUMNS: Final[Mapping[str, tuple[tuple[str, str], ...]]] = {
    "text": (("", "TEXT"),),
    "int": (("", "INTEGER"),),
    "float": (("", "REAL"),),
    "percent": (("", "REAL"),),
    "length_cm": (("", "REAL"),),
    "mass_kg": (("", "REAL"),),
    "distance_km": (("", "REAL"),),
    "date": (("", "TEXT"),),
    # 아래는 컬럼 여러 개로 퍼지는 kind들
    "birth": (("", "TEXT"), ("_age_label", "INTEGER")),
    "appearances": (("_raw", "TEXT"), ("_starts", "INTEGER"), ("_subs", "INTEGER")),
    # money의 대표값은 접미 없이 필드 이름 그대로 쓴다 (value, min_fee …)
    "money": (("_raw", "TEXT"), ("_low", "REAL"), ("_high", "REAL"), ("", "REAL")),
    "wage": (("_raw", "TEXT"), ("_amount", "REAL"), ("_period", "TEXT")),
}


#: snapshots 테이블에 정규화해서 넣을 필드들.
#:
#: 여기 없는 컬럼이 버려지는 게 아니라, **전부 raw_json에 남는다.**
#: 자주 조회하게 되는 필드가 생기면 여기 한 줄 추가하고
#: ``scripts/update.py --migrate`` 를 한 번 돌리면 컬럼이 추가된다.
FIELD_SPECS: Final[tuple[FieldSpec, ...]] = (
    # --- 신원 -------------------------------------------------------------
    FieldSpec("name", ("이름",)),
    FieldSpec("age", ("나이",), "int"),
    FieldSpec("birth_date", ("생일", "생년월일"), "birth", note="2001년/12월/12일 (25세)"),
    FieldSpec("nationality", ("국적",)),
    FieldSpec("second_nationality", ("2차 국적",)),
    FieldSpec("birth_country", ("출생국",)),
    FieldSpec("birth_city", ("출생 도시",)),
    FieldSpec("homegrown_status", ("홈그로운 상태",)),
    # --- 소속 -------------------------------------------------------------
    FieldSpec("club", ("구단",), disambiguator="club_name", note="`구단` 컬럼 2개 중 팀 이름 쪽"),
    FieldSpec("squad_level", ("구단",), disambiguator="squad_level", note="1군 / U23 / U18 …"),
    FieldSpec("previous_club", ("최근 구단",), note="영입/유스 구분에 쓴다"),
    FieldSpec("league", ("리그",)),
    FieldSpec("squad_number", ("번호",), "int"),
    FieldSpec("loan_status", ("임대 상태",)),
    FieldSpec("based_country", ("소재 국가",)),
    # --- 포지션 -----------------------------------------------------------
    FieldSpec("position", ("포지션",), note="M/AM (RC)"),
    FieldSpec("second_position", ("2차 포지션",)),
    FieldSpec("selected_position", ("포지션/역할/임무",)),
    FieldSpec("best_position", ("최적 포지션",), disambiguator="non_empty"),
    FieldSpec("best_role", ("최적 역할",), disambiguator="role_like", note="export에 5번 등장"),
    FieldSpec("best_role_duty", ("최적 역할",), disambiguator="duty_like", note="지원/공격/수비"),
    FieldSpec("preferred_foot", ("잘 쓰는 발",)),
    # --- 신체 -------------------------------------------------------------
    FieldSpec("height_cm", ("신장",), "length_cm"),
    FieldSpec("weight_kg", ("체중",), "mass_kg"),
    # --- 계약 / 금액 -------------------------------------------------------
    FieldSpec("value", ("몸값",), "money", note="원1,800억 또는 원290억 - 원440억"),
    FieldSpec("wage", ("급료",), "wage", note="연봉 원377억"),
    FieldSpec("wage_after_tax", ("제세 후 급료",), "wage"),
    FieldSpec("min_fee", ("최소 이적료",), "money"),
    FieldSpec("asking_price", ("이적 시 요구 금액",), "money"),
    FieldSpec("last_transfer_fee", ("최근 이적료",), "money"),
    FieldSpec("contract_start", ("시작",), "date", disambiguator="date_like"),
    FieldSpec("contract_expiry", ("만료",), "date"),
    FieldSpec("contract_type", ("F/PT", "유형"), disambiguator="non_empty"),
    FieldSpec("transfer_status", ("선수 상태 설정",)),
    # --- 출전 기록 ---------------------------------------------------------
    FieldSpec("appearances", ("경기",), "appearances", note="27 (4) → starts 27 / subs 4"),
    FieldSpec("starts", ("시작",), "int", disambiguator="count_like"),
    FieldSpec("minutes", ("출장시간",), "int"),
    FieldSpec("goals", ("골",), "int"),
    FieldSpec("assists", ("도움",), "int"),
    FieldSpec("avg_rating", ("평균평점",), "float"),
    FieldSpec("motm", ("MVP",), "int"),
    FieldSpec("yellow_cards", ("경고",), "int"),
    FieldSpec("red_cards", ("퇴장",), "int", disambiguator="count_like"),
    FieldSpec("career_apps", ("통산 출장",), "int"),
    FieldSpec("career_goals", ("통산 득점",), "int"),
    FieldSpec("youth_apps", ("유소년 경기",), "int"),
    FieldSpec("youth_goals", ("유소년 골",), "int"),
    FieldSpec("intl_caps", ("A매치",), "int"),
    FieldSpec("intl_assists", ("A매치 도움",), "int"),
    FieldSpec("intl_rating", ("A매치 평점",), "float"),
    # --- 고급 지표 ---------------------------------------------------------
    FieldSpec("xg", ("기대 득점",), "float"),
    FieldSpec("xa", ("xA",), "float"),
    FieldSpec("npxg", ("NP-xG",), "float"),
    FieldSpec("xg_per90", ("xG/90",), "float"),
    FieldSpec("xa_per90", ("xA/90",), "float"),
    FieldSpec("npxg_per90", ("NP-xG/90분",), "float"),
    FieldSpec("pass_pct", ("패스 %",), "percent"),
    FieldSpec("passes_attempted", ("패스 시도",), "int"),
    FieldSpec("passes_completed", ("패스 성공",), "int"),
    FieldSpec("key_passes_per90", ("K Ps/90",), "float"),
    FieldSpec("chances_created_per90", ("기회 창출/90",), "float"),
    FieldSpec("tackle_pct", ("태클 성공률",), "percent"),
    FieldSpec("header_pct", ("헤더 성공%",), "percent"),
    FieldSpec("distance_per90_km", ("달린 거리/90분",), "distance_km"),
    FieldSpec("sprints_per90", ("스프린트/90분",), "float"),
    FieldSpec("minutes_per_goal", ("분/골",), "float"),
    # --- 상태 / 운영 -------------------------------------------------------
    FieldSpec("condition", ("컨디션",)),
    FieldSpec("morale", ("사기",)),
    FieldSpec("player_status", ("상태",)),
    FieldSpec("fatigue", ("피로도",)),
    FieldSpec("match_load", ("경기 소화량",)),
    FieldSpec("agreed_playing_time", ("동의한 출전 시간",), note="눈부신 유망주 / 잉여 자원 …"),
    FieldSpec("actual_playing_time", ("실제 출전 시간",), note="주전 선수 / 어린 선수 …"),
    FieldSpec("future_playing_time", ("미래의 출전 시간",)),
    FieldSpec("playing_time_happiness", ("출전 시간 만족도",)),
    FieldSpec("personality", ("성격",)),
    FieldSpec("media_handling", ("언론 대처",)),
    FieldSpec("strengths", ("장점",)),
    FieldSpec("weaknesses", ("단점",)),
    # --- 부상 / 훈련 -------------------------------------------------------
    FieldSpec("injury", ("부상",)),
    FieldSpec("injury_risk", ("부상 위험",)),
    FieldSpec("injury_proneness", ("부상당하기 쉬운 정도",)),
    FieldSpec("overall_risk", ("종합 위험",)),
    FieldSpec("training_focus", ("집중 훈련",)),
    FieldSpec("training_intensity", ("훈련 강도",)),
    FieldSpec("training_rating", ("훈련 평점",), "float"),
    FieldSpec("training_happiness", ("훈련 만족도",)),
)

#: export에서 선수 ID를 찾을 때 쓸 헤더 후보.
PLAYER_ID_HEADERS: Final[tuple[str, ...]] = ("ID", "UID", "고유번호", "선수 ID")

#: ID가 없을 때 fallback 키를 만들 재료 (순서 고정).
FALLBACK_KEY_FIELDS: Final[tuple[str, ...]] = ("name", "birth_date", "nationality")


def snapshot_columns() -> list[tuple[str, str]]:
    """FIELD_SPECS로부터 snapshots 테이블의 (컬럼명, SQL 타입) 목록을 만든다.

    Returns:
        정규화 필드에서 파생된 컬럼 목록. 고정 컬럼(player_id 등)은 포함하지 않는다.
    """
    columns: list[tuple[str, str]] = []
    seen: set[str] = set()
    for spec in FIELD_SPECS:
        for suffix, sqltype in KIND_COLUMNS.get(spec.kind, KIND_COLUMNS["text"]):
            column = f"{spec.name}{suffix}"
            if column in seen:  # 설정 실수로 이름이 겹쳐도 DDL이 깨지지 않게
                continue
            seen.add(column)
            columns.append((column, sqltype))
    return columns


# ---------------------------------------------------------------------------
# 경로 기본값
# ---------------------------------------------------------------------------

DEFAULT_DB_PATH: Final[str] = "db/fm24.db"
DEFAULT_RAW_DIR: Final[str] = "data/raw"
