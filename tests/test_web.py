"""로컬 웹 서버 테스트.

실제로 서버를 띄우고 HTTP로 때려본다 (포트 0 = 빈 포트 자동 할당).
파일 업로드 → 미리보기 → import 까지의 전 과정이 핵심이다.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _fixtures
from src import database
from web import api, server


class WebTestCase(unittest.TestCase):
    """임시 DB를 쓰는 서버를 띄워두고 시작한다."""

    #: 테스트마다 DB를 새로 만든다. 한 클래스 안의 테스트는 알파벳 순으로
    #: 돌기 때문에, 공유하면 앞 테스트의 수동 역할/출신 변경이 뒤 테스트를
    #: 깨뜨린다(그 값들이 덮어써지지 않는 것은 의도된 동작이다).
    fresh_db_per_test = True

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp_path = Path(cls._tmp.name)
        cls._db_counter = 0

        # 업로드가 프로젝트의 data/raw/ 를 오염시키지 않도록 임시 폴더로 돌린다.
        cls.data_dir = cls.tmp_path / "data"
        cls.raw_dir = cls.data_dir / "raw"
        cls.raw_dir.mkdir(parents=True)
        server.Handler.data_dir = cls.data_dir
        server.Handler.raw_dir = cls.raw_dir

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        server.Handler.data_dir = server.DEFAULT_DATA_DIR
        server.Handler.raw_dir = server.DEFAULT_RAW_DIR
        cls._tmp.cleanup()

    def setUp(self) -> None:
        if self.fresh_db_per_test:
            type(self)._db_counter += 1
            server.Handler.db_path = str(self.tmp_path / f"test{self._db_counter}.db")

    # -- 요청 헬퍼 -------------------------------------------------------
    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_expect_error(self, path: str, payload: dict) -> tuple[int, str]:
        try:
            self.post(path, payload)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8")).get("error", "")
        self.fail(f"{path} 가 오류를 내지 않았습니다.")

    def upload(self, path: Path) -> dict:
        request = urllib.request.Request(
            f"{self.base}/api/upload?filename={path.name}",
            data=path.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))


class TestStaticFiles(WebTestCase):
    def test_index_is_served(self) -> None:
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            body = response.read().decode("utf-8")
        self.assertEqual(response.status, 200)
        self.assertIn("FM24 Tracker", body)

    def test_assets_are_served(self) -> None:
        for path in ("/app.js", "/style.css"):
            with self.subTest(path=path):
                with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertGreater(len(response.read()), 100)

    def test_unknown_static_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.base}/nope.js", timeout=10)
        self.assertEqual(ctx.exception.code, 404)


class TestSecurity(WebTestCase):
    def test_absolute_path_outside_data_is_refused(self) -> None:
        # 실제로 존재하는 파일이라도 data/ 밖이면 열 수 없어야 한다.
        outside = Path(__file__).resolve()
        self.assertTrue(outside.is_file())
        status, message = self.post_expect_error("/api/inspect", {"path": str(outside)})
        self.assertEqual(status, 403)
        self.assertIn("data/", message)

    def test_relative_traversal_is_refused(self) -> None:
        status, _ = self.post_expect_error(
            "/api/inspect", {"path": "../../../../Windows/win.ini"}
        )
        self.assertEqual(status, 403)

    def test_uploads_stay_inside_the_configured_raw_dir(self) -> None:
        export = _fixtures.write_export(self.tmp_path, filename="escape.html")
        request = urllib.request.Request(
            f"{self.base}/api/upload?filename={urllib.parse.quote('../../escaped.html')}",
            data=export.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        saved = Path(result["path"]).resolve()
        self.assertEqual(saved.parent, self.raw_dir.resolve())

    def test_upload_filename_is_sanitised(self) -> None:
        self.assertEqual(server.safe_filename("../../evil.html"), "evil.html")
        self.assertEqual(server.safe_filename("a/b/c.html"), "c.html")
        self.assertEqual(server.safe_filename("270424"), "270424.html")
        self.assertNotIn("/", server.safe_filename("x/y"))

    def test_unknown_api_is_404(self) -> None:
        status, _ = self.post_expect_error("/api/nope", {})
        self.assertEqual(status, 404)


class TestImportFlow(WebTestCase):
    """불러오기 버튼 뒤에서 벌어지는 일 전체."""

    def test_full_upload_then_import(self) -> None:
        export = _fixtures.write_export(self.tmp_path, filename="270424.html")

        # 1) 업로드하면 미리보기가 돌아오고, 파일명에서 날짜를 추측한다.
        preview = self.upload(export)
        self.assertEqual(preview["players"], 4)
        self.assertEqual(preview["inferred_date"], "2027-04-24")
        self.assertEqual(preview["fallback_ids"], 1)
        self.assertGreater(preview["attributes_found"], 5)

        # 2) 그 날짜로 실제 import.
        summary = self.post("/api/import", {
            "path": preview["path"], "game_date": preview["inferred_date"],
        })
        self.assertEqual(summary["imported"], 4)
        self.assertEqual(summary["game_date"], "2027-04-24")
        self.assertEqual(summary["signed"], 1)
        self.assertEqual(summary["youth"], 3)
        self.assertEqual(summary["roles_auto"], 4)

        # 3) 목록에 바로 반영된다.
        squad = self.get("/api/squad?date=2027-04-24")
        self.assertEqual(squad["game_date"], "2027-04-24")
        self.assertEqual(len(squad["players"]), 4)

    def test_import_rejects_bad_date(self) -> None:
        export = _fixtures.write_export(self.tmp_path, filename="dated.html")
        preview = self.upload(export)
        status, message = self.post_expect_error(
            "/api/import", {"path": preview["path"], "game_date": "내년 봄"}
        )
        self.assertEqual(status, 400)
        self.assertIn("해석할 수 없습니다", message)

    def test_import_requires_a_date(self) -> None:
        export = _fixtures.write_export(self.tmp_path, filename="nodate.html")
        preview = self.upload(export)
        status, _ = self.post_expect_error("/api/import", {"path": preview["path"]})
        self.assertEqual(status, 400)

    def test_files_listing_marks_imported_dates(self) -> None:
        export = _fixtures.write_export(self.tmp_path, filename="271115.html")
        preview = self.upload(export)
        self.post("/api/import", {"path": preview["path"], "game_date": "2027-11-15"})

        listing = self.get("/api/files")["files"]
        match = next(f for f in listing if f["name"] == "271115.html")
        self.assertEqual(match["inferred_date"], "2027-11-15")
        self.assertTrue(match["already_imported"])


class TestQueryEndpoints(WebTestCase):
    def setUp(self) -> None:
        super().setUp()
        export = _fixtures.write_export(self.tmp_path, filename="query.html")
        preview = self.upload(export)
        self.post("/api/import", {"path": preview["path"], "game_date": "2028-01-10"})

    def test_overview(self) -> None:
        data = self.get("/api/overview")
        self.assertIn("2028-01-10", data["dates"])
        self.assertGreaterEqual(data["players"], 4)
        self.assertIn("CB", data["position_groups"])

    def test_squad_rows_carry_the_decision_fields(self) -> None:
        players = self.get("/api/squad?date=2028-01-10")["players"]
        row = next(p for p in players if p["name"] == "Michael Olise")
        for key in ("quality", "starter_gap", "rank", "depth", "usage", "origin", "role"):
            with self.subTest(key=key):
                self.assertIn(key, row)
        self.assertEqual(row["origin"], "signed")
        self.assertEqual(row["role"], "starter")

    def test_player_detail(self) -> None:
        detail = self.get("/api/player?id=29221846")
        self.assertEqual(detail["player"]["name"], "Michael Olise")
        self.assertTrue(detail["timeline"])
        self.assertIn("technical", detail["attributes"])
        # 포지션 핵심 능력치는 표시용으로 표시돼 있어야 한다.
        flags = [a["core"] for a in detail["attributes"]["technical"]]
        self.assertIn(True, flags)

    def test_goalkeeping_attributes_are_hidden_for_outfielders(self) -> None:
        # 필드 플레이어의 GK 능력치는 전부 1~3이라 보여줄 이유가 없다.
        detail = self.get("/api/player?id=29221846")
        self.assertNotIn("goalkeeping", detail["attributes"])

    def test_missing_player_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/player?id=does-not-exist")
        self.assertEqual(ctx.exception.code, 404)

    def test_role_override_persists_and_is_manual(self) -> None:
        self.post("/api/role", {
            "player_id": "29221846", "game_date": "2028-01-10", "role": "development",
        })
        players = self.get("/api/squad?date=2028-01-10")["players"]
        row = next(p for p in players if p["player_id"] == "29221846")
        self.assertEqual(row["role"], "development")
        self.assertEqual(row["role_source"], "manual")

    def test_role_rejects_unknown_value(self) -> None:
        status, _ = self.post_expect_error("/api/role", {
            "player_id": "29221846", "game_date": "2028-01-10", "role": "superstar",
        })
        self.assertEqual(status, 400)

    def test_origin_override(self) -> None:
        self.post("/api/origin", {"player_id": "29221846", "origin": "youth"})
        players = self.get("/api/squad?date=2028-01-10")["players"]
        row = next(p for p in players if p["player_id"] == "29221846")
        self.assertEqual(row["origin"], "youth")
        self.assertTrue(row["manual_origin"])

    def test_position_choices_persist_and_change_recommendation(self) -> None:
        before = self.get("/api/squad?date=2028-01-10")
        row = next(p for p in before["players"] if p["player_id"] == "29221846")
        self.assertFalse(row["manual_positions"])
        self.post("/api/positions", {
            "player_id": "29221846", "primary_position": "ST(C)",
            "other_positions": ["AM(C)", "AM(R)"],
        })
        after = self.get("/api/squad?date=2028-01-10")
        row = next(p for p in after["players"] if p["player_id"] == "29221846")
        self.assertEqual(row["primary_position"], "ST(C)")
        self.assertEqual(row["other_positions"], ["AM(C)", "AM(R)"])
        self.assertTrue(row["manual_positions"])
        self.assertEqual(row["group"], "ST")
        self.assertEqual(after["recommendation"]["formation"], "4-2-3-1")
        picks = [slot["player"]["player_id"] for squad in after["recommendation"]["squads"].values()
                 for slot in squad if slot["player"]]
        self.assertEqual(len(picks), len(set(picks)))
        detail = self.get("/api/player?id=29221846")
        self.assertEqual(detail["player"]["primary_position"], "ST(C)")

        export = _fixtures.write_export(self.tmp_path, filename="later.html")
        preview = self.upload(export)
        self.post("/api/import", {"path": preview["path"], "game_date": "2028-06-01"})
        later = self.get("/api/squad?date=2028-06-01")
        row = next(p for p in later["players"] if p["player_id"] == "29221846")
        self.assertEqual(row["primary_position"], "ST(C)")
        self.assertEqual(row["group"], "ST")

    def test_position_choices_validate_input(self) -> None:
        for primary, others in (("invalid", []), ("GK", ["GK"]), ("GK", "ST")):
            with self.subTest(primary=primary, others=others):
                status, _ = self.post_expect_error("/api/positions", {
                    "player_id": "29221846", "primary_position": primary,
                    "other_positions": others,
                })
                self.assertEqual(status, 400)


class TestApiLayerWithoutServer(unittest.TestCase):
    """HTTP 없이 api 모듈만 쓰는 경우 (빈 DB 등 경계 상황)."""

    def test_empty_database(self) -> None:
        conn = database.connect(":memory:")
        self.addCleanup(conn.close)
        database.ensure_schema(conn)

        overview = api.overview(conn)
        self.assertEqual(overview["dates"], [])
        self.assertIsNone(overview["latest_date"])
        self.assertFalse(overview["can_compare_growth"])

        squad = api.squad(conn)
        self.assertIsNone(squad["game_date"])
        self.assertEqual(squad["players"], [])
        self.assertIsNone(api.player_detail(conn, "nobody"))


if __name__ == "__main__":
    unittest.main()
