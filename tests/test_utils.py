"""``src.utils`` 의 셀 파서 테스트.

``python -m unittest discover -s tests -t .`` 또는 ``pytest`` 로 실행한다.
"""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils


class TestCleanText(unittest.TestCase):
    def test_strips_and_collapses_whitespace(self) -> None:
        self.assertEqual(utils.clean_text("  DM,   M (C)\n"), "DM, M (C)")

    def test_converts_fm_inline_markup(self) -> None:
        raw = "Richard Olise |c:disabled|형제|/c|, Vincent Kompany |c:disabled|전 감독|/c|"
        self.assertEqual(utils.clean_text(raw), "Richard Olise (형제), Vincent Kompany (전 감독)")

    def test_handles_none_and_nbsp(self) -> None:
        self.assertEqual(utils.clean_text(None), "")
        self.assertEqual(utils.clean_text("a\xa0b"), "a b")


class TestMissing(unittest.TestCase):
    def test_recognises_fm_missing_tokens(self) -> None:
        for token in ("", "-", "--", "  ", "N/A"):
            with self.subTest(token=token):
                self.assertTrue(utils.is_missing(token))

    def test_meaningful_values_are_not_missing(self) -> None:
        # "없음"은 '정보 없음'이 아니라 '해당 사항 없음'이라는 실제 값이다.
        for token in ("없음", "0", "미설정"):
            with self.subTest(token=token):
                self.assertFalse(utils.is_missing(token))


class TestNumbers(unittest.TestCase):
    def test_parse_int(self) -> None:
        self.assertEqual(utils.parse_int("2326"), 2326)
        self.assertEqual(utils.parse_int("1,030"), 1030)
        self.assertIsNone(utils.parse_int("-"))
        self.assertIsNone(utils.parse_int("7.18"))  # 정수가 아니면 버린다

    def test_parse_float(self) -> None:
        self.assertEqual(utils.parse_float("7.18"), 7.18)
        self.assertEqual(utils.parse_float("166.14"), 166.14)
        self.assertIsNone(utils.parse_float("--"))
        self.assertIsNone(utils.parse_float("전임 계약"))

    def test_parse_percent_keeps_0_to_100_scale(self) -> None:
        self.assertEqual(utils.parse_percent("86%"), 86.0)
        self.assertEqual(utils.parse_percent("0%"), 0.0)
        self.assertIsNone(utils.parse_percent("-"))

    def test_units(self) -> None:
        self.assertEqual(utils.parse_length_cm("184 cm"), 184.0)
        self.assertEqual(utils.parse_mass_kg("71 kg"), 71.0)
        self.assertEqual(utils.parse_distance_km("370.2km"), 370.2)
        self.assertEqual(utils.parse_distance_km("14.3km"), 14.3)


class TestAppearances(unittest.TestCase):
    def test_starts_and_subs(self) -> None:
        parsed = utils.parse_appearances("27 (4)")
        assert parsed is not None
        self.assertEqual((parsed.starts, parsed.subs), (27, 4))
        self.assertEqual(parsed.raw, "27 (4)")

    def test_starts_only(self) -> None:
        parsed = utils.parse_appearances("35")
        assert parsed is not None
        self.assertEqual((parsed.starts, parsed.subs), (35, 0))

    def test_missing(self) -> None:
        self.assertIsNone(utils.parse_appearances("-"))

    def test_unexpected_format_keeps_raw(self) -> None:
        parsed = utils.parse_appearances("6 (4) / 2")
        assert parsed is not None
        self.assertIsNone(parsed.starts)
        self.assertEqual(parsed.raw, "6 (4) / 2")


class TestMoney(unittest.TestCase):
    def test_single_amount(self) -> None:
        money = utils.parse_money("원1,800억")
        assert money is not None
        self.assertEqual(money.value, 180_000_000_000)
        self.assertEqual(money.low, money.high)

    def test_decimal_amount(self) -> None:
        money = utils.parse_money("원436.09억")
        assert money is not None
        self.assertAlmostEqual(money.value or 0.0, 43_609_000_000, places=2)

    def test_range(self) -> None:
        money = utils.parse_money("원185억 - 원270억")
        assert money is not None
        self.assertEqual(money.low, 18_500_000_000)
        self.assertEqual(money.high, 27_000_000_000)
        self.assertEqual(money.value, 22_750_000_000)

    def test_man_unit(self) -> None:
        money = utils.parse_money("원3,350만")
        assert money is not None
        self.assertEqual(money.value, 33_500_000)

    def test_missing(self) -> None:
        self.assertIsNone(utils.parse_money("-"))

    def test_unparseable_keeps_raw(self) -> None:
        money = utils.parse_money("협상 필요")
        assert money is not None
        self.assertIsNone(money.value)
        self.assertEqual(money.raw, "협상 필요")


class TestWage(unittest.TestCase):
    def test_annual(self) -> None:
        wage = utils.parse_wage("연봉 원377억")
        assert wage is not None
        self.assertEqual(wage.amount, 37_700_000_000)
        self.assertEqual(wage.period, "annual")

    def test_weekly(self) -> None:
        wage = utils.parse_wage("주급 원1.35억")
        assert wage is not None
        self.assertEqual(wage.amount, 135_000_000)
        self.assertEqual(wage.period, "weekly")

    def test_no_period_label(self) -> None:
        wage = utils.parse_wage("원9.02억")
        assert wage is not None
        self.assertIsNone(wage.period)
        self.assertEqual(wage.amount, 902_000_000)


class TestDates(unittest.TestCase):
    def test_korean_date(self) -> None:
        self.assertEqual(utils.parse_date("2025년/6월/20일"), dt.date(2025, 6, 20))

    def test_iso_date(self) -> None:
        self.assertEqual(utils.parse_date("2027-03-01"), dt.date(2027, 3, 1))

    def test_birth_date_with_age(self) -> None:
        born, age = utils.parse_birth_date("2001년/12월/12일 (25세)")
        self.assertEqual(born, dt.date(2001, 12, 12))
        self.assertEqual(age, 25)

    def test_game_date_raises_on_garbage(self) -> None:
        # 날짜는 스냅샷의 키이므로 조용히 넘기지 않고 예외를 던져야 한다.
        with self.assertRaises(ValueError):
            utils.parse_game_date("내년 봄")

    def test_days_between(self) -> None:
        self.assertEqual(utils.days_between("2027-01-01", "2027-03-02"), 60)
        self.assertIsNone(utils.days_between(None, "2027-03-02"))


class TestMisc(unittest.TestCase):
    def test_fallback_id_is_stable_and_prefixed(self) -> None:
        first = utils.fallback_player_id(["Sam Johnson", "2009-04-07", "ENG"])
        second = utils.fallback_player_id(["Sam Johnson", "2009-04-07", "ENG"])
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("fb-"))

    def test_fallback_id_differs_by_input(self) -> None:
        self.assertNotEqual(
            utils.fallback_player_id(["Sam Johnson", "2009-04-07", "ENG"]),
            utils.fallback_player_id(["Sam Johnson", "2009-04-08", "ENG"]),
        )

    def test_safe_mean_and_stdev(self) -> None:
        self.assertEqual(utils.safe_mean([1, 2, 3]), 2)
        self.assertIsNone(utils.safe_mean([]))
        self.assertIsNone(utils.safe_stdev([5]))
        self.assertAlmostEqual(utils.safe_stdev([2, 4]) or 0.0, 1.0)


if __name__ == "__main__":
    unittest.main()
