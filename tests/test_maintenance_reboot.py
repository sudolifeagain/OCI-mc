import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts.graceful_shutdown import stop_server
from scripts.maintenance_reboot import parse_player_count, prepare_reboot
from utils.backup_fingerprint import (
    backup_is_acceptable,
    compute_fingerprint,
    load_fingerprints,
    update_fingerprint,
)


class BackupFingerprintTests(unittest.TestCase):
    def test_recent_backup_is_acceptable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            world = root / "world"
            world.mkdir()
            (world / "level.dat").write_bytes(b"current")
            fingerprints = root / "fingerprints.json"

            update_fingerprint(
                fingerprints,
                "paper",
                str(root),
                ["world"],
                now=1_000,
            )
            (world / "level.dat").write_bytes(b"changed-after-backup")

            self.assertTrue(
                backup_is_acceptable(
                    fingerprints,
                    "paper",
                    str(root),
                    ["world"],
                    max_age_seconds=300,
                    now=1_200,
                )
            )

    def test_stale_unchanged_backup_is_acceptable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            world = root / "world"
            world.mkdir()
            (world / "level.dat").write_bytes(b"unchanged")
            fingerprints = root / "fingerprints.json"
            fingerprints.write_text(
                json.dumps({"paper": compute_fingerprint(str(root), ["world"])}),
                encoding="utf-8",
            )

            self.assertTrue(
                backup_is_acceptable(
                    fingerprints,
                    "paper",
                    str(root),
                    ["world"],
                    max_age_seconds=1,
                    now=10_000,
                )
            )

    def test_stale_changed_backup_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            world = root / "world"
            world.mkdir()
            level = world / "level.dat"
            level.write_bytes(b"before")
            fingerprints = root / "fingerprints.json"
            update_fingerprint(
                fingerprints,
                "paper",
                str(root),
                ["world"],
                now=1_000,
            )
            level.write_bytes(b"after")

            self.assertFalse(
                backup_is_acceptable(
                    fingerprints,
                    "paper",
                    str(root),
                    ["world"],
                    max_age_seconds=100,
                    now=2_000,
                )
            )

    def test_fingerprint_file_is_structured_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "world").mkdir()
            fingerprints = root / "fingerprints.json"
            update_fingerprint(fingerprints, "paper", str(root), ["world"], now=42)

            entry = load_fingerprints(fingerprints)["paper"]
            self.assertEqual(entry["updated_at"], 42)
            if os.name == "posix":
                self.assertEqual(fingerprints.stat().st_mode & 0o777, 0o600)


class MaintenanceRebootTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_player_count(self) -> None:
        self.assertEqual(
            parse_player_count("There are 3 of a max of 20 players online"),
            3,
        )
        self.assertIsNone(parse_player_count("unexpected"))

    async def test_automatic_stop_refuses_active_players(self) -> None:
        client = AsyncMock()
        client.execute.return_value = (
            True,
            "There are 1 of a max of 20 players online",
        )
        config = {
            "port": 25565,
            "rcon_port": 25575,
            "rcon_password_env": "PAPER_RCON_PASSWORD",
        }
        with (
            patch("scripts.graceful_shutdown.port_is_open", return_value=True),
            patch("utils.rcon.get_rcon_client", return_value=client),
        ):
            stopped = await stop_server(
                "paper",
                config,
                allow_active_players=False,
            )

        self.assertFalse(stopped)
        client.execute.assert_awaited_once_with("list")

    async def test_prepare_defers_when_a_player_is_online(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "servers": {
                            "paper": {
                                "cwd": str(root),
                                "port": 25565,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                force=True,
                reboot_required=root / "reboot-required",
                config=config,
                state=root / "desired.json",
                fingerprints=root / "fingerprints.json",
                backup_max_age=300,
                dry_run=False,
                marker=root / "pending.json",
                ready=root / "reboot-ready",
            )

            with (
                patch("scripts.maintenance_reboot.port_is_open", return_value=True),
                patch(
                    "scripts.maintenance_reboot.get_player_count",
                    new=AsyncMock(return_value=1),
                ),
                patch("scripts.maintenance_reboot.send_discord_notification"),
                patch(
                    "scripts.maintenance_reboot.stop_server",
                    new=AsyncMock(),
                ) as stop,
            ):
                result = await prepare_reboot(args)

            self.assertEqual(result, 0)
            self.assertFalse(args.marker.exists())
            self.assertFalse(args.ready.exists())
            stop.assert_not_awaited()

    async def test_prepare_hands_reboot_to_privileged_finalizer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "servers": {
                            "paper": {
                                "cwd": str(root),
                                "port": 25565,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = SimpleNamespace(
                force=True,
                reboot_required=root / "reboot-required",
                config=config,
                state=root / "desired.json",
                fingerprints=root / "fingerprints.json",
                backup_max_age=300,
                dry_run=False,
                marker=root / "pending.json",
                ready=root / "reboot-ready",
            )

            with (
                patch("scripts.maintenance_reboot.port_is_open", return_value=True),
                patch(
                    "scripts.maintenance_reboot.get_player_count",
                    new=AsyncMock(return_value=0),
                ),
                patch("scripts.maintenance_reboot.ensure_backups"),
                patch("scripts.maintenance_reboot.send_discord_notification"),
                patch(
                    "scripts.maintenance_reboot.stop_server",
                    new=AsyncMock(return_value=True),
                ) as stop,
            ):
                result = await prepare_reboot(args)

            self.assertEqual(result, 0)
            self.assertTrue(args.marker.exists())
            self.assertEqual(args.ready.read_text(encoding="utf-8"), "ready\n")
            stop.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
