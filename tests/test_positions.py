"""포지션 문자열 파싱과 파일명 날짜 추론 테스트."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config, utils


class TestPositionTokens(unittest.TestCase):
    def test_simple(self) -> None:
        self.assertEqual(utils.parse_position_tokens("D (C)"), {"D(C)"})
        self.assertEqual(utils.parse_position_tokens("GK"), {"GK"})

    def test_multiple_sides_expand(self) -> None:
        self.assertEqual(utils.parse_position_tokens("D (RL)"), {"D(R)", "D(L)"})

    def test_slash_expands_bases(self) -> None:
        self.assertEqual(
            utils.parse_position_tokens("M/AM (RC)"),
            {"M(R)", "M(C)", "AM(R)", "AM(C)"},
        )

    def test_comma_separated_chunks(self) -> None:
        self.assertEqual(
            utils.parse_position_tokens("DM, M (C), AM (LC)"),
            {"DM", "M(C)", "AM(L)", "AM(C)"},
        )

    def test_real_export_strings(self) -> None:
        # 실제 export에서 관찰된 표기들이 전부 토큰을 만들어야 한다.
        for text in (
            "M/AM (RC)", "D (RL)", "D (C)", "D (RC), DM", "DM, M (C)",
            "M (L), AM (RL)", "D (LC), WB/AM (L)", "M/AM (R), ST (C)",
            "DM, M (C), AM (RLC)", "GK",
        ):
            with self.subTest(text=text):
                self.assertTrue(utils.parse_position_tokens(text))

    def test_missing_returns_empty(self) -> None:
        for text in ("", "-", None):
            with self.subTest(text=text):
                self.assertEqual(utils.parse_position_tokens(text), set())


class TestPositionGroups(unittest.TestCase):
    def test_centre_back(self) -> None:
        self.assertEqual(utils.position_groups("D (C)"), ["CB"])

    def test_fullback_covers_both_sides(self) -> None:
        self.assertEqual(utils.position_groups("D (RL)"), ["FB"])

    def test_multi_position_player_has_several_groups(self) -> None:
        groups = utils.position_groups("DM, M (C), AM (LC)")
        self.assertEqual(set(groups), {"DM", "CM", "AM", "W"})

    def test_every_group_has_core_attributes(self) -> None:
        # POSITION_GROUPS에 새 그룹을 추가하고 CORE_ATTRIBUTES를 빠뜨리면
        # quality가 조용히 fallback으로 넘어간다. 그걸 막는다.
        for group in set(config.POSITION_GROUPS.values()):
            with self.subTest(group=group):
                self.assertIn(group, config.CORE_ATTRIBUTES)
                self.assertTrue(config.CORE_ATTRIBUTES[group])

    def test_core_attributes_reference_known_keys(self) -> None:
        known = set(config.attribute_keys())
        for group, keys in config.CORE_ATTRIBUTES.items():
            for key in keys:
                with self.subTest(group=group, key=key):
                    self.assertIn(key, known)

    def test_every_group_has_a_label(self) -> None:
        for group in set(config.POSITION_GROUPS.values()):
            self.assertIn(group, config.POSITION_GROUP_LABELS)


class TestFilenameDate(unittest.TestCase):
    def test_yymmdd(self) -> None:
        self.assertEqual(utils.infer_date_from_filename("270424.html"), dt.date(2027, 4, 24))

    def test_yyyymmdd(self) -> None:
        self.assertEqual(utils.infer_date_from_filename("20270424.html"), dt.date(2027, 4, 24))

    def test_separators(self) -> None:
        for name in ("2027-04-24.html", "2027_04_24.html", "27-04-24.html"):
            with self.subTest(name=name):
                self.assertEqual(utils.infer_date_from_filename(name), dt.date(2027, 4, 24))

    def test_full_path(self) -> None:
        self.assertEqual(
            utils.infer_date_from_filename(r"C:\fm\data\raw\270424.html"), dt.date(2027, 4, 24)
        )

    def test_no_date_returns_none(self) -> None:
        for name in ("players.html", "제목없음.html", "squad.html"):
            with self.subTest(name=name):
                self.assertIsNone(utils.infer_date_from_filename(name))

    def test_impossible_date_returns_none(self) -> None:
        self.assertIsNone(utils.infer_date_from_filename("279999.html"))

    def test_century_is_configurable(self) -> None:
        self.assertEqual(
            utils.infer_date_from_filename("270424.html", century=2100), dt.date(2127, 4, 24)
        )


class TestRoleMapping(unittest.TestCase):
    def test_every_mapped_role_is_valid(self) -> None:
        for status, role in config.PLAYING_TIME_ROLE_MAP.items():
            with self.subTest(status=status):
                self.assertIn(role, config.SQUAD_ROLES)

    def test_covers_statuses_seen_in_real_export(self) -> None:
        observed = {
            "주전 선수", "중요 선수", "비주전 선수", "뛰어난 후보", "선수단 선수",
            "후보", "어린 선수", "미래의 유망주", "눈부신 유망주", "비상 후보", "잉여 자원",
        }
        missing = observed - set(config.PLAYING_TIME_ROLE_MAP)
        self.assertEqual(missing, set(), f"매핑 누락: {missing}")


if __name__ == "__main__":
    unittest.main()
