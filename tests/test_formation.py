"""Multi-formation recommendation tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src import config, formation


class TestFormationDefinitions(unittest.TestCase):
    def test_default_and_supported_formations(self) -> None:
        self.assertEqual(formation.DEFAULT_FORMATION, "4-2-3-1")
        self.assertEqual(
            list(formation.FORMATIONS),
            ["4-2-3-1", "4-3-3 DM", "4-4-2", "3-4-2-1"],
        )

    def test_every_formation_has_valid_unique_slots(self) -> None:
        known_positions = set(config.POSITION_GROUPS)
        for formation_id, slots in formation.FORMATIONS.items():
            with self.subTest(formation=formation_id):
                self.assertEqual(len(slots), 11)
                self.assertEqual(len({slot[0] for slot in slots}), 11)
                for name, eligible, x, y in slots:
                    self.assertTrue(name)
                    self.assertTrue(set(eligible) <= known_positions)
                    self.assertTrue(0 < x < 100)
                    self.assertTrue(0 < y < 100)


class TestRecommendations(unittest.TestCase):
    @staticmethod
    def players(count: int = 40) -> list[dict[str, object]]:
        positions = list(config.POSITION_GROUPS)
        return [
            {
                "player_id": str(index),
                "name": f"Player {index}",
                "age": 20,
                "role": None,
                "primary_position": positions[index % len(positions)],
                "other_positions": positions,
            }
            for index in range(count)
        ]

    @patch.object(formation.metrics, "positional_quality", return_value=10.0)
    def test_payload_is_compatible_and_each_squad_set_is_disjoint(self, _quality) -> None:
        result = formation.recommend(self.players(), {})
        self.assertEqual(result["formation"], formation.DEFAULT_FORMATION)
        self.assertEqual(result["squads"], result["formations"][0]["squads"])
        self.assertEqual(len(result["formations"]), len(formation.FORMATIONS))

        for recommendation in result["formations"]:
            picks = [
                slot["player"]["player_id"]
                for squad in recommendation["squads"].values()
                for slot in squad
                if slot["player"]
            ]
            self.assertEqual(len(picks), 33)
            self.assertEqual(len(picks), len(set(picks)))
            self.assertTrue(all("x" in slot and "y" in slot
                                for squad in recommendation["squads"].values()
                                for slot in squad))

    def test_empty_pool_keeps_all_positions_vacant(self) -> None:
        result = formation.recommend([], {})
        for recommendation in result["formations"]:
            for squad in recommendation["squads"].values():
                self.assertEqual(len(squad), 11)
                self.assertTrue(all(slot["player"] is None for slot in squad))

    @patch.object(formation.metrics, "positional_quality", return_value=10.0)
    def test_constrained_pool_respects_eligibility_and_leaves_gaps(self, _quality) -> None:
        players = [
            {
                "player_id": player_id,
                "name": name,
                "age": 24,
                "role": None,
                "primary_position": position,
                "other_positions": [],
            }
            for player_id, name, position in (
                ("gk", "Keeper", "GK"),
                ("dc-1", "Centre Back One", "D(C)"),
                ("dc-2", "Centre Back Two", "D(C)"),
                ("st", "Striker", "ST(C)"),
            )
        ]
        slots_definition = formation.FORMATIONS["3-4-2-1"]
        squads = formation._recommend_squads(players, {}, slots_definition)
        eligible_by_slot = {slot: set(eligible) for slot, eligible, _, _ in slots_definition}
        starter = squads["starter"]

        assigned = [slot for slot in starter if slot["player"]]
        self.assertEqual(len(assigned), len(players))
        self.assertEqual(sum(slot["player"] is None for slot in starter), 7)
        self.assertTrue(all(
            slot["player"]["position"] in eligible_by_slot[slot["slot"]]
            for slot in assigned
        ))
        self.assertEqual(
            sum(slot["slot"] in {"RCB", "CB", "LCB"} and slot["player"] is None for slot in starter),
            1,
        )
        self.assertTrue(all(slot["player"] is None for slot in squads["rotation"]))
        self.assertTrue(all(slot["player"] is None for slot in squads["development"]))


if __name__ == "__main__":
    unittest.main()
