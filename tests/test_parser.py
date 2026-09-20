"""``src.parser`` 테스트 — 중복 컬럼 처리와 원본 보존이 핵심.

``python -m unittest discover -s tests -t .`` 또는 ``pytest`` 로 실행한다.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _fixtures
from src import config, parser


class TestDedupeHeaders(unittest.TestCase):
    def test_first_occurrence_keeps_name(self) -> None:
        unique, duplicates = parser.dedupe_headers(["구단", "나이", "구단", "구단"])
        self.assertEqual(unique, ["구단", "나이", "구단__2", "구단__3"])
        self.assertEqual(duplicates, {"구단": 3})

    def test_no_duplicates(self) -> None:
        unique, duplicates = parser.dedupe_headers(["이름", "나이"])
        self.assertEqual(unique, ["이름", "나이"])
        self.assertEqual(duplicates, {})

    def test_blank_header_gets_placeholder(self) -> None:
        unique, _ = parser.dedupe_headers(["", " "])
        self.assertEqual(unique, ["unnamed", "unnamed__2"])


class ParserTestCase(unittest.TestCase):
    """임시 디렉터리에 export를 만들어 파싱하는 공통 준비."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.result = parser.parse_export(_fixtures.write_export(self.tmp_path))
        self.by_name = {player.name: player for player in self.result.players}


class TestTableExtraction(ParserTestCase):
    def test_reads_every_row(self) -> None:
        self.assertEqual(len(self.result.players), 4)

    def test_keeps_every_column_in_raw(self) -> None:
        # 중복 컬럼까지 포함해 한 개도 잃지 않아야 한다.
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(len(nyoni.raw), len(_fixtures.HEADERS))
        self.assertIn("구단", nyoni.raw)
        self.assertIn("구단__2", nyoni.raw)

    def test_raw_values_are_untouched_strings(self) -> None:
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.raw["몸값"], "원290억 - 원440억")
        self.assertEqual(nyoni.raw["경기"], "18 (16)")

    def test_duplicate_headers_are_reported(self) -> None:
        self.assertEqual(self.result.table.duplicates, {"구단": 2, "시작": 2, "최적 역할": 2})
        self.assertTrue(any("중복 헤더" in w for w in self.result.warnings))


class TestDuplicateColumnResolution(ParserTestCase):
    def test_club_and_squad_level_split_correctly(self) -> None:
        # `구단`이 두 개 — 값의 모양으로 팀 이름과 소속 레벨을 갈라야 한다.
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.fields["club"], "Liverpool")
        self.assertEqual(nyoni.fields["squad_level"], "U21")

    def test_starts_and_contract_start_split_correctly(self) -> None:
        # `시작`이 두 개 — 하나는 선발 출전 수, 하나는 계약 시작일.
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.fields["starts"], 27)
        self.assertEqual(olise.fields["contract_start"], "2025-06-20")

    def test_best_role_prefers_role_name_over_duty(self) -> None:
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.fields["best_role"], "전진형 플레이메이커")
        self.assertEqual(nyoni.fields["best_role_duty"], "지원")

    def test_column_order_does_not_matter(self) -> None:
        # 두 `구단` 컬럼의 순서를 바꿔도 결과가 같아야 한다.
        headers = list(_fixtures.HEADERS)
        rows = [list(row) for row in (_fixtures.ROW_NYONI, _fixtures.ROW_OLISE)]
        left, right = headers.index("구단"), headers.index("구단") + 1
        for row in rows:
            row[left], row[right] = row[right], row[left]

        path = _fixtures.write_export(self.tmp_path, rows, headers, filename="swapped.html")
        swapped = parser.parse_export(path)
        by_name = {player.name: player for player in swapped.players}
        self.assertEqual(by_name["Trey Nyoni"].fields["club"], "Liverpool")
        self.assertEqual(by_name["Trey Nyoni"].fields["squad_level"], "U21")


class TestValueNormalisation(ParserTestCase):
    def test_money_range(self) -> None:
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.fields["value_raw"], "원290억 - 원440억")
        self.assertEqual(nyoni.fields["value_low"], 29_000_000_000)
        self.assertEqual(nyoni.fields["value_high"], 44_000_000_000)

    def test_money_single(self) -> None:
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.fields["value"], 180_000_000_000)

    def test_wage(self) -> None:
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.fields["wage_amount"], 37_700_000_000)
        self.assertEqual(olise.fields["wage_period"], "annual")

    def test_appearances(self) -> None:
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.fields["appearances_starts"], 18)
        self.assertEqual(nyoni.fields["appearances_subs"], 16)
        self.assertEqual(nyoni.fields["appearances_raw"], "18 (16)")

    def test_birth_date_and_age(self) -> None:
        nyoni = self.by_name["Trey Nyoni"]
        self.assertEqual(nyoni.fields["birth_date"], "2007-05-30")
        self.assertEqual(nyoni.fields["birth_date_age_label"], 19)
        self.assertEqual(nyoni.fields["age"], 19)

    def test_missing_tokens_become_none(self) -> None:
        johnson = self.by_name["Sam Johnson"]
        self.assertIsNone(johnson.fields["pass_pct"])  # "--"

    def test_units(self) -> None:
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.fields["height_cm"], 184.0)
        self.assertEqual(olise.fields["weight_kg"], 71.0)


class TestAttributes(ParserTestCase):
    def test_attributes_are_grouped(self) -> None:
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.attributes["passing"], 19.0)
        self.assertEqual(olise.attributes["vision"], 18.0)
        self.assertEqual(olise.attributes["pace"], 14.0)
        self.assertEqual(olise.attribute_groups["passing"], "technical")
        self.assertEqual(olise.attribute_groups["vision"], "mental")
        self.assertEqual(olise.attribute_groups["pace"], "physical")

    def test_missing_attribute_columns_are_warned(self) -> None:
        # fixture에는 일부 능력치만 있으므로 경고가 나와야 한다.
        self.assertTrue(any("능력치 컬럼" in w for w in self.result.warnings))

    def test_out_of_range_values_are_skipped(self) -> None:
        headers = list(_fixtures.HEADERS)
        row = list(_fixtures.ROW_OLISE)
        row[headers.index("패스")] = "86"  # 능력치 범위(1~20) 밖
        path = _fixtures.write_export(self.tmp_path, [row], headers, filename="oor.html")
        result = parser.parse_export(path)
        self.assertNotIn("passing", result.players[0].attributes)


class TestPlayerIdentity(ParserTestCase):
    def test_uses_export_id_when_present(self) -> None:
        olise = self.by_name["Michael Olise"]
        self.assertEqual(olise.player_id, "29221846")
        self.assertEqual(olise.id_source, "export")

    def test_falls_back_when_id_blank(self) -> None:
        johnson = self.by_name["Sam Johnson"]
        self.assertTrue(johnson.player_id.startswith("fb-"))
        self.assertEqual(johnson.id_source, "fallback")

    def test_warns_when_id_column_absent(self) -> None:
        headers = [h for h in _fixtures.HEADERS if h != "ID"]
        id_index = _fixtures.HEADERS.index("ID")
        rows = [
            [cell for i, cell in enumerate(row) if i != id_index]
            for row in (_fixtures.ROW_NYONI, _fixtures.ROW_OLISE)
        ]
        path = _fixtures.write_export(self.tmp_path, rows, headers, filename="noid.html")
        result = parser.parse_export(path)
        self.assertTrue(any("ID 컬럼을 찾지 못했습니다" in w for w in result.warnings))
        self.assertTrue(all(p.id_source == "fallback" for p in result.players))


class TestDiagnostics(ParserTestCase):
    def test_unmapped_headers_are_listed(self) -> None:
        # 매핑되지 않은 컬럼도 목록으로 확인할 수 있어야 한다.
        self.assertIsInstance(self.result.unmapped_headers, list)

    def test_suggest_attribute_columns_skips_known(self) -> None:
        suggestions = dict(parser.suggest_attribute_columns(self.result.table))
        known = set(config.attribute_lookup())
        # 이미 config에 등록된 능력치는 제안하지 않는다.
        self.assertFalse(known & set(suggestions))
        self.assertTrue(all(0.0 <= score <= 1.0 for score in suggestions.values()))


class TestRealExport(unittest.TestCase):
    """실제 export 파일이 있으면 그걸로도 한 번 돌려본다 (없으면 skip)."""

    REAL = Path(__file__).resolve().parents[1] / "data" / "제목없음.html"

    def setUp(self) -> None:
        if not self.REAL.exists():
            self.skipTest(f"실제 export 파일이 없습니다: {self.REAL}")

    def test_parses_without_error(self) -> None:
        result = parser.parse_export(self.REAL)
        self.assertGreater(len(result.players), 0)
        self.assertGreater(len(result.table.headers), 100)

    def test_every_player_has_an_id(self) -> None:
        result = parser.parse_export(self.REAL)
        self.assertTrue(all(p.player_id for p in result.players))

    def test_ids_are_unique(self) -> None:
        result = parser.parse_export(self.REAL)
        ids = [p.player_id for p in result.players]
        self.assertEqual(len(ids), len(set(ids)))

    def test_raw_keys_match_column_count(self) -> None:
        result = parser.parse_export(self.REAL)
        width = len(result.table.headers)
        self.assertTrue(all(len(p.raw) == width for p in result.players))

    def test_core_fields_resolved(self) -> None:
        result = parser.parse_export(self.REAL)
        for field_name in ("name", "age", "position", "club", "minutes", "value"):
            with self.subTest(field=field_name):
                self.assertIn(field_name, result.resolved_fields)


if __name__ == "__main__":
    unittest.main()
