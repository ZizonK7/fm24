"""FM24 선수 추적기.

게임 내 날짜별로 FM24 HTML export를 누적 저장하고, 능력치 성장과 출전
활용도를 추적하기 위한 로컬 도구.

모듈 구성::

    config    설정 (컬럼 매핑, 능력치 그룹, 판별기)
    utils     셀 문자열 → 파이썬 값 변환
    parser    HTML → 선수 레코드
    database  SQLite 스키마와 질의
    growth    스냅샷 간 능력치 변화
    metrics   Quality / Ceiling / Growth / Usage
    importer  위를 엮는 import 파이프라인
"""

__version__ = "0.1.0"
