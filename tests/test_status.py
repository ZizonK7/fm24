"""선수 상태(정상 / 임대 나감 / 방출) 판정 테스트.

FM export는 "이 선수가 임대 나갔다"고 말해주지 않는다. `구단` 이 임대처로
찍히는 것과, 다음 시점 명단에서 사라지는 것 두 가지가 단서의 전부다.
여기서는 그 두 단서가 화면까지 제대로 전달되는지 확인한다.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _fixtures
from src import database, importer, utils
from web import api


class StatusTestCase(unittest.TestCase):
    """1차에는 4명 전원, 2차에는 Olise가 빠진 상태로 넣는다."""

    DATE_1 = "2027-03-01"
    DATE_2 = "2027-08-01"

    #: 임대 나가 있어 `구단` 이 '번리' 로 찍히는 유스 선수.
    LOANED = "29221848"
    #: 2차 export에서 사라지는 선수.
    GONE = "29221846"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

        self.conn = database.connect(":memory:")
        self.addCleanup(self.conn.close)
        database.ensure_schema(self.conn)

        first = _fixtures.write_export(self.tmp_path, filename="s1.html")
        importer.import_export(self.conn, first, self.DATE_1)

        second = _fixtures.write_export(
            self.tmp_path,
            [_fixtures.ROW_NYONI, _fixtures.ROW_NO_ID, _fixtures.ROW_LOANED_YOUTH],
            filename="s2.html",
        )
        importer.import_export(self.conn, second, self.DATE_2)

    def status_of(self, player_id: str, game_date: str | None = None) -> str:
        players = api.squad(self.conn, game_date)["players"]
        row = next(p for p in players if p["player_id"] == player_id)
        return row["status"]


class TestParentClub(StatusTestCase):
    def test_parent_club_is_remembered_per_date(self) -> None:
        self.assertEqual(database.parent_club(self.conn, self.DATE_1), "Liverpool")
        self.assertEqual(database.parent_club(self.conn, self.DATE_2), "Liverpool")

    def test_parent_club_is_backfilled_from_snapshots(self) -> None:
        # 이 테이블이 생기기 전에 넣은 DB를 흉내낸다. 기록이 없어도 저장된
        # 스냅샷의 과반 구단으로 되살아나야 한다.
        self.conn.execute("DELETE FROM squad_meta")
        self.conn.commit()
        self.assertEqual(database.parent_club(self.conn, self.DATE_2), "Liverpool")

    def test_majority_rule_ignores_a_mixed_list(self) -> None:
        # 스카우트 결과처럼 구단이 제각각이면 모구단을 단정하지 않는다.
        self.assertIsNone(utils.majority_club(["A", "B", "C"]))
        self.assertEqual(utils.majority_club(["A", "A", "B"]), "A")
        self.assertIsNone(utils.majority_club([]))


class TestLoanedOut(StatusTestCase):
    def test_player_at_another_club_is_loaned_out(self) -> None:
        self.assertEqual(self.status_of(self.LOANED), "loaned_out")

    def test_players_at_the_parent_club_are_active(self) -> None:
        self.assertEqual(self.status_of("29221847"), "active")

    def test_loan_destination_is_reported(self) -> None:
        players = api.squad(self.conn)["players"]
        row = next(p for p in players if p["player_id"] == self.LOANED)
        self.assertEqual(row["club"], "번리")

    def test_unknown_parent_club_leaves_everyone_active(self) -> None:
        # 모구단을 모르면 임대로 단정하지 않는다. 잘못 숨기는 것이 더 나쁘다.
        self.assertEqual(api._squad_status("번리", None), "active")
        self.assertEqual(api._squad_status(None, "Liverpool"), "active")


class TestReleased(StatusTestCase):
    def test_missing_player_is_released(self) -> None:
        self.assertEqual(self.status_of(self.GONE), "released")

    def test_released_player_keeps_his_last_snapshot(self) -> None:
        players = api.squad(self.conn)["players"]
        row = next(p for p in players if p["player_id"] == self.GONE)
        # 숫자는 마지막으로 확인된 시점 기준이다.
        self.assertEqual(row["last_seen_date"], self.DATE_1)
        self.assertIsNotNone(row["quality"])

    def test_he_was_active_at_the_earlier_date(self) -> None:
        # 과거 시점을 보면 그때 기준으로 판정해야 한다.
        self.assertEqual(self.status_of(self.GONE, self.DATE_1), "active")

    def test_history_is_not_deleted(self) -> None:
        detail = api.player_detail(self.conn, self.GONE)
        assert detail is not None
        self.assertEqual(detail["player"]["status"], "released")
        self.assertTrue(detail["timeline"])

    def test_departed_query_returns_one_row_per_player(self) -> None:
        rows = database.departed_snapshots(self.conn, self.DATE_2)
        self.assertEqual([row["player_id"] for row in rows], [self.GONE])


class TestAvailability(StatusTestCase):
    def test_squad_contains_everyone_regardless_of_status(self) -> None:
        # 걸러내는 일은 화면이 한다. API는 전부 넘긴다.
        players = api.squad(self.conn)["players"]
        self.assertEqual(len(players), 4)
        self.assertEqual(
            {p["status"] for p in players}, {"active", "loaned_out", "released"}
        )

    def test_recommendation_uses_available_players_only(self) -> None:
        recommendation = api.squad(self.conn)["recommendation"]
        picked = {
            slot["player"]["player_id"]
            for slots in recommendation["squads"].values()
            for slot in slots
            if slot.get("player")
        }
        # 임대 나갔거나 떠난 선수는 지금 못 쓴다.
        self.assertNotIn(self.LOANED, picked)
        self.assertNotIn(self.GONE, picked)


if __name__ == "__main__":
    unittest.main()
