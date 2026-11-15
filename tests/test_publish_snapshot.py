"""Regression tests for scheduled refresh when a league has no matches or its feed is unavailable."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from scripts import publish_snapshot as publisher


class RefreshSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / "data").mkdir()
        self.patches = [
            patch.object(publisher, "ROOT", root),
            patch.object(publisher, "current_seasons", return_value={
                "spain": "2026-27", "hungary": "2026-27",
            }),
            patch.object(publisher, "get_coverage", return_value={"fixtures_available": True, "scheduled": 0}),
            patch.object(publisher, "persisted_games", return_value=[{"id": "spain:1"}, {"id": "hungary:1"}]),
            patch.object(publisher.db, "games", side_effect=lambda league, season:
                         [{"id": "hungary:1", "ft_home": 2, "status": "finished"}] if league == "hungary" else []),
            patch.object(publisher.db, "upsert", return_value={"added": 0, "updated": 0, "unchanged": 1}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.root = root

    def test_no_new_matches_is_successful(self):
        """No games this week does not mean an empty or broken season feed."""
        with patch.object(publisher, "sync_games", side_effect=lambda league, season: [
            {"id": f"{league}:old", "status": "finished"}
        ]):
            publisher.refresh_sources()
        sources = json.loads((self.root / "data/current.json").read_text())["sources"]
        self.assertTrue(all(source["ok"] and not source["stale"] for source in sources))
        self.assertTrue(all(source["coverage"]["scheduled"] == 0 for source in sources))

    def test_one_unavailable_league_uses_saved_data_without_blocking_others(self):
        def fetch(league, season):
            if league == "hungary":
                raise publisher.ProviderError("Fallo temporal del calendario.")
            return [{"id": "spain:new", "status": "finished"}]
        with patch.object(publisher, "sync_games", side_effect=fetch):
            publisher.refresh_sources()
        snapshot = json.loads((self.root / "data/current.json").read_text())
        self.assertEqual(snapshot["sources"][1]["league"], "hungary")
        self.assertFalse(snapshot["sources"][1]["ok"])
        self.assertTrue(snapshot["sources"][1]["stale"])
        self.assertEqual(snapshot["sources"][1]["saved"], 1)
        publisher.db.upsert.assert_called_once_with([{"id": "spain:new", "status": "finished"}])

    def test_all_feeds_unavailable_still_fails_safely(self):
        with patch.object(publisher, "sync_games", side_effect=publisher.ProviderError("Sin conexión")):
            with self.assertRaisesRegex(RuntimeError, "Ninguna fuente"):
                publisher.refresh_sources()
        publisher.db.upsert.assert_not_called()

    def test_missing_saved_season_still_fails_safely(self):
        publisher.db.games.side_effect = lambda league, season: []
        def fetch(league, season):
            if league == "hungary":
                raise publisher.ProviderError("Sin calendario")
            return [{"id": "spain:1"}]
        with patch.object(publisher, "sync_games", side_effect=fetch):
            with self.assertRaisesRegex(RuntimeError, "sin datos guardados"):
                publisher.refresh_sources()
        publisher.db.upsert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
