"""테스트용 FM24 export HTML 생성기.

실제 export의 특징을 축소해서 재현한다:

* 중복 헤더 (`구단` ×2, `시작` ×2, `최적 역할` ×2)
* 한국어 금액/날짜 표기
* ``"18 (16)"`` 형태의 출전 수
* 결측을 뜻하는 ``-`` / ``--`` / 빈칸
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 중복이 섞인 헤더. 순서는 실제 export와 같은 성격으로 배치했다
#: (구단: 소속 레벨이 팀 이름보다 앞, 시작: 출전 수가 계약일보다 앞).
HEADERS: list[str] = [
    "ID", "이름", "나이", "생일", "국적",
    "구단", "구단", "포지션",
    "경기", "시작", "시작", "출장시간", "골", "도움", "평균평점",
    "몸값", "급료", "만료", "신장", "체중", "패스 %",
    "패스", "판단", "시야", "주력", "순간 속도", "몸싸움", "기술", "침착",
    "최적 역할", "최적 역할",
    "최근 구단", "최근 이적료", "실제 출전 시간",
]

ROW_NYONI: list[str] = [
    "29221847", "Trey Nyoni", "19", "2007년/5월/30일 (19세)", "ENG",
    "U21", "Liverpool", "DM, M (C), AM (LC)",
    "18 (16)", "18", "2025년/6월/20일", "1600", "3", "5", "7.10",
    "원290억 - 원440억", "연봉 원9.02억", "2030년/6월/30일", "178 cm", "68 kg", "86%",
    "13", "12", "13", "14", "15", "11", "14", "13",
    "지원", "전진형 플레이메이커",
    "Liverpool", "-", "어린 선수",
]

#: ID가 비어 있어 fallback 식별자를 쓰게 되는 행.
ROW_NO_ID: list[str] = [
    "", "Sam Johnson", "17", "2009년/4월/7일 (17세)", "ENG",
    "U18", "Liverpool", "D (RL)",
    "1 (1)", "1", "2026년/7월/1일", "98", "0", "0", "6.80",
    "원30억 - 원44억", "연봉 원1.02억", "2029년/6월/30일", "180 cm", "70 kg", "--",
    "10", "9", "10", "15", "16", "8", "11", "9",
    "지원", "풀백",
    "", "-", "어린 선수",
]

#: 몸값이 단일값이고 일부 셀이 비어 있는 행.
ROW_OLISE: list[str] = [
    "29221846", "Michael Olise", "25", "2001년/12월/12일 (25세)", "FRA",
    "1군", "Liverpool", "M/AM (RC)",
    "27 (4)", "27", "2025년/6월/20일", "2326", "14", "7", "7.18",
    "원1,800억", "연봉 원377억", "2030년/6월/30일", "184 cm", "71 kg", "86%",
    "19", "17", "18", "14", "15", "14", "18", "18",
    "지원", "전진형 플레이메이커",
    "FC 바이에른", "원1,680억", "주전 선수",
]


#: 유스 출신인데 **임대 나간** 선수. 구단이 임대처로 찍히므로, 모구단과
#: 비교하지 않으면 영입으로 잘못 분류된다.
ROW_LOANED_YOUTH: list[str] = [
    "29221848", "Joshua Abe", "19", "2007년/9월/4일 (19세)", "ENG",
    "", "번리", "AM (R)",
    "12 (5)", "12", "2026년/9월/4일", "1100", "2", "3", "6.90",
    "원60억 - 원85억", "연봉 원2.10억", "2029년/6월/30일", "176 cm", "66 kg", "79%",
    "12", "11", "12", "16", "16", "9", "13", "12",
    "공격", "인사이드 포워드",
    "Liverpool", "-", "어린 선수",
]


def build_html(headers: list[str], rows: list[list[str]]) -> str:
    """헤더와 행으로 FM export를 흉내낸 HTML 문자열을 만든다."""
    head = "".join(f"<th>{cell}</th>\n\t" for cell in headers)
    body = ""
    for row in rows:
        cells = "".join(f"<td>{cell}</td>\n\t" for cell in row)
        body += f'<tr>\n\t{cells}</tr>\n'
    return (
        "<html>\n<head>\n<title><br/></title>\n</head>\n<body>\n"
        '<table bordercolor="#000000" width="90%" align="center">\n'
        f'<tr bgcolor="#EEEEEE">\n\t{head}</tr>\n{body}'
        "</table>\n</body>\n</html>\n"
    )


def write_export(
    directory: Path,
    rows: list[list[str]] | None = None,
    headers: list[str] | None = None,
    filename: str = "export.html",
) -> Path:
    """임시 디렉터리에 export HTML을 써서 경로를 돌려준다."""
    path = Path(directory) / filename
    default = [ROW_NYONI, ROW_NO_ID, ROW_OLISE, ROW_LOANED_YOUTH]
    path.write_text(
        build_html(headers or HEADERS, rows if rows is not None else default),
        encoding="utf-8",
    )
    return path


def bump_attributes(row: list[str], amounts: dict[int, int]) -> list[str]:
    """능력치 컬럼 값을 증감시킨 새 행을 만든다.

    Args:
        row: 원본 행.
        amounts: ``{컬럼 인덱스: 증감}``.

    Returns:
        복사된 새 행.
    """
    updated = list(row)
    for index, amount in amounts.items():
        updated[index] = str(int(updated[index]) + amount)
    return updated


#: 헤더 인덱스 단축 상수 (테스트 가독성용).
IDX_PASSING = HEADERS.index("패스")
IDX_DECISIONS = IDX_PASSING + 1
IDX_VISION = IDX_PASSING + 2
IDX_PACE = IDX_PASSING + 3
IDX_MINUTES = HEADERS.index("출장시간")
IDX_AGE = HEADERS.index("나이")
