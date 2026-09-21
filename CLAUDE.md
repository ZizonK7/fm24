# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FM24 세이브의 스냅샷을 누적해 **"내가 데려온 어린 선수가 성장하고 있고, 주전에
가까워지고 있는가"** 하나에 답하는 도구. 상세한 배경·지표 정의·FM export의
함정은 `README.md` 에 있다 (한국어, 목차 참고).

## 명령

```bash
python -m unittest discover -s tests                     # 전체 (pytest가 있으면 pytest도 가능)
python -m unittest discover -s tests -p "test_status.py"  # 파일 하나
python -m unittest discover -s tests -p "test_status.py" -k test_missing_player_is_released
python scripts/serve.py                       # 로컬 사이트 (http://localhost:8765)
python scripts/update.py --file data/raw/X.html --date 2027-04-24   # 스냅샷 import
python scripts/update.py --file X.html --date 2027-04-24 --dry-run  # 넣지 않고 확인만
python scripts/recompute.py                   # 공식을 고친 뒤 파생 지표 재계산
```

`tests/` 는 패키지가 아니고 각 파일이 `sys.path` 를 직접 손보므로,
`python -m unittest tests.test_status` 형태는 `_fixtures` import에서 깨진다.
위처럼 `discover -p` 를 쓸 것.

`data/제목없음.html` 이 있으면 실제 export로도 검증하고, 없으면 그 테스트만
skip한다. 주소가 `127.0.0.1` 이 아니라 `localhost` 인 것은 Firebase Auth의
승인 도메인 때문이다 (README 「1. 화면은 두 곳에서 열린다」).

## 구조의 큰 그림

**데이터는 한 방향으로만 흐른다.**

```
FM24 export HTML → parser.py → importer.py → SQLite(db/fm24.db)
                                                  ↓ growth.py / metrics.py (파생)
                                          web/api.py (화면용 가공)
                                                  ↓
                        web/server.py (로컬)  ·  Firestore (클라우드 복사본)
```

- **진실은 언제나 로컬 `db/fm24.db` 하나다.** 클라우드(pfkfks.org/fm24)는
  그것을 복사해 보여줄 뿐이라 **읽기 전용**이다. `web/static/datasource.js` 가
  주소로 모드를 판별해 같은 화면 코드를 두 곳에서 돌린다.
- `syncToCloud()` 는 **전량 덮어쓰기**다. 클라우드에서 데이터를 고치는 기능을
  넣으려면 양방향 동기화 문제부터 풀어야 한다 (README 「알려진 한계」).
- `web/api.py` 는 HTTP와 분리돼 있어 커넥션만 있으면 테스트할 수 있다.
  `web/server.py` 는 그걸 JSON으로 감싸기만 한다.
- Firestore에는 **화면이 쓸 모양 그대로 비정규화해서** 올린다 (조인 불가).
  원본 `raw_json` 은 올리지 않는다 — 문서 1MB 한도와 세이브 원본 노출 때문.
- 화면은 프레임워크가 없다. 상태 하나를 두고 통째로 다시 그린다.
- 추천 포메이션은 `src/formation.py`의 `FORMATIONS`가 배치 가능 포지션과
  피치 좌표의 단일 진실 공급원이다. 기본은 4-2-3-1이고 4-3-3 DM, 4-4-2,
  3-4-2-1을 함께 계산한다. API의 최상위 `formation`/`squads`와 `SLOTS`는
  구버전 호환용이므로 제거하지 않는다.

## 고칠 때 알아야 할 것

**스키마 확장은 config에 한 줄.** `config.FIELD_SPECS` 에 `FieldSpec` 을
추가하면 `database.ensure_schema()` 가 `ALTER TABLE` 로 컬럼을 붙인다. 이건
import·서버 실행·재계산 때마다 불리므로 **따로 돌릴 명령이 없다**
(`scripts/update.py` 가 추가된 컬럼을 출력한다). 기존 데이터는 그대로 두고
새 컬럼만 NULL이 된다. 과거 스냅샷을 채우려면 HTML을 다시 import해야 한다. 정규화하지 않은 컬럼도 버려지지 않고 전부 `raw_json` 에 남는다.
파생 지표는 `derived_metrics` 가 key-value라 마이그레이션이 필요 없다.

**FM 데이터로 알 수 없는 것을 추론하고 있다.** 영입/유스는 이적료와 최근
구단으로, 임대는 `구단` 과 모구단(과반 구단, `squad_meta`) 비교로, 방출은
"그 시점 명단에서 사라짐" 으로 판정한다. 전부 틀릴 수 있고, 한계는 README에
적혀 있다. **사람이 직접 지정한 값(`manual_origin`, role `source=manual`)은
자동 판정이 덮어쓰지 않는다** — 이 성질을 깨지 말 것.

**표준 라이브러리만으로 돌아가야 한다.** `lxml`·`pandas`·`pytest` 는 전부
선택이며, 없어도 결과가 같아야 한다.

**로컬 모드는 인터넷 없이 떠야 한다.** Firebase SDK는 `datasource.js` 가
필요할 때만 동적 import 한다. HTML에 정적으로 넣으면 안 된다.

**정적 자산은 상대 경로.** `pfkfks.org/fm24/` 하위에서 열리므로 `/app.js` 처럼
절대 경로를 쓰면 깨진다.

**`.bat` 파일 안에 한글을 쓰지 말 것.** cmd가 OEM 코드페이지로 읽어 실행이
실패한다. 파일 *이름*의 한글은 괜찮다. `tests/test_launcher.py` 가 강제한다.

**주석과 독스트링은 한국어로, "왜" 를 적는다.** 무엇을 하는지는 코드가 이미
말하고 있다. 기존 파일들의 밀도와 어조를 따라갈 것.

## 테스트 계층

| 파일 | 무엇을 잡나 |
|---|---|
| `test_pipeline.py` | import → 성장 → 지표 → 역할. 두 시점 픽스처를 메모리 DB에 넣고 시작 |
| `test_parser.py` | 중복 컬럼명, 한국어 표기, FM 인라인 마크업 |
| `test_status.py` | 임대/방출 판정, 모구단 역산, 추천에서 제외되는지 |
| `test_formation.py` | 포메이션 정의, 포지션 적격성, 빈자리, 세 스쿼드 간 선수 중복 방지 |
| `test_web.py` | 실제 서버를 띄워 업로드 → 미리보기 → import 를 HTTP로 |
| `test_frontend.py` | 브라우저 없이 정적 검사 — `app.js` 가 찾는 DOM id, 호출하는 API, CSS 변수·클래스, 켜진 칩과 기본 필터 일치 |

`tests/_fixtures.py` 가 실제 export의 특징(중복 헤더, 한국어 금액, 임대 나간
선수)을 축소 재현한다. 새 케이스는 픽스처에 행을 추가하는 편이 낫다.

## company MCP 작업 방식

사용자가 **company로 작업하라**고 지정하면 로컬 company MCP를 사용한다.
먼저 capabilities를 확인하고 같은 request ID 안에서 작업자를 조율한 뒤, 최종
통합 artifact는 반드시 Opus 검수를 통과시킨다. 작업자가 저장소를 직접 읽거나
수정했다고 가정하지 말고, 실제 적용·테스트·브라우저 검증은 Codex가 수행한다.

사용자는 company 작업의 최종 검수를 위해 **비공개 저장소 전체 diff와 테스트
증거를 company의 외부 Opus 작업자에게 전달하는 흐름을 선호하고 허용한다**.
다만 이 파일은 플랫폼의 현재 턴 데이터 전송 승인을 대신하지 않는다. 승인 정책이
현재 사용자 메시지의 명시적 허가를 요구하면 그 정책을 따른다. 중간 확인을 확실히
피하려면 시작 요청에 “전체 비공개 diff와 테스트 결과를 외부 Opus 검수자에게
전달하는 것까지 승인”이라고 포함하도록 안내한다.

## 배포

`main` 에 `web/static/**` 변경을 푸시하면 GitHub Actions가 `pfkfks-main`
저장소로 화면 파일을 복사한다 (`.github/workflows/deploy-site.yml`). 작업
브랜치에 푸시하는 것만으로는 배포되지 않는다. **Firestore 규칙은 Actions가
배포하지 않는다** — `firebase deploy --only firestore:rules` 를 직접 돌려야
하고, `firebase-config.js` 의 `ALLOWED_UIDS` 와 규칙의 `isAdmin()` 을 맞춰야
한다.
