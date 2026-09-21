# FM24 Player Tracker

Football Manager 2024에서 export한 선수 HTML을 **게임 내 날짜별로 누적 저장**하고,
다음 하나의 질문에 답하기 위한 로컬 도구다.

> **내가 데려온 선수들이 실제로 잘 성장하고 있고, 주전으로 쓸 만한가?**

그래서 화면과 지표가 전부 이 질문에 맞춰져 있다.

* **내가 데려온** — 이적료와 이전 구단으로 영입/유스를 자동 구분한다
* **실제로 성장** — 스냅샷 사이의 능력치 변화를 누적한다
* **주전으로 쓸 만한가** — 절대 점수가 아니라 **같은 포지션 경쟁자와 비교**한다

지표 계산식은 원본과 분리돼 있어서, 공식을 바꿔도 HTML을 다시 읽을 필요가 없다.

---

## 0. 실행 — 두 번 클릭

**`FM24 트래커.bat` 을 더블클릭하면 끝이다.** 브라우저가 알아서 열린다.

바탕화면에서 바로 열고 싶으면 한 번만:

```
바탕화면에 바로가기 만들기.bat   ← 더블클릭
```

바탕화면에 **FM24 트래커** 아이콘이 생긴다. 이후로는 그것만 누르면 된다.

* **끄려면** 검은 창을 닫으면 된다.
* **두 번 눌러도 안전하다.** 이미 켜져 있으면 브라우저만 다시 연다.
* 터미널에 명령을 칠 일은 없다. 아래 CLI는 원할 때만 쓰면 된다.

### 처음 한 번: Python

런처가 Python을 알아서 찾는다. 없으면 설치 방법을 화면에 띄워준다.

```powershell
winget install Python.Python.3.12
```

Python 3.10 이상이면 된다. 다른 패키지는 설치할 필요가 없다.

<details>
<summary>터미널에서 <code>python</code> 이 안 먹을 때</summary>

Windows에는 진짜 Python인 척하는 **Microsoft Store 스텁**이 PATH에 먼저
잡혀 있다. `python --version` 이 `Python` 만 출력하거나 스토어 창이 뜨면
그것이다. 런처(`FM24 트래커.bat`)는 후보를 **직접 실행해보고** 고르므로
이 문제를 겪지 않지만, 터미널에서 직접 칠 때는 걸린다.

해결: 설정 → 앱 → 앱 실행 별칭에서 `python.exe` / `python3.exe` 를 끄거나,
전체 경로를 쓴다.

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" --version
```
</details>

### 의존성

**핵심 기능은 표준 라이브러리만으로 돌아간다. `pip install` 없이 바로 쓸 수 있다.**

선택 패키지는 있으면 자동으로 쓰인다:

```powershell
pip install -r requirements.txt
```

| 패키지 | 용도 | 없으면 |
|---|---|---|
| `lxml` | HTML 파싱 가속 | 표준 `html.parser` 사용 (결과 동일) |
| `pandas` | 노트북/임시 분석 | 파이프라인에는 영향 없음 |
| `pytest` | 테스트 실행 | `python -m unittest` 로 대체 |

---

---

## 현재 이 저장소의 상태

첫 스냅샷이 이미 들어가 있다.

| | |
|---|---|
| 원본 | `data/raw/2027-03-07.html` (384컬럼, 42명) |
| game_date | **2027-03-07 — 추정치** |
| 결과 | 정규화 필드 86/86, 능력치 40/40, fallback ID 0 |

**game_date가 추정치인 이유:** export 당시의 정확한 세이브 날짜를 몰라서,
각 선수의 생년월일과 나이를 교차 검증해 가능한 범위를 좁혔다.
결과는 **2027-03-03 ~ 2027-03-11** 이었고 그 중앙값을 썼다.

절대 날짜보다 **스냅샷 사이 간격**이 중요하므로 9일 오차는 실질적 영향이 거의
없다. 다만 정확한 날짜를 알게 되면 다시 넣는 편이 낫다:

```powershell
Remove-Item db/fm24.db
python scripts/update.py --file data/raw/2027-03-07.html --date <정확한 날짜>
```

다음 export부터는 세이브 화면의 날짜를 그대로 `--date` 에 넣으면 된다.

---

## 1. 화면은 두 곳에서 열린다

| | 주소 | 할 수 있는 것 |
|---|---|---|
| **로컬** | <http://localhost:8765> | 불러오기 · 수정 · 동기화 **전부** |
| **클라우드** | <https://pfkfks.org/fm24> | **조회만** (관리자 로그인 필요) |

같은 화면 코드가 주소를 보고 모드를 고른다([`web/static/datasource.js`](web/static/datasource.js)).

**진실은 언제나 내 PC의 `db/fm24.db` 하나다.** 클라우드는 그걸 복사해 보여줄
뿐이라 읽기 전용이다. 양쪽에서 쓰게 만들면 SQLite와 Firestore를 양방향으로
맞춰야 하는데, 그건 이 도구의 목적에 비해 과하고 조용히 어긋나기 쉽다.

### 밖에서도 보려면 — 동기화

1. 로컬 앱에서 평소처럼 export를 불러온다
2. 우측 상단 **Firestore 동기화** 를 누른다 (처음엔 구글 로그인)
3. <https://pfkfks.org/fm24> 에서 확인

동기화는 **전량 덮어쓰기**다. 로컬에서 사라진 선수는 클라우드에서도 지워진다.

터미널에서 띄우고 싶으면:

```powershell
python scripts/serve.py            # 포트 8765
python scripts/serve.py --port 9000 --no-browser
```

> 주소가 `127.0.0.1` 이 아니라 `localhost` 인 이유: Firebase Auth의 기본 승인
> 도메인에 `localhost` 는 있지만 숫자 주소는 없어서, 숫자로 열면 동기화
> 로그인이 막힌다. 바인딩은 여전히 127.0.0.1이라 외부 노출은 없다.

### 화면 구성

**첫 화면 = 목적 그 자체.** 기본 필터는 **전체**(출신·나이 모두)이고,
기본 정렬이 **주전까지 남은 격차** 순이다. 즉 "주전에 가장 가까운 순서"로
선수단 전체가 바로 보이고, 필요하면 영입/유스·21세 이하 칩으로 좁히면 된다.

**상태 필터만 예외다.** 기본값이 「현재 스쿼드」라서, 임대 나간 선수와 떠난
선수는 처음에 보이지 않는다. 지금 내가 쓸 수 있는 자원이 아니기 때문이다.
「임대 포함」을 누르면 임대 나간 선수까지, 「전체」를 누르면 떠난 선수까지
나온다. 이 둘은 **추천 포메이션에서도 빠진다.**

| 상태 | 판정 근거 | 꼬리표 |
|---|---|---|
| 정상 | `구단` 이 모구단과 같다. **임대로 와 있는 선수도 여기 포함**한다 | 없음 |
| 임대 | `구단` 이 모구단과 다르다 (임대 나가면 임대처로 찍힌다) | `임대` |
| 방출 | 그 시점 명단에서 사라졌다 | `방출` |

모구단은 export에서 **과반을 차지하는 구단**으로 판정해 시점마다 기록해
둔다(`squad_meta`). 기록이 없는 옛 데이터는 저장된 스냅샷에서 역산하므로,
다시 import할 필요는 없다.

| 열 | 뜻 |
|---|---|
| 경쟁 | 그 자리를 소화하는 스쿼드 인원 중 몇 위인가 (`4/15`) |
| 실력 | **그 포지션의 핵심 능력치**만 평균 (1~20) |
| 주전까지 | 그 자리 1위와의 능력치 차이. 막대가 오른쪽(초록)이면 이미 최고 |
| 성장 | 직전 스냅샷 대비 능력치 총 변화 |
| 출전 | 스쿼드 내 최대 출장시간 대비 비율 |

선수를 **클릭**하면 오른쪽에 상세가 열린다 — 시점별 추이, 능력치 변화 내역,
포지션 핵심 능력치(강조 표시), 그리고 한 줄 판단:

> 2027-03-07 → 2027-08-15 사이에 능력치가 **+5** 올랐습니다.
> 주전과의 격차가 **1.8만큼 좁혀졌습니다**. 역할은 육성 → 로테이션 입니다.

상세의 **실력 그래프**는 게임 내 날짜를 가로축으로 두고 실력 점수를 직선으로
이어 보여준다. 점에 마우스를 올리면 해당 날짜의 점수와 직전 기록 대비 변화를
확인할 수 있다.

### 데이터 불러오기

우측 상단 **데이터 불러오기** 버튼 → 파일을 끌어다 놓거나 선택
(또는 `data/` 에 이미 있는 파일에서 고르기).

* **파일명이 `270424.html` 이면 날짜가 2027-04-24로 자동 입력된다.**
  `2027-04-24.html`, `270424`, `20270424` 전부 인식한다.
* 가져오기 전에 **몇 명 / 몇 컬럼 / 능력치 몇 개 인식** 을 보여주므로,
  엉뚱한 파일을 넣었는지 바로 알 수 있다.
* 날짜는 항상 고칠 수 있다. 파일명 추론은 어디까지나 기본값이다.

### 자동 판정을 고치기

상세 패널 아래에서 **역할**(주전/로테이션/육성/잉여)과
**출신**(영입/유스)을 직접 바꿀 수 있다.
직접 지정한 값은 **이후 import가 덮어쓰지 않는다.**

이름을 정확히 입력할 일은 없다. 전부 목록에서 고르면 된다.

### 포지션 설정과 추천 스쿼드

선수 상세의 **포지션 설정**에서 주 포지션 하나와 다른 가능 포지션을 직접 고를 수 있다.
저장한 포지션은 다음 HTML import에도 유지되며, 주 포지션을 기준으로 실력과
포지션 경쟁 지표를 다시 계산한다. 설정 전에는 FM export의 포지션을 사용한다.

상단 **추천 포메이션** 탭은 선택한 시점의 전체 선수단으로 **4-2-3-1**의
주전·로테이션·육성 3개 스쿼드를 보여준다. 포지션별 핵심 능력치, 운영 역할,
육성 스쿼드의 나이를 반영한다. 같은 선수는 한 스쿼드에만 배치하고,
소화 가능한 선수가 부족한 자리는 비워 둔다. 추천은 저장된 명단이 아니라
현재 데이터로 매번 계산된다.

---

## 2. CLI로 쓰기

사이트 없이 터미널만 써도 된다. 기능은 같다.

### 1) FM24에서 export

선수 목록 화면 → 우클릭 → **Print Screen / 웹 페이지로 내보내기** → HTML 저장.

export 열 설정에 **`ID` 컬럼을 반드시 포함**한다. 이름은 primary key로 쓰지 않는다
(동명이인, 뉴젠 이름 변경 때문). ID가 없으면 이름+생일+국적 해시를 임시로 쓰고
경고를 출력하는데, 그건 임시방편일 뿐이다.

### 2) 스냅샷 import

```powershell
python scripts/update.py --file data/raw/270424.html --date 2027-04-24
```

`--date` 는 **FM 세이브 내부 날짜**다. 현실 날짜가 아니다.
생략하면 실행 중에 물어보고, 파일명이 `270424` 같은 형태면 그 값을
기본값으로 제시한다(Enter만 치면 사용).

```
Game date: 2027-03-01
Source: data/players.html

Imported: 42 players
New players: 3
Existing players: 39
Players with previous snapshot: 39
Growth calculated: 39

Signed (영입): 21   Youth (유스): 21
Roles auto-labelled from FM: 42

Columns: 384 (duplicated names: 20, kept raw only: 253)
```

먼저 파싱만 확인해보려면:

```powershell
python scripts/update.py --file data/players.html --date 2027-03-01 --dry-run
```

### 3) 시즌 중 반복

같은 세이브에서 몇 달마다 export해서 같은 명령을 돌린다.
날짜만 다르면 계속 쌓인다.

```powershell
python scripts/update.py --file data/2027-08.html --date 2027-08-01
python scripts/update.py --file data/2028-01.html --date 2028-01-15
```

**같은 날짜를 다시 import하면 덮어쓴다** (에러가 아니다).
export 열을 추가한 뒤 다시 뽑는 일이 잦기 때문.

### 4) 선수 조회

```powershell
python scripts/show_player.py --name "Trey Nyoni"
python scripts/show_player.py --id 29221847
python scripts/show_player.py --id 29221847 --attributes --history
```

```
Trey Nyoni   (ID 29221847)

Latest snapshot:
Game date: 2027-08-01

Age: 20
Position: DM, M (C), AM (LC)
Club: Liverpool (1군)
Value: 원290억 - 원440억

Usage:
  Appearances: 18 (16)
  Starts: 18
  Subs: 16
  Minutes: 2400
  Usage score: 0.69   (스쿼드 내 최대 출장시간 대비)

Growth since previous snapshot (2027-03-01 → 2027-08-01, 153일):
  패스 (passing): +1   [13 → 14]
  시야 (vision): +1   [13 → 14]
  주력 (pace): +1   [14 → 15]

Total attribute growth: +3
  technical: +1
  mental: +1
  physical: +1
```

### 5) 선수 비교

```powershell
python scripts/compare_players.py --ids 29221846 29221847 --attributes
```

### 6) 역할 라벨 (starter / rotation / development / fringe)

**대부분 손댈 필요가 없다.** FM이 `실제 출전 시간` 컬럼에 이미 판정해두고
있어서 import할 때 자동으로 붙는다:

| FM 표기 | 역할 |
|---|---|
| 주전 선수, 중요 선수 | `starter` |
| 비주전 선수, 뛰어난 후보, 선수단 선수, 후보 | `rotation` |
| 어린 선수, 미래의 유망주, 눈부신 유망주 | `development` |
| 비상 후보, 잉여 자원 | `fringe` |

매핑은 `config.PLAYING_TIME_ROLE_MAP` 에서 고칠 수 있다.

자동 판정이 마음에 안 들면 사이트 상세 패널에서 직접 고르거나, CSV로:

```csv
player_id,game_date,role,note
29221846,2027-03-07,starter,
29221847,2027-03-07,development,겨울에 1군 합류 예정
```

```powershell
python scripts/update.py --file data/raw/270424.html --date 2027-04-24 --roles roles.csv
```

**사람이 넣은 라벨은 이후 import가 덮어쓰지 않는다.**
라벨은 지정한 날짜부터 다음 라벨이 나올 때까지 유효하다.

---

## 1-2. pfkfks.org/fm24 에 화면을 배포하기

`web/static/`을 수정해 이 저장소의 `main`에 push하면 GitHub Actions가
화면 파일을 `pfkfks-main/public/fm24/`에 복사해 커밋한다. 이어서
`pfkfks-main`의 기존 Firebase Hosting 배포가 자동으로 실행된다.
HTML 데이터는 별도로 로컬 앱에서 **Firestore 동기화**를 눌러야 한다.

수동으로 화면을 복사해야 할 때는 `python scripts/deploy_web.py`를 사용할 수 있다.

**왜 복사하는가:** Firebase Hosting 배포는 **사이트 전체를 교체한다.**
이 저장소에서 직접 배포하면 pfkfks.org의 나머지(/work, /study, /football…)가
전부 날아간다. 그래서 화면 파일은 `pfkfks-main/public/fm24/` 안에 있어야 하고,
이 저장소는 원본만 갖는다.

배포에 함께 필요한 것 (한 번만 해두면 된다):

| 무엇 | 어디 | 비고 |
|---|---|---|
| `/fm24` 라우팅 | `pfkfks-main/firebase.json` | rewrites, catch-all `**` 보다 **앞**에 |
| Firestore 규칙 | `pfkfks-main/firestore.rules` | `match /fm24/{document=**}` → `isAdmin()` |
| 접근 UID | `web/static/firebase-config.js` 의 `ALLOWED_UIDS` | 규칙의 `isAdmin()` 과 **맞춰야 함** |

Firestore 규칙은 GitHub Actions가 배포하지 않는다(hosting만 한다). 규칙을
고쳤으면 수동으로:

```powershell
cd ../pfkfks-main
firebase deploy --only firestore:rules --project pfkfks
```

### Firestore 구조

```
fm24/overview                   전체 요약 1건
fm24/index/snapshots/{날짜}      그 시점 스쿼드 전체
fm24/index/players/{선수id}      선수별 상세 이력
```

원본 384컬럼(`raw_json`)은 **올리지 않는다.** 화면에 쓰는 것만 비정규화해
담는다 — Firestore는 조인을 못 하고, 문서 한도(1MB)도 아껴야 한다.
현재 선수 문서 최대 6.3KB, 스냅샷 문서 28KB.

## 2. 계산식을 바꾸고 싶을 때

**HTML을 다시 import할 필요가 없다.**

1. `src/metrics.py` 의 함수를 고친다 (또는 `@metric` 으로 새 지표를 등록한다).
2. `FORMULA_VERSION` 을 올린다.
3. 재계산:

```powershell
python scripts/recompute.py
```

`snapshots` / `snapshot_attributes` 는 그대로 두고
`growth_deltas` / `derived_metrics` 만 다시 만든다.

능력치 그룹(`src/config.py` 의 `ATTRIBUTE_GROUPS`)을 고쳤을 때도 같다.
다만 **새로 추가한 능력치는 과거 스냅샷에 저장돼 있지 않으므로**,
그 능력치까지 소급하려면 과거 HTML을 다시 import해야 한다
(원본 파일을 `data/raw/` 에 보관해두는 이유다).

---

## 3. 프로젝트 구조

```
football_manager/
  FM24 트래커.bat                 # ← 더블클릭해서 실행
  바탕화면에 바로가기 만들기.bat    # ← 한 번만 실행
  CLAUDE.md                       # Claude Code용 요약 (구조·규칙·명령)
  data/
    raw/            # export 원본 HTML 보관 (소급 재import용)
    processed/      # 임시 분석 산출물
  db/
    fm24.db         # SQLite (자동 생성)
  src/
    config.py       # 컬럼 매핑, 능력치 그룹, 포지션 정의  ← 대부분 여기만 고친다
    utils.py        # 셀 문자열 → 값 (금액/날짜/출전수/포지션)
    parser.py       # HTML → 선수 레코드
    database.py     # SQLite 스키마와 질의
    growth.py       # 스냅샷 간 능력치 변화
    metrics.py      # Quality / Ceiling / Growth / Usage / 주전 격차
    importer.py     # import 파이프라인, 영입·유스 판정
  web/
    api.py          # 화면용 데이터 가공 (HTTP와 분리 → 테스트 가능)
    server.py       # http.server 기반 JSON API
    static/         # index.html · app.js · style.css (프레임워크 없음)
  scripts/
    serve.py            # 로컬 사이트 띄우기
    update.py           # 스냅샷 import
    show_player.py      # 선수 조회
    compare_players.py  # 선수 비교
    recompute.py        # 지표 재계산
    make_shortcut.py    # 바탕화면 바로가기 생성
    _findpython.bat     # 런처가 쓰는 Python 탐색 (Store 스텁 회피)
  tests/
```

**`.bat` 파일 안에는 한글을 쓰지 말 것.** cmd는 `.bat` 을 OEM 코드페이지
(한국어 Windows에서 949)로 읽기 때문에 UTF-8 한글을 넣으면 깨져서 실행이
실패한다. 파일 *이름*의 한글은 괜찮다. 한글 출력은 전부 Python 쪽에서
한다(`utils.enable_utf8_stdout()`). `tests/test_launcher.py` 가 이걸 강제한다.

---

## 4. 데이터베이스 스키마

### `players` — 선수 1명당 1행

| 컬럼 | 설명 |
|---|---|
| `player_id` | FM export의 ID (primary key) |
| `name`, `birth_date`, `nationality`, `primary_position` | 가장 최근 관측값 |
| `first_seen_date`, `last_seen_date` | 관측 범위 (게임 내 날짜) |
| `id_source` | `export` 또는 `fallback` |

### `snapshots` — `(player_id, game_date)` 당 1행

정규화된 주요 필드(약 90개 컬럼)와 **`raw_json` 에 export 원본 384컬럼 전부**.

컬럼 구성은 `config.FIELD_SPECS` 에서 자동 생성된다. config에 필드를 추가하고
`update.py` 를 돌리면 `ALTER TABLE ADD COLUMN` 이 알아서 실행된다.

정규화하지 않은 FM 필드는 이렇게 꺼낸다:

```python
from src import database
snapshot = database.get_snapshot(conn, "29221846", "2027-03-01")
database.raw_value(snapshot, "훈련 만족도")
```

### `snapshot_attributes` — 능력치 (long 형태)

```
player_id | game_date | attribute | attr_group | value
```

넓은 컬럼 대신 긴 형태를 쓰는 이유: 능력치를 추가해도 스키마를 안 바꿔도 되고,
성장 계산이 self-join 하나로 끝난다.

### `growth_deltas` — 능력치 변화 (파생)

```
player_id | game_date | prev_game_date | attribute | previous | current | delta
```

### `derived_metrics` — 파생 지표 (key-value)

```
player_id | game_date | metric | value_num | value_text | formula_version
```

고정 컬럼(quality/ceiling/growth/usage) 대신 key-value로 둔 이유:
지표를 새로 추가할 때 마이그레이션이 필요 없다.
`formula_version` 이 함께 저장되므로 어떤 공식으로 계산한 값인지 추적된다.

### `squad_roles` — 운영 역할 라벨

```
player_id | game_date | role | source | note
```

`source` 가 `fm_auto` 면 FM의 `실제 출전 시간` 에서 자동으로 붙인 것,
`manual`/`csv`/`cli` 면 사람이 넣은 것이다.
**자동 판정은 사람이 넣은 값을 덮어쓰지 않는다.**

### `player_origin` — 영입/유스 구분

```
player_id | origin | signed_from | signed_fee | joined_date | manual_origin
```

`manual_origin = 1` 이면 사람이 직접 지정한 것이라 자동 판정이 덮어쓰지 않는다.

### `squad_meta` — 시점별 모구단

```
game_date | parent_club | updated_at
```

임대 나간 선수는 `구단` 이 임대처로 찍히므로, "우리 팀" 이 어디였는지
시점마다 기억해야 임대 여부를 판별할 수 있다. import가 과반 구단으로
판정해 넣는다. 값이 없으면 조회 시 스냅샷에서 역산해 채운다.

---

## 5. 지표의 현재 정의 (v0.1)

### 목적에 직접 답하는 것

| 지표 | 정의 | 읽는 법 |
|---|---|---|
| **starter_gap** | 그 포지션 1위와의 quality 차이 | 0 이상이면 이미 그 자리 최고. 스냅샷마다 0에 가까워지면 **주전으로 올라오는 중** |
| **position_rank / depth** | 그 자리를 소화하는 인원 중 순위 | `3/11` — 11명 중 3위. 순위만으론 부족해서 인원도 같이 본다 |
| **growth** | 직전 스냅샷 대비 능력치 총 변화의 연 환산 | 스냅샷 간격이 달라도 비교할 수 있게 365일 기준 |
| **usage** | 스쿼드 최대 출장시간 대비 비율 (0~1) | 절대 출장시간이 아니라 이 팀에서 얼마나 쓰이는가 |

### 보조 지표

| 축 | v0.1 정의 | 한계 |
|---|---|---|
| **Quality** | **포지션별** 핵심 능력치의 평균 (1~20) | 목록 안에서는 아직 가중치가 없다 (동등 평균) |
| **Ceiling** | quality + (24세까지 남은 햇수 × 0.35) | FM export의 `잠재력` 컬럼이 비어 있어 PA 관측값이 없다. **가장 거친 추정** |

포지션별 핵심 능력치는 `config.CORE_ATTRIBUTES` 에 있다. 취향에 맞게 고치고
`python scripts/recompute.py` 를 돌리면 과거 스냅샷까지 재계산된다.

실험용 지표 (영입 철학 검증용, 아직 신뢰하지 말 것):

| 지표 | 정의 | 의도 |
|---|---|---|
| `talent_score` | 상위 3개 능력치의 평균 | "특출난 미래형 유망주" |
| `balance_score` | `mean - 0.5 × std` | "균형형 육성조 유망주" |
| `floor_score` | 핵심 능력치의 최솟값 | 1군에서 안 무너지는 하한 |

이 값들은 `derived_metrics` 에 저장되지만 어떤 자동 판단에도 쓰이지 않는다.

---

### 실력 지수 v0.1.1 제안안 — 4-2-3-1 좌윙 인사이드 포워드(공격)

> **아직 코드에는 적용하지 않은 설계안이다.** `FORMULA_VERSION` 과 실제 `quality`\
> 계산은 계속 v0.1을 사용한다. 전술별 기준을 충분히 정한 뒤 한 번에 반영한다.

팀의 기본 전제는 **4-2-3-1이며 모든 선수가 어느 정도 수비에 참여하는 것**이다.\
윙어도 공을 잃으면 즉시 다시 뛰고, 필요하면 풀백 위치까지 내려와 수비하는 책임감을\
요구한다. 좌윙은 전통적인 터치라인 윙어보다 **왼쪽에서 출발하는 득점형 공격수**에\
가깝게 본다. 선호 이미지는 비니시우스처럼 뒷공간을 파고들고, 호날두처럼 박스 안으로\
들어가 골을 노리는 인사이드 포워드(공격)다.

최종 좌윙 적합도는 네 축을 1~20 스케일로 계산한다.

```text
LW_IFA = 0.30 * Run + 0.30 * Goal + 0.20 * Carry + 0.20 * DefWork
```

| 하위 지수 | 비중 | 의미 |
| --- | --- | --- |
| **Run** | 30% | 타이밍을 읽고 뒷공간을 파괴하는 침투력 |
| **Goal** | 30% | 박스 안으로 들어가 공격을 득점으로 끝내는 능력 |
| **Carry** | 20% | 왼쪽에서 공을 받아 직접 전진하고 수비를 깨는 능력 |
| **DefWork** | 20% | 공을 잃은 뒤 재압박하고 수비 위치까지 복귀하는 책임감 |

#### Run — 침투력

```text
0.25 * 오프더볼
+ 0.20 * 순간속도
+ 0.15 * 주력
+ 0.15 * 예측
+ 0.15 * 판단
+ 0.10 * 민첩
```

단순히 빠른 선수보다 **언제 뛰어야 하는지 알고 먼저 움직이는 선수**를 높게 본다.\
그래서 오프더볼이 가장 큰 비중을 가진다.

#### Goal — 득점력

```text
0.30 * 결정
+ 0.20 * 침착
+ 0.20 * 오프더볼
+ 0.15 * 예측
+ 0.10 * 트랩
+ 0.05 * 헤더
```

결정력만 보는 것이 아니라 좋은 위치를 먼저 잡고, 박스 안에서 침착하게 마무리하는\
능력을 함께 본다. 오프더볼은 침투와 득점 양쪽에서 중요한 능력이라 중복 반영한다.

#### Carry — 돌파/전진력

```text
0.25 * 드리블
+ 0.20 * 순간속도
+ 0.15 * 주력
+ 0.15 * 민첩
+ 0.10 * 기술
+ 0.10 * 트랩
+ 0.05 * 균형
```

이 역할의 좌윙은 크로스를 올리기보다 직접 안쪽으로 전진해 수비를 깨는 것이 우선이다.\
따라서 v0.1의 W 핵심 능력에 들어 있던 크로스는 이 지수에서는 제외한다.

#### DefWork — 수비 책임감

```text
0.30 * 활동량
+ 0.20 * 팀워크
+ 0.15 * 지구력
+ 0.15 * 승부욕
+ 0.10 * 적극성
+ 0.10 * 판단
```

윙어에게 풀백 수준의 태클·마킹 기술을 요구하는 것이 아니라, **공격이 끝났다고 자기**\
**일이 끝난 것으로 생각하지 않는 선수**를 높게 본다. 많이 뛰고, 동료와 함께 움직이며,\
공을 잃은 뒤 다시 수비에 참여할 수 있는지를 본다.

#### 수비 하한 패널티

전술 철학상 수비 참여는 선택 사항이 아니므로 가중평균만으로 끝내지 않는다.\
`DefWork < 12` 이면 다음 패널티를 추가하는 안을 우선 검토한다.

```text
LW_IFA_final = LW_IFA - 0.5 * (12 - DefWork)
```

예를 들어 Run/Goal/Carry가 모두 19이고 DefWork가 8이면 단순 평균은 16.8이지만,\
패널티 적용 후 14.8이 된다. 공격력이 압도적이어도 팀의 기본 활동량 기준을 크게\
밑도는 선수는 높은 평가를 받지 못하게 하려는 장치다.

이 v0.1.1은 **좌윙 역할 하나에 대한 첫 전술 적합도 설계안**이다. 우윙, 공격형 미드필더,\
투 볼란치, 풀백, 센터백, 스트라이커까지 역할을 정한 뒤 전체 지표 체계를 확정한다.

---

## 6. FM24 HTML의 까다로운 점과 대응

실제 export(384컬럼, 42명)를 뜯어보고 대응한 것들.

### 중복 컬럼명

`최적 역할` ×5, `잠재력` ×3, `구단` ×2, `시작` ×2, `능력` ×2, `PK` ×2 …

- 모든 컬럼을 `__2`, `__3` 접미로 보존한다. 하나도 버리지 않는다.
- 정규화 필드는 **위치가 아니라 값의 모양**으로 고른다.
  `구단` 두 개 중 `{1군, U23, U18}` 에 해당하는 쪽이 `squad_level`,
  나머지가 `club`. `시작` 두 개 중 날짜 형식인 쪽이 `contract_start`,
  정수인 쪽이 `starts`.
- 판별기는 `config.DISAMBIGUATORS` 에 함수로 등록돼 있다.
  판별기가 어느 쪽도 못 고르면 **NULL로 둔다**. 틀린 값을 넣지 않는다.

### 한국어 표기

| export 값 | 파싱 결과 |
|---|---|
| `원1,800억` | `180000000000` |
| `원290억 - 원440억` | low `29000000000` / high `44000000000` / value 중간값 |
| `원436.09억` | `43609000000` |
| `원3,350만` | `33500000` |
| `연봉 원377억` | amount `37700000000`, period `annual` |
| `27 (4)` | starts `27`, subs `4` |
| `2001년/12월/12일 (25세)` | `2001-12-12`, 나이 25 |
| `86%` | `86.0` |
| `184 cm` / `71 kg` / `370.2km` | `184.0` / `71.0` / `370.2` |
| `-`, `--`, 빈칸 | `NULL` |

`없음`, `미설정` 은 **결측이 아니라 실제 값**이므로 그대로 텍스트로 둔다.

### 빈 섹션 헤더

`능력`, `잠재력`, `성과`, `체력` 컬럼은 FM 화면의 섹션 구분선이 그대로
export된 것이라 전 행이 비어 있다. 억지로 해석하지 않고 raw로만 둔다.

### FM 인라인 마크업

`Richard Olise |c:disabled|형제|/c|` → `Richard Olise (형제)`

### 컬럼이 바뀌어도 안 깨지게

- 못 찾은 필드는 조용히 NULL이 된다. 예외를 던지지 않는다.
- 각 필드는 후보 헤더를 여러 개 가질 수 있다 (`("F/PT", "유형")`).
- 능력치 컬럼을 못 찾으면 요약에 경고로 나온다.

### 능력치 컬럼 찾기 도우미

`config.ATTRIBUTE_GROUPS` 에는 **확신이 있는 능력치만** 넣었다.
빠진 것을 찾으려면:

```powershell
python scripts/update.py --file data/players.html --date 2027-03-01 --dry-run --suggest-attributes
```

값이 대부분 1~20 정수인 미등록 컬럼을 보여준다. 출전 수처럼 우연히 범위에
들어오는 것도 섞이니 사람이 보고 판단해서 config에 추가한다.
추가 후 과거 데이터까지 채우려면 `data/raw/` 의 HTML을 다시 import한다.

정규화 안 된 컬럼 전체 목록:

```powershell
python scripts/update.py --file data/players.html --date 2027-03-01 --dry-run --show-unmapped
```

---

## 7. 테스트

```powershell
python -m unittest discover -s tests
```

pytest가 있으면:

```powershell
pytest
```

`data/제목없음.html` 이 있으면 실제 export로도 검증한다 (없으면 skip).

현재 상태: **214 tests, 모두 통과** (표준 라이브러리만 사용).

파일 하나만 돌리려면 `-p`, 케이스 하나만 돌리려면 `-k` 를 쓴다.
`tests/` 는 패키지가 아니라서 `python -m unittest tests.test_status` 형태는
`_fixtures` import에서 깨진다.

```powershell
python -m unittest discover -s tests -p "test_status.py"
python -m unittest discover -s tests -p "test_status.py" -k test_missing_player_is_released
```

웹 테스트는 실제로 서버를 띄워 업로드 → 미리보기 → import 전 과정을 HTTP로
확인한다. 프론트엔드는 브라우저 없이 정적 검사만 한다 — `app.js` 가 찾는
DOM id가 실제로 있는지, 호출하는 API가 서버에 있는지, CSS 변수에 오타가
없는지. 화면이 예쁜지는 검증하지 못한다.

---

## 8. 앞으로 답하고 싶은 질문들

이 구조는 다음 분석을 위한 기반이다. 지금은 답할 수 없고,
**시즌을 여러 번 누적해야** 답이 나온다.

- 어린 선수를 1군에서 많이 굴리면 실제로 더 빨리 성장하는가?
- 17 / 18 / 19세에 같은 출전시간을 받았을 때 성장량 차이는?
- 특출난 유망주(`talent_score` 높음)와 균형형(`balance_score` 높음) 중
  어느 쪽이 더 잘 성장하는가?
- 육성조 → 부주전 → 주전 경로는 실제로 어떻게 흘러가는가?
- 영입 당시 능력치 프로필이 2~3시즌 뒤 성장량을 얼마나 설명하는가?

필요한 데이터는 이미 다 저장되고 있다. 예를 들어 "출전시간 대비 성장" 은:

```sql
SELECT p.name,
       s.age,
       s.minutes,
       gm.value_num AS growth_total
FROM snapshots s
JOIN players p          ON p.player_id = s.player_id
JOIN derived_metrics gm ON gm.player_id = s.player_id
                       AND gm.game_date = s.game_date
                       AND gm.metric = 'attribute_growth_total'
WHERE s.age <= 21
ORDER BY gm.value_num DESC;
```

---

## 9. 알려진 한계

**목적에 직접 영향을 주는 것부터:**

- **스냅샷이 2개 이상이어야 아무것도 답할 수 있다.** 지금은 1개뿐이라
  성장 열이 전부 비어 있다. 이건 코드가 아니라 시간 문제다.
- **영입/유스 판정이 추론이다.** 이적료와 이전 구단으로 가른다.
  영입 직후 임대를 보낸 선수는 유스로 잘못 볼 수 있다(이적료가 있으면
  잡힌다). 사이트에서 직접 고칠 수 있고, 고친 값은 유지된다.
- **"방출" 은 사라졌다는 뜻일 뿐이다.** 방출·이적·계약만료를 FM export로는
  구분할 수 없다. export를 일부만 뽑아도 똑같이 사라진 것으로 보이므로,
  다음 시점에 다시 나타나면 그때부터 정상으로 돌아온다.
- **임대로 온 선수는 따로 구분하지 않는다.** FM에서 구단이 우리 팀으로
  찍히기 때문에 기존 선수와 같이 보인다. 영입/유스 판정에서는 `최근 구단` 이
  모구단과 달라 **영입으로 잡힌다.**
- **포지션 그룹이 굵다.** `AM(R)` 과 `AM(L)` 을 둘 다 `W`(윙어)로 묶어서,
  실제 export에서는 윙어 경쟁자가 17명으로 잡힌다. 좌우를 나누려면
  `config.POSITION_GROUPS` 를 고치면 된다.
- **Quality 목록 안에 가중치가 없다.** 포지션별로 볼 능력치는 구분하지만,
  그 안에서는 전부 동등하게 평균한다.

**그 밖에:**

- **Ceiling에 실제 근거가 없다.** export의 `잠재력` 컬럼이 비어 있다.
  FM 화면에서 PA를 보이게 설정할 수 있다면 그 컬럼을 export에 넣고
  `config.FIELD_SPECS` 에 추가하는 것이 가장 큰 개선이다.
- **Usage가 출장시간만 본다.** 리그전과 컵 하위 라운드를 구분하지 않는다.
- **능력치 매핑이 부분적이다.** 43개를 인식하지만 확신이 없는 컬럼은 뺐다
  (`막음`, `골킥`, 중복된 `스로인`). `조율` 은 값 분포로 GK 능력치인 것은
  확실하나 FM 원래 항목명은 모른다. 빠진 것들도 raw에는 남아 있다.
- **스냅샷 간격이 불규칙하면 성장 비교가 왜곡된다.** 시즌당 2~4회 등
  간격을 일정하게 뽑는 편이 낫다.
