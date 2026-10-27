import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts import verify_custom_maps


class VerificationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_player_count_records_failure_and_continues(self) -> None:
        items = [
            {"id": "unsupported", "min_players": 5, "max_players": 10},
            {"id": "valid-three", "min_players": 2, "max_players": 3},
            {"id": "valid-two", "min_players": 2, "max_players": 2},
        ]
        for item in items:
            item.update(mc_version="1.20.4", revision="qa")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"custom_maps": {"root": str(root)}, "servers": {}}))
            for item in items[1:]:
                template = root / "templates" / item["id"] / item["revision"]
                (template / "world").mkdir(parents=True)
                (template / "world" / "level.dat").write_bytes(b"unchanged original")
                (template / "manifest.json").write_text(json.dumps({"smoke_verified": False}))

            rooms = {}

            async def create_room(guild: int, channel: int, owner: int, map_id: str, name: str, players: int, session: dict | None = None) -> dict:
                room = {"id": f"room-{map_id}", "map_id": map_id, "map": {"requirements_hash": map_id}, "generation": 0}
                rooms[room["id"]] = room
                return room

            async def start(room_id: str, actor: str, generation: int) -> dict:
                return {"id": f"session-{rooms[room_id]['map_id']}", "slot": "custom-1", "control_generation": 0}

            async def pause(ident: str, actor: str, finish: bool = False) -> dict:
                return {"id": ident, "slot": None, "control_generation": 1}

            service = SimpleNamespace(
                slots=["custom-1"],
                catalog=SimpleNamespace(items={item["id"]: item for item in items}),
                store=SimpleNamespace(put=Mock()),
                create_room=AsyncMock(side_effect=create_room),
                membership=AsyncMock(side_effect=lambda ident, *args: rooms[ident]),
                start=AsyncMock(side_effect=start),
                pause=AsyncMock(side_effect=pause),
                poll=AsyncMock(),
                delete=AsyncMock(),
                rows=AsyncMock(return_value=[]),
                _session_path=lambda ident: root / "sessions" / ident,
            )
            manager = SimpleNamespace(
                get_server=Mock(return_value=SimpleNamespace(cwd=str(root / "empty-slot"))),
                is_running=Mock(return_value=False),
                stop_server=AsyncMock(),
            )
            args = SimpleNamespace(
                allow_production_maintenance=True,
                catalog=root / "candidates.json",
                trace_rcon=False,
                map=None,
                only_unverified=False,
                parallel=False,
                integration=False,
                node="unused-node",
            )

            def verification_path(value: str | Path) -> Path:
                return config_path if value == "config.json" else Path(value)

            with contextlib.ExitStack() as patches:
                patches.enter_context(patch.object(verify_custom_maps, "Path", side_effect=verification_path))
                patches.enter_context(patch.object(verify_custom_maps.subprocess, "run", return_value=SimpleNamespace(returncode=3)))
                patches.enter_context(patch.object(verify_custom_maps.psutil, "process_iter", return_value=[]))
                patches.enter_context(patch.object(verify_custom_maps, "MultiServerManager", return_value=manager))
                patches.enter_context(patch.object(verify_custom_maps, "VerificationService", return_value=service))
                clients = patches.enter_context(patch.object(verify_custom_maps, "clients", new_callable=AsyncMock, return_value={"rcon_players": True}))
                patches.enter_context(patch.object(verify_custom_maps, "assert_saved_score"))
                patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
                await verify_custom_maps.verify(args)

            results_path, = (root / "qa").glob("run-*/results.json")
            results = {row["map_id"]: row for row in json.loads(results_path.read_text())}
            self.assertFalse(results["unsupported"]["passed"])
            self.assertIn("2～4人", results["unsupported"]["error"])
            self.assertEqual([call.args[3] for call in service.create_room.await_args_list], ["valid-three", "valid-three", "valid-two", "valid-two"])
            self.assertEqual([call.kwargs["players"] for call in clients.await_args_list], [3, 3, 2, 2])
            for item in items[1:]:
                with self.subTest(map_id=item["id"]):
                    evidence = results[item["id"]]
                    self.assertTrue(evidence["passed"])
                    self.assertTrue(evidence["save_resume"])
                    self.assertTrue(evidence["original_unchanged"])
                    self.assertEqual(evidence["expected_players"], item["max_players"])
                    manifest_path = root / "templates" / item["id"] / item["revision"] / "manifest.json"
                    self.assertTrue(json.loads(manifest_path.read_text())["smoke_verified"])


if __name__ == "__main__":
    unittest.main()
