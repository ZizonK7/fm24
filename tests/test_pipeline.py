"""import → 누적 → 성장 계산 전체 흐름 테스트 (메모리 DB 사용).

가장 중요한 두 가지를 검증한다:

* 같은 선수를 날짜별로 **누적**하고, 직전 스냅샷 대비 delta가 맞는가
* 공식을 바꿨을 때 **원본을 다시 읽지 않고** 재계산되는가
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _fixtures
from src import config, database, growth, importer, metrics


class PipelineTestCase(unittest.TestCase):
    """두 시점의 export를 메모리 DB에 넣어둔 상태로 시작한다."""

    DATE_1 = "2027-03-01"
    DATE_2 = "2027-08-01"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

        self.conn = database.connect(":memory:")
        self.addCleanup(self.conn.close)
        database.ensure_schema(self.conn)

        first = _fixtures.write_export(self.tmp_path, filename="t1.html")
        self.summary_1 = importer.import_export(self.conn, first, self.DATE_1)

        # 두 번째 시점: Nyoni만 성장시킨다 (패스 +1, 시야 +1, 주력 +1).
        grown = _fixtures.bump_attributes(
            _fixtures.ROW_NYONI,
            {_fixtures.IDX_PASSING: 1, _fixtures.IDX_VISION: 1, _fixtures.IDX_PACE: 1},
        )
        grown[_fixtures.IDX_MINUTES] = "2400"
        second = _fixtures.write_export(
            self.tmp_path,
            [grown, _fixtures.ROW_NO_ID, _fixtures.ROW_OLISE, _fixtures.ROW_LOANED_YOUTH],
            filename="t2.html",
        )
        self.summary_2 = importer.import_export(self.conn, second, self.DATE_2)

        self.nyoni = "29221847"


class TestImportSummary(PipelineTestCase):
    def test_first_import_has_no_previous_snapshots(self) -> None:
        self.assertEqual(self.summary_1.imported, 4)
        self.assertEqual(self.summary_1.new_players, 4)
        self.assertEqual(self.summary_1.with_previous, 0)
        self.assertEqual(self.summary_1.growth_calculated, 0)

    def test_second_import_reuses_players(self) -> None:
        self.assertEqual(self.summary_2.imported, 4)
        self.assertEqual(self.summary_2.new_players, 0)
        self.assertEqual(self.summary_2.existing_players, 4)
        self.assertEqual(self.summary_2.with_previous, 4)
        self.assertEqual(self.summary_2.growth_calculated, 4)

    def test_fallback_ids_are_counted(self) -> None:
        self.assertEqual(self.summary_1.fallback_ids, 1)

    def test_summary_renders(self) -> None:
        text = self.summary_2.render()
        self.assertIn("Imported: 4 players", text)
        self.assertIn("Growth calculated: 4", text)
        self.assertIn("Signed (영입): 1", text)


class TestSnapshotAccumulation(PipelineTestCase):
    def test_two_snapshots_per_player(self) -> None:
        self.assertEqual(
            database.snapshot_dates(self.conn, self.nyoni), [self.DATE_1, self.DATE_2]
        )

    def test_players_table_tracks_observation_window(self) -> None:
        player = database.get_player(self.conn, self.nyoni)
        assert player is not None
        self.assertEqual(player["first_seen_date"], self.DATE_1)
        self.assertEqual(player["last_seen_date"], self.DATE_2)

    def test_raw_export_is_preserved(self) -> None:
        snapshot = database.get_snapshot(self.conn, self.nyoni, self.DATE_1)
        assert snapshot is not None
        # 정규화하지 않은 컬럼도 raw_json에서 꺼낼 수 있어야 한다.
        self.assertEqual(database.raw_value(snapshot, "구단"), "U21")
        self.assertEqual(database.raw_value(snapshot, "구단__2"), "Liverpool")

    def test_reimporting_same_date_replaces_not_duplicates(self) -> None:
        path = _fixtures.write_export(self.tmp_path, filename="again.html")
        summary = importer.import_export(self.conn, path, self.DATE_1)
        self.assertEqual(summary.replaced, 4)
        self.assertEqual(len(database.snapshot_dates(self.conn, self.nyoni)), 2)

    def test_out_of_order_import_keeps_window(self) -> None:
        earlier = _fixtures.write_export(self.tmp_path, filename="t0.html")
        importer.import_export(self.conn, earlier, "2026-08-01")
        player = database.get_player(self.conn, self.nyoni)
        assert player is not None
        self.assertEqual(player["first_seen_date"], "2026-08-01")
        self.assertEqual(player["last_seen_date"], self.DATE_2)


class TestGrowth(PipelineTestCase):
    def test_per_attribute_deltas(self) -> None:
        rows = {
            row["attribute"]: row["delta"]
            for row in database.get_growth(self.conn, self.nyoni, self.DATE_2)
        }
        self.assertEqual(rows["passing"], 1.0)
        self.assertEqual(rows["vision"], 1.0)
        self.assertEqual(rows["pace"], 1.0)
        self.assertEqual(rows["decisions"], 0.0)

    def test_aggregate_totals(self) -> None:
        stored = database.get_metrics(self.conn, self.nyoni, self.DATE_2)
        self.assertEqual(stored["attribute_growth_total"], 3.0)
        self.assertEqual(stored["technical_growth"], 1.0)  # 패스
        self.assertEqual(stored["mental_growth"], 1.0)  # 시야
        self.assertEqual(stored["physical_growth"], 1.0)  # 주력
        self.assertEqual(stored["attributes_improved"], 3.0)

    def test_no_growth_for_unchanged_player(self) -> None:
        stored = database.get_metrics(self.conn, "29221846", self.DATE_2)
        self.assertEqual(stored["attribute_growth_total"], 0.0)

    def test_growth_between_arbitrary_dates(self) -> None:
        result = growth.growth_between(self.conn, self.nyoni, self.DATE_1, self.DATE_2)
        assert result is not None
        self.assertEqual(result.total, 3.0)
        self.assertEqual(len(result.changed()), 3)

    def test_growth_per_year_is_normalised_by_days(self) -> None:
        result = growth.growth_between(self.conn, self.nyoni, self.DATE_1, self.DATE_2)
        assert result is not None
        days = result.aggregates["growth_days"]
        assert days is not None
        self.assertAlmostEqual(
            result.aggregates["attribute_growth_per_year"] or 0.0, 3.0 * 365 / days, places=6
        )


class TestMetrics(PipelineTestCase):
    def test_quality_uses_positional_core_attributes_only(self) -> None:
        # Olise는 M/AM (RC) → AM/CM 그룹. 그 그룹의 핵심 능력치는
        # 패스19·시야18·기술18·침착18·판단17 다섯 개이고 평균은 18.0.
        # 주력14·순간속도15·몸싸움14는 이 자리의 핵심이 아니라 빠진다.
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertAlmostEqual(stored["quality"], 18.0, places=6)

        attributes = database.get_attributes(self.conn, "29221846", self.DATE_1)
        flat_mean = sum(attributes.values()) / len(attributes)
        self.assertNotAlmostEqual(stored["quality"], flat_mean, places=2)

    def test_quality_differs_by_position_for_same_attributes(self) -> None:
        """같은 능력치라도 포지션이 다르면 quality가 달라야 한다."""
        attributes = database.get_attributes(self.conn, "29221846", self.DATE_1)
        as_winger = metrics.positional_quality(attributes, "AM (RL)")
        as_playmaker = metrics.positional_quality(attributes, "M (C)")
        self.assertIsNotNone(as_winger)
        self.assertIsNotNone(as_playmaker)
        # 패스/시야가 높고 속도는 평범하므로 플레이메이커 쪽이 높아야 한다.
        self.assertGreater(as_playmaker, as_winger)

    def test_best_position_group_is_recorded(self) -> None:
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertIn(stored["best_position_group"], ("AM", "CM"))

    def test_usage_is_relative_to_squad_max_minutes(self) -> None:
        # 1차 시점의 최대 출장시간은 Olise의 2326분.
        stored = database.get_metrics(self.conn, self.nyoni, self.DATE_1)
        self.assertAlmostEqual(stored["usage"], 1600 / 2326, places=6)
        self.assertEqual(database.get_metrics(self.conn, "29221846", self.DATE_1)["usage"], 1.0)

    def test_usage_start_share(self) -> None:
        stored = database.get_metrics(self.conn, self.nyoni, self.DATE_1)
        self.assertAlmostEqual(stored["usage_start_share"], 18 / 34, places=6)

    def test_ceiling_adds_age_headroom_for_young_player(self) -> None:
        stored = database.get_metrics(self.conn, self.nyoni, self.DATE_1)
        self.assertGreater(stored["ceiling"], stored["quality"])

    def test_ceiling_equals_quality_at_peak_age(self) -> None:
        # Olise는 25세로 PEAK_AGE(24)를 넘었으므로 가산이 없다.
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertAlmostEqual(stored["ceiling"], stored["quality"], places=6)

    def test_experimental_scores_are_stored(self) -> None:
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        for name in ("talent_score", "balance_score", "floor_score"):
            with self.subTest(metric=name):
                self.assertIn(name, stored)

    def test_talent_score_uses_top_positional_attributes(self) -> None:
        # 핵심 능력치 상위 3개: 패스19, 시야18, 기술18 (또는 침착18) → 18.33
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertAlmostEqual(stored["talent_score"], (19 + 18 + 18) / 3, places=6)

    def test_floor_score_is_minimum_of_positional_core(self) -> None:
        # 핵심 능력치 중 최솟값은 판단 17. 몸싸움 14는 이 자리의 핵심이
        # 아니므로 하한을 끌어내리지 않는다.
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertEqual(stored["floor_score"], 17.0)


class TestStarterReadiness(PipelineTestCase):
    """'주전으로 쓸 만한가' — 절대 점수가 아니라 경쟁자 대비."""

    def test_rank_is_within_position_group(self) -> None:
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        depth = stored["position_depth"]
        self.assertGreaterEqual(stored["position_rank"], 1)
        self.assertLessEqual(stored["position_rank"], depth)

    def test_best_player_in_group_has_non_negative_gap(self) -> None:
        # Olise가 Nyoni보다 모든 핵심 능력치가 높으므로 같은 그룹에서 1위여야 한다.
        stored = database.get_metrics(self.conn, "29221846", self.DATE_1)
        self.assertEqual(stored["position_rank"], 1.0)
        self.assertGreaterEqual(stored["starter_gap"], 0.0)

    def test_weaker_player_has_negative_gap(self) -> None:
        stored = database.get_metrics(self.conn, self.nyoni, self.DATE_1)
        self.assertLess(stored["starter_gap"], 0.0)

    def test_gap_is_difference_from_best_rival(self) -> None:
        cohort = metrics.CohortStats.build(self.conn, self.DATE_1)
        mine = database.get_metrics(self.conn, self.nyoni, self.DATE_1)
        group = mine["best_position_group"]
        attributes = database.get_attributes(self.conn, self.nyoni, self.DATE_1)
        snapshot = database.get_snapshot(self.conn, self.nyoni, self.DATE_1)
        assert snapshot is not None
        quality = metrics.positional_quality(attributes, snapshot["position"], [group])
        best = cohort.best_other(group, self.nyoni)
        self.assertAlmostEqual(mine["starter_gap"], quality - best, places=6)

    def test_no_rival_in_group_means_no_comparison(self) -> None:
        cohort = metrics.CohortStats.build(self.conn, self.DATE_1)
        sole = [g for g, members in cohort.group_quality.items() if len(members) == 1]
        self.assertTrue(sole, "1명뿐인 포지션 그룹이 픽스처에 있어야 한다")
        for group in sole:
            player_id = cohort.group_quality[group][0][0]
            # 경쟁자가 없으면 비교 대상이 없다 → gap 계산은 0으로 처리된다.
            self.assertIsNone(cohort.best_other(group, player_id))

    def test_rank_is_unique_within_a_group(self) -> None:
        cohort = metrics.CohortStats.build(self.conn, self.DATE_1)
        for group, members in cohort.group_quality.items():
            ranks = [cohort.rank_in(group, pid) for pid, _ in members]
            self.assertEqual(sorted(ranks), list(range(1, len(members) + 1)), group)


class TestPlayerOrigin(PipelineTestCase):
    """'내가 데려온 선수' 구분."""

    def test_signed_player_is_detected(self) -> None:
        origin = database.get_player_origin(self.conn, "29221846")
        assert origin is not None
        self.assertEqual(origin["origin"], "signed")
        self.assertEqual(origin["signed_from"], "FC 바이에른")
        self.assertEqual(origin["signed_fee"], 168_000_000_000)

    def test_youth_player_is_detected(self) -> None:
        origin = database.get_player_origin(self.conn, self.nyoni)
        assert origin is not None
        self.assertEqual(origin["origin"], "youth")

    def test_loaned_out_youth_is_not_mistaken_for_a_signing(self) -> None:
        # 임대 나가면 구단이 임대처로 찍힌다. 모구단과 비교하지 않으면
        # '이전 구단 != 현재 구단'이 참이 돼 영입으로 잘못 잡힌다.
        origin = database.get_player_origin(self.conn, "29221848")
        assert origin is not None
        self.assertEqual(origin["origin"], "youth")

    def test_parent_club_is_the_majority_club(self) -> None:
        from src import parser

        path = _fixtures.write_export(self.tmp_path, filename="parent.html")
        result = parser.parse_export(path)
        self.assertEqual(importer.detect_parent_club(result.players), "Liverpool")

    def test_free_transfer_counts_as_signed(self) -> None:
        fields = {"club": "Liverpool", "previous_club": "아약스", "last_transfer_fee": None}
        origin, signed_from, fee = importer.classify_origin(fields, "Liverpool")
        self.assertEqual(origin, "signed")
        self.assertEqual(signed_from, "아약스")
        self.assertIsNone(fee)

    def test_manual_origin_survives_reimport(self) -> None:
        database.set_player_origin(self.conn, self.nyoni, "signed", manual=True)
        self.conn.commit()
        path = _fixtures.write_export(self.tmp_path, filename="again2.html")
        importer.import_export(self.conn, path, "2027-09-01")
        origin = database.get_player_origin(self.conn, self.nyoni)
        assert origin is not None
        self.assertEqual(origin["origin"], "signed")

    def test_summary_counts_origins(self) -> None:
        self.assertEqual(self.summary_1.signed_players, 1)
        self.assertEqual(self.summary_1.youth_players, 3)


class TestAutoRoles(PipelineTestCase):
    """FM의 `실제 출전 시간` → starter/rotation/development 자동 매핑."""

    def test_fm_status_becomes_role(self) -> None:
        self.assertEqual(database.get_role(self.conn, "29221846", self.DATE_1), "starter")
        self.assertEqual(database.get_role(self.conn, self.nyoni, self.DATE_1), "development")

    def test_summary_counts_auto_roles(self) -> None:
        self.assertEqual(self.summary_1.roles_auto, 4)

    def test_manual_role_is_not_overwritten_by_import(self) -> None:
        importer.apply_roles(
            self.conn,
            [{"player_id": self.nyoni, "game_date": "2027-09-01", "role": "starter"}],
        )
        path = _fixtures.write_export(self.tmp_path, filename="again3.html")
        importer.import_export(self.conn, path, "2027-09-01")
        # FM은 '어린 선수'(development)라고 하지만 사람이 넣은 값이 이긴다.
        self.assertEqual(database.get_role(self.conn, self.nyoni, "2027-09-01"), "starter")

    def test_unmapped_fm_status_is_skipped(self) -> None:
        self.assertIsNone(config.PLAYING_TIME_ROLE_MAP.get("없는 상태"))


class TestRecompute(PipelineTestCase):
    def test_recompute_reproduces_same_values(self) -> None:
        before = database.get_metrics(self.conn, self.nyoni, self.DATE_2)
        importer.recompute_all(self.conn)
        after = database.get_metrics(self.conn, self.nyoni, self.DATE_2)
        self.assertEqual(before["quality"], after["quality"])
        self.assertEqual(before["attribute_growth_total"], after["attribute_growth_total"])

    def test_changing_a_formula_changes_only_derived_data(self) -> None:
        """공식을 바꿔도 원본 스냅샷은 그대로여야 한다."""
        snapshot_before = dict(database.get_snapshot(self.conn, self.nyoni, self.DATE_2))

        original = metrics.METRIC_FUNCTIONS["quality"]
        try:
            metrics.METRIC_FUNCTIONS["quality"] = lambda ctx: 42.0
            importer.recompute_all(self.conn)
            self.assertEqual(
                database.get_metrics(self.conn, self.nyoni, self.DATE_2)["quality"], 42.0
            )
        finally:
            metrics.METRIC_FUNCTIONS["quality"] = original

        snapshot_after = dict(database.get_snapshot(self.conn, self.nyoni, self.DATE_2))
        self.assertEqual(snapshot_before["minutes"], snapshot_after["minutes"])
        self.assertEqual(snapshot_before["raw_json"], snapshot_after["raw_json"])

    def test_recompute_counts(self) -> None:
        counts = importer.recompute_all(self.conn)
        self.assertEqual(counts["snapshots"], 8)  # 선수 4 × 날짜 2
        self.assertEqual(counts["growth"], 4)  # 2차 시점만 이전 스냅샷이 있다


class TestRoles(PipelineTestCase):
    def test_apply_roles_from_rows(self) -> None:
        applied, warnings = importer.apply_roles(
            self.conn,
            [{"player_id": self.nyoni, "game_date": self.DATE_2, "role": "development"}],
        )
        self.assertEqual(applied, 1)
        self.assertEqual(warnings, [])
        self.assertEqual(database.get_role(self.conn, self.nyoni, self.DATE_2), "development")

    def test_unknown_role_is_rejected(self) -> None:
        applied, warnings = importer.apply_roles(
            self.conn, [{"player_id": self.nyoni, "game_date": self.DATE_2, "role": "superstar"}]
        )
        self.assertEqual(applied, 0)
        self.assertTrue(warnings)

    def test_manual_role_beats_the_auto_label_on_the_same_date(self) -> None:
        # import가 FM 상태로 development를 붙여뒀지만 사람이 넣으면 그게 이긴다.
        self.assertEqual(database.get_role(self.conn, self.nyoni, self.DATE_1), "development")
        importer.apply_roles(
            self.conn,
            [{"player_id": self.nyoni, "game_date": self.DATE_1, "role": "rotation"}],
        )
        self.assertEqual(database.get_role(self.conn, self.nyoni, self.DATE_1), "rotation")

    def test_role_carries_forward_to_dates_without_a_label(self) -> None:
        importer.apply_roles(
            self.conn,
            [{"player_id": self.nyoni, "game_date": self.DATE_1, "role": "rotation"}],
        )
        # 두 스냅샷 사이의 날짜에는 라벨이 없으므로 직전 라벨을 쓴다.
        between = "2027-05-01"
        self.assertEqual(database.get_role(self.conn, self.nyoni, between), "rotation")

    def test_roles_csv_requires_columns(self) -> None:
        bad = self.tmp_path / "bad.csv"
        bad.write_text("player_id,role\n1,starter\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            importer.load_roles_csv(bad)

    def test_roles_csv_round_trip(self) -> None:
        good = self.tmp_path / "roles.csv"
        good.write_text(
            f"player_id,game_date,role\n{self.nyoni},{self.DATE_2},starter\n", encoding="utf-8"
        )
        applied, _ = importer.apply_roles(self.conn, importer.load_roles_csv(good))
        self.assertEqual(applied, 1)
        self.assertEqual(database.get_role(self.conn, self.nyoni, self.DATE_2), "starter")


class TestSchemaMigration(unittest.TestCase):
    def test_new_config_field_adds_column(self) -> None:
        conn = database.connect(":memory:")
        self.addCleanup(conn.close)
        database.ensure_schema(conn)

        extra = config.FieldSpec("test_only_field", ("존재하지 않는 컬럼",), "text")
        original = config.FIELD_SPECS
        try:
            config.FIELD_SPECS = (*original, extra)
            added = database.ensure_schema(conn)
            self.assertIn("test_only_field", added)
        finally:
            config.FIELD_SPECS = original

        columns = {row["name"] for row in conn.execute("PRAGMA table_info(snapshots)")}
        self.assertIn("test_only_field", columns)

    def test_ensure_schema_is_idempotent(self) -> None:
        conn = database.connect(":memory:")
        self.addCleanup(conn.close)
        database.ensure_schema(conn)
        self.assertEqual(database.ensure_schema(conn), [])


if __name__ == "__main__":
    unittest.main()
