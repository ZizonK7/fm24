"""표 정렬용 표시 폭 계산 테스트.

한글은 터미널에서 두 칸을 차지한다. ``len()`` 으로 정렬하면 한국어 라벨이
섞인 표가 어긋나므로 :func:`src.utils.display_width` 를 쓴다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import utils


class TestDisplayWidth(unittest.TestCase):
    def test_ascii_is_one_column_each(self) -> None:
        self.assertEqual(utils.display_width("passing"), 7)

    def test_hangul_is_two_columns_each(self) -> None:
        self.assertEqual(utils.display_width("패스"), 4)
        self.assertEqual(utils.display_width("순간 속도"), 9)  # 한글 4자 + 공백

    def test_mixed(self) -> None:
        # 한글 2자(4) + 공백(1) + "(passing)"(9)
        self.assertEqual(utils.display_width("패스 (passing)"), 14)


class TestPad(unittest.TestCase):
    def test_pads_to_exact_display_width(self) -> None:
        for text in ("패스", "passing", "순간 속도", "1군", ""):
            with self.subTest(text=text):
                self.assertEqual(utils.display_width(utils.pad(text, 16)), 16)

    def test_right_align(self) -> None:
        self.assertTrue(utils.pad("1군", 8, "right").startswith("    "))
        self.assertTrue(utils.pad("1군", 8, "right").endswith("1군"))

    def test_clips_when_too_long(self) -> None:
        clipped = utils.pad("DM, M (C), AM (LC)", 12)
        self.assertEqual(utils.display_width(clipped), 12)
        self.assertTrue(clipped.rstrip().endswith("…"))

    def test_clips_hangul_without_overflow(self) -> None:
        # 두 칸짜리 글자가 경계에 걸려도 폭을 넘지 않아야 한다.
        for width in range(3, 12):
            with self.subTest(width=width):
                self.assertEqual(
                    utils.display_width(utils.pad("전진형 플레이메이커", width)), width
                )


if __name__ == "__main__":
    unittest.main()
