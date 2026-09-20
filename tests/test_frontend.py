"""프론트엔드 정적 검사.

브라우저 없이 돌릴 수 있는 범위에서, 가장 깨지기 쉬운 것들을 잡는다:

* ``app.js`` 가 찾는 DOM id가 ``index.html`` 에 실제로 있는가
* HTML이 참조하는 정적 파일이 존재하는가
* 서버 API 경로와 프론트가 호출하는 경로가 일치하는가
* CSS 변수가 정의돼 있는가 (오타 난 변수는 조용히 무시돼서 눈치채기 어렵다)

이게 통과한다고 화면이 예쁘다는 뜻은 아니지만, "버튼을 눌렀는데 아무 일도
안 일어난다" 류의 사고는 대부분 여기서 걸린다.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web import server as web_server

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

#: 화면 쪽 자바스크립트 전부를 이어붙인 것. 서버 호출은 datasource.js 에,
#: DOM 조작은 app.js 에 있으므로 둘을 함께 봐야 계약 검사가 성립한다.
JS_FILES = sorted(STATIC.glob("*.js"))
JS = "\n".join(path.read_text(encoding="utf-8") for path in JS_FILES)
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

#: 주석을 걷어낸 CSS. 규칙을 정규식으로 찾을 때 주석 안의 예시 코드
#: (`/* ... [hidden]{display:none} ... */`)를 실제 규칙으로 오인하면 안 된다.
CSS_RULES = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)


def html_ids() -> set[str]:
    return set(re.findall(r'\bid="([^"]+)"', HTML))


class TestDomReferences(unittest.TestCase):
    def test_every_dollar_lookup_exists_in_html(self) -> None:
        referenced = set(re.findall(r"\$\('([^']+)'\)", JS))
        missing = referenced - html_ids()
        self.assertEqual(missing, set(), f"app.js가 찾는 id가 HTML에 없습니다: {missing}")

    def test_query_selectors_have_matching_markup(self) -> None:
        # querySelector로 찾는 주요 훅들.
        for selector, needle in (
            (".squad thead", "<thead>"),
            (".chip", 'class="chip'),
            ("tr[data-id]", "data-id="),
        ):
            with self.subTest(selector=selector):
                self.assertIn(needle, HTML + JS)

    def test_dataset_attributes_used_by_js_are_emitted(self) -> None:
        # 필터 칩은 data-filter / data-value 로 상태를 옮긴다.
        self.assertIn("data-filter=", HTML)
        self.assertIn("data-value=", HTML)
        self.assertIn("data-sort=", HTML)

    def test_filter_keys_match_state_shape(self) -> None:
        # 칩의 data-filter 값은 state.filters의 키여야 한다.
        keys = set(re.findall(r'data-filter="([^"]+)"', HTML))
        state_block = re.search(r"filters:\s*\{([^}]*)\}", JS)
        self.assertIsNotNone(state_block)
        declared = set(re.findall(r"(\w+):", state_block.group(1)))
        self.assertTrue(keys <= declared, f"state.filters에 없는 필터: {keys - declared}")

    def test_sort_keys_are_real_squad_fields(self) -> None:
        # 표 헤더의 data-sort는 /api/squad 행의 키여야 한다.
        from web import api  # noqa: F401  (존재 확인용)

        sort_keys = set(re.findall(r'data-sort="([^"]+)"', HTML))
        api_source = (Path(__file__).resolve().parents[1] / "web" / "api.py").read_text("utf-8")
        for key in sort_keys:
            with self.subTest(key=key):
                self.assertIn(f'"{key}"', api_source)


class TestAssets(unittest.TestCase):
    def test_referenced_assets_exist(self) -> None:
        # 상대 경로를 쓴다. /fm24/ 같은 하위 경로에 올릴 때 절대 경로면 깨진다.
        for href in re.findall(r'(?:href|src)="\./([^"]+)"', HTML):
            with self.subTest(href=href):
                self.assertTrue((STATIC / href).is_file(), f"없는 파일: {href}")

    def test_assets_use_relative_paths(self) -> None:
        # pfkfks.org/fm24/ 아래에서 열리므로 "/app.js" 는 루트를 가리켜 깨진다.
        absolute = re.findall(r'(?:href|src)="(/[^/"][^"]*)"', HTML)
        self.assertEqual(absolute, [], f"절대 경로 참조: {absolute}")

    def test_js_modules_import_each_other_relatively(self) -> None:
        for path in JS_FILES:
            source = path.read_text(encoding="utf-8")
            for target in re.findall(r"from\s+'(\.[^']+)'", source):
                with self.subTest(file=path.name, target=target):
                    self.assertTrue((STATIC / target.lstrip("./")).is_file())

    def test_html_loads_no_external_resources(self) -> None:
        # 첫 화면은 인터넷 없이 떠야 한다(로컬 모드). Firebase SDK는
        # HTML이 아니라 datasource.js가 **필요할 때만** 동적 import 한다.
        external = re.findall(r'(?:href|src)="(https?://[^"]+)"', HTML)
        self.assertEqual(external, [], f"HTML이 외부 리소스를 참조합니다: {external}")

    def test_firebase_sdk_is_lazily_imported(self) -> None:
        source = (STATIC / "datasource.js").read_text(encoding="utf-8")
        # 정적 import면 로컬 모드도 인터넷을 타게 된다. 반드시 동적이어야 한다.
        self.assertNotRegex(source, r"^import .*gstatic", re.MULTILINE)
        self.assertIn("await Promise.all", source)
        self.assertIn("import(`${SDK}/firebase-app.js`)", source)

    def test_static_files_are_servable_types(self) -> None:
        for path in STATIC.iterdir():
            if path.is_file():
                with self.subTest(name=path.name):
                    self.assertIn(path.suffix, web_server._CONTENT_TYPES)


class TestApiContract(unittest.TestCase):
    def test_every_fetched_path_is_routed(self) -> None:
        called = set(re.findall(r"['\"`](/api/[a-z_]+)", JS))
        routed = set(web_server.GET_ROUTES) | set(web_server.POST_ROUTES)
        missing = called - routed
        self.assertEqual(missing, set(), f"서버에 없는 API를 호출합니다: {missing}")

    def test_no_unused_routes(self) -> None:
        # 라우트를 만들어놓고 화면에서 안 쓰면 죽은 코드다 (recompute는 예외).
        called = set(re.findall(r"['\"`](/api/[a-z_]+)", JS))
        routed = set(web_server.GET_ROUTES) | set(web_server.POST_ROUTES)
        unused = routed - called - {"/api/recompute"}
        self.assertEqual(unused, set(), f"아무도 호출하지 않는 API: {unused}")


class TestHiddenAttribute(unittest.TestCase):
    """`hidden` 이 실제로 숨기는지.

    브라우저 기본 `[hidden]{display:none}` 은 author 규칙보다 약하다.
    `.drawer{display:flex}` 처럼 display를 직접 지정한 요소에 hidden을 걸면
    **조용히 안 숨겨진다.** 실제로 상세 패널이 처음부터 열려서 표 오른쪽과
    "데이터 불러오기" 버튼을 덮는 버그가 있었다.
    """

    def test_global_hidden_override_exists(self) -> None:
        match = re.search(r"\[hidden\]\s*\{([^}]*)\}", CSS_RULES)
        self.assertIsNotNone(match, "[hidden] 전역 규칙이 없습니다")
        body = match.group(1).replace(" ", "")
        self.assertIn("display:none", body)
        self.assertIn("!important", body, "author의 display 규칙을 이기려면 !important가 필요합니다")

    def test_elements_toggled_by_js_start_hidden_in_html(self) -> None:
        # JS가 .hidden 으로 여닫는 요소는 HTML에서 hidden으로 시작해야
        # 첫 화면에 잠깐 번쩍이지 않는다.
        toggled = set(re.findall(r"\$\('([^']+)'\)\.hidden\s*=", JS))
        for element_id in ("drawer", "scrim"):
            with self.subTest(id=element_id):
                self.assertIn(element_id, toggled)
                self.assertRegex(
                    HTML, rf'id="{element_id}"[^>]*\shidden', f"#{element_id} 가 hidden으로 시작하지 않습니다"
                )

    def test_drawer_does_not_cover_the_page_on_load(self) -> None:
        # 드로어는 오른쪽 전체를 덮는 fixed 요소라, 안 숨겨지면 상단
        # 액션 버튼까지 가린다. 두 조건이 함께 지켜져야 한다.
        drawer = re.search(r"\.drawer\s*\{([^}]*)\}", CSS_RULES)
        self.assertIsNotNone(drawer)
        self.assertIn("position: fixed", drawer.group(1))
        self.assertRegex(HTML, r'id="drawer"[^>]*\shidden')
        self.assertRegex(CSS_RULES, r"\[hidden\][^{]*\{[^}]*!important")


class TestCssVariables(unittest.TestCase):
    def test_every_used_variable_is_defined(self) -> None:
        defined = set(re.findall(r"^\s*(--[\w-]+):", CSS_RULES, re.MULTILINE))
        used = set(re.findall(r"var\((--[\w-]+)", CSS_RULES))
        missing = used - defined
        self.assertEqual(missing, set(), f"정의되지 않은 CSS 변수: {missing}")

    def test_dark_theme_overrides_the_palette(self) -> None:
        self.assertIn("prefers-color-scheme: dark", CSS)

    def test_role_tags_have_styles(self) -> None:
        from src import config

        for role in config.SQUAD_ROLES:
            with self.subTest(role=role):
                self.assertIn(f".tag.{role}", CSS)

    def test_body_sets_its_own_background(self) -> None:
        body_block = re.search(r"\nbody\s*\{([^}]*)\}", CSS_RULES)
        self.assertIsNotNone(body_block)
        self.assertIn("background", body_block.group(1))


class TestDeployedCopy(unittest.TestCase):
    """pfkfks-main 에 복사해 둔 화면이 원본과 같은지.

    화면을 고치고 `scripts/deploy_web.py` 돌리는 걸 잊으면, 로컬은 새 화면인데
    pfkfks.org/fm24 는 옛 화면이 된다. 조용히 어긋나므로 여기서 잡는다.
    pfkfks-main 저장소가 옆에 없으면 건너뛴다.
    """

    def setUp(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import deploy_web  # noqa: PLC0415

        self.deploy_web = deploy_web
        if not deploy_web.DEFAULT_TARGET.is_dir():
            self.skipTest(f"배포 폴더가 없습니다: {deploy_web.DEFAULT_TARGET}")

    def test_deployed_copy_is_up_to_date(self) -> None:
        added, changed, removed = self.deploy_web.plan(
            self.deploy_web.SOURCE, self.deploy_web.DEFAULT_TARGET
        )
        self.assertEqual(
            (added, changed, removed),
            ([], [], []),
            "web/static 과 배포본이 다릅니다. `python scripts/deploy_web.py` 를 실행하세요.",
        )


class TestRoleLabels(unittest.TestCase):
    def test_js_labels_cover_every_role(self) -> None:
        from src import config

        # roleTag()의 라벨 맵에 모든 역할이 있어야 "starter" 같은 영어가 새지 않는다.
        block = re.search(r"const labels = \{([^}]*)\}", JS)
        self.assertIsNotNone(block)
        for role in config.SQUAD_ROLES:
            with self.subTest(role=role):
                self.assertIn(role, block.group(1))


if __name__ == "__main__":
    unittest.main()
