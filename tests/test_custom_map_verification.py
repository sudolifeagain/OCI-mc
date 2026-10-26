import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from scripts.verify_custom_maps import assert_saved_score, clients, verification_players


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    def test_namespaced_scoreboard_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            namespaced = root / "world/data/minecraft/scoreboard.dat"
            namespaced.parent.mkdir(parents=True)
            namespaced.touch()
            (root / "world/data/scoreboard.dat").touch()
            score = {"data": {"PlayerScores": [{"Name": "OCI_QA1", "Objective": "oci_qa", "Score": 117}]}}
            with patch("scripts.verify_custom_maps.read_nbt", return_value=score) as read:
                assert_saved_score(root)
            read.assert_called_once_with(namespaced)

    def test_legacy_scoreboard_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "world/data/scoreboard.dat"
            legacy.parent.mkdir(parents=True)
            legacy.touch()
            score = {"Data": {"PlayerScores": [{"Name": "OCI_QA1", "Objective": "oci_qa", "Score": 117}]}}
            with patch("scripts.verify_custom_maps.read_nbt", return_value=score) as read:
                assert_saved_score(root)
            read.assert_called_once_with(legacy)

    def test_invalid_namespaced_score_cannot_pass_using_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            namespaced = root / "world/data/minecraft/scoreboard.dat"
            namespaced.parent.mkdir(parents=True)
            namespaced.touch()
            legacy = root / "world/data/scoreboard.dat"
            legacy.touch()

            def read_score(path: Path) -> dict:
                value = 117 if path == legacy else 0
                return {"data": {"PlayerScores": [{"Name": "OCI_QA1", "Objective": "oci_qa", "Score": value}]}}

            with patch("scripts.verify_custom_maps.read_nbt", side_effect=read_score) as read:
                with self.assertRaisesRegex(RuntimeError, "検証スコア117"):
                    assert_saved_score(root)
            read.assert_called_once_with(namespaced)

    def test_saved_score_requires_expected_player_objective_and_value(self) -> None:
        invalid_records = [
            {"Name": "another-player", "Objective": "oci_qa", "Score": 117},
            {"Name": "OCI_QA1", "Objective": "another-objective", "Score": 117},
            {"Name": "OCI_QA1", "Objective": "oci_qa", "Score": 116},
            {"Name": "OCI_QA1", "Objective": "oci_qa"},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "world/data/scoreboard.dat"
            legacy.parent.mkdir(parents=True)
            legacy.touch()
            invalid_data = [{"data": {"PlayerScores": [record]}} for record in invalid_records]
            invalid_data.extend([{"data": {"PlayerScores": []}}, {"data": {}}, {}])
            for score in invalid_data:
                with self.subTest(score=score), patch("scripts.verify_custom_maps.read_nbt", return_value=score):
                    with self.assertRaisesRegex(RuntimeError, "検証スコア117"):
                        assert_saved_score(root)

    def test_author_player_limits_are_respected(self) -> None:
        for minimum, maximum, expected in ((2, 2, 2), (1, 3, 3), (2, 4, 4), (1, 10, 4)):
            with self.subTest(minimum=minimum, maximum=maximum):
                self.assertEqual(verification_players({"min_players": minimum, "max_players": maximum}), expected)
        for minimum, maximum in ((1, 1), (5, 10), (3, 2)):
            with self.subTest(minimum=minimum, maximum=maximum), self.assertRaises(ValueError):
                verification_players({"min_players": minimum, "max_players": maximum})

    async def test_two_player_map_checks_both_expected_players(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "launch.json").write_text(json.dumps({"version": "1.20.4"}))
            server = SimpleNamespace(cwd=temporary, port=25568, config={}, get_stats=lambda: None)
            for response, passes in (("OCI_QA1, OCI_QA2", True), ("OCI_QA1", False)):
                with self.subTest(response=response):
                    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{"joined": ["OCI_QA1", "OCI_QA2"]}', b"")))
                    rcon = SimpleNamespace(execute=AsyncMock(return_value=(True, response)))
                    with patch("scripts.verify_custom_maps.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=process) as spawn, patch("scripts.verify_custom_maps.get_rcon_client", return_value=rcon), patch("scripts.verify_custom_maps.asyncio.sleep", new_callable=AsyncMock):
                        if passes:
                            evidence = await clients("node", server, first=False, players=2)
                            self.assertEqual(evidence["expected_players"], 2)
                            self.assertTrue(evidence["rcon_players"])
                            self.assertNotIn("rcon_four_players", evidence)
                        else:
                            with self.assertRaisesRegex(RuntimeError, "2人同時接続"):
                                await clients("node", server, first=False, players=2)
                    self.assertEqual(spawn.call_args.args[-1], "2")


if __name__ == "__main__":
    unittest.main()
