import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psutil

from scripts.graceful_shutdown import stop_server
from scripts.maintenance_reboot import (
    parse_player_count,
    prepare_reboot,
    recorded_process_is_running,
    running_servers,
)
from utils.backup_fingerprint import (
    backup_is_acceptable,
    compute_fingerprint,
    load_fingerprints,
    update_fingerprint,
)


class FakeProcess:
    def __init__(
        self,
        name: str,
        create_time: float = 1_000.0,
        children: tuple["FakeProcess", ...] = (),
        status: str = psutil.STATUS_RUNNING,
    ) -> None:
        self._name = name
        self._create_time = create_time
        self._children = list(children)
        self._status = status

    def name(self) -> str:
        return self._name

    def create_time(self) -> float:
        return self._create_time

    def children(self, recursive: bool = False) -> list["FakeProcess"]:
        return list(self._children)

    def status(self) -> str:
        return self._status


def fake_process_table(processes: dict[int, FakeProcess]):
    def lookup(pid: int) -> FakeProcess:
        if pid not in processes:
            raise psutil.NoSuchProcess(pid)
        return processes[pid]

    return patch("scripts.maintenance_reboot.psutil.Process", side_effect=lookup)


def write_pid_record(runtime_dir: Path, server_id: str, pid: int, cwd: str, create_time: float = 1_000.0) -> None:
    pids = runtime_dir / "pids"
    pids.mkdir(parents=True, exist_ok=True)
    (pids / f"{server_id}.json").write_text(
        json.dumps({"pid": pid, "create_time": create_time, "cwd": cwd}),
        encoding="utf-8",
    )


# 本番構成と同じく、paperとpaper-newはゲームポート、RCONポート、パスワードを共有する。
SHARED_PAPER_SERVERS = {
    "paper": {"cwd": "/opt/minecraft/paper", "port": 25565, "rcon_port": 25575, "rcon_password_env": "PAPER_RCON_PASSWORD"},
    "paper-new": {"cwd": "/opt/minecraft/paper-new", "port": 25565, "rcon_port": 25575, "rcon_password_env": "PAPER_RCON_PASSWORD"},
}


def shared_rcon_client() -> SimpleNamespace:
    return SimpleNamespace(execute=AsyncMock(return_value=(True, "There are 0 of a max of 20 players online:")))


class RecordedProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.runtime = Path(temp_dir.name)
        self.config = SHARED_PAPER_SERVERS["paper-new"]

    def test_sudo_wrapper_with_java_child_is_running(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper-new")
        processes = {10: FakeProcess("sudo", children=(FakeProcess("java"),))}
        with fake_process_table(processes):
            self.assertTrue(recorded_process_is_running(self.runtime, "paper-new", self.config))

    def test_reused_pid_is_rejected(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper-new", create_time=1_000.0)
        processes = {10: FakeProcess("java", create_time=2_000.0)}
        with fake_process_table(processes):
            self.assertFalse(recorded_process_is_running(self.runtime, "paper-new", self.config))

    def test_record_for_other_directory_is_rejected(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper")
        with fake_process_table({10: FakeProcess("java")}):
            self.assertFalse(recorded_process_is_running(self.runtime, "paper-new", self.config))

    def test_process_tree_without_live_java_is_rejected(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper-new")
        processes = {10: FakeProcess("sudo", children=(FakeProcess("java", status=psutil.STATUS_ZOMBIE),))}
        with fake_process_table(processes):
            self.assertFalse(recorded_process_is_running(self.runtime, "paper-new", self.config))

    def test_exited_process_and_missing_record_are_rejected(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper-new")
        with fake_process_table({}):
            self.assertFalse(recorded_process_is_running(self.runtime, "paper-new", self.config))
            self.assertFalse(recorded_process_is_running(self.runtime, "paper", SHARED_PAPER_SERVERS["paper"]))


class SharedPortTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.runtime = Path(temp_dir.name)
        for target, value in (
            ("scripts.maintenance_reboot.port_is_open", True),
            ("scripts.maintenance_reboot.find_running_servers", set()),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_shared_rcon_endpoint_uses_the_bot_process_record(self) -> None:
        write_pid_record(self.runtime, "paper-new", 10, "/opt/minecraft/paper-new")
        client = shared_rcon_client()
        with fake_process_table({10: FakeProcess("sudo", children=(FakeProcess("java"),))}), patch("scripts.maintenance_reboot.get_rcon_client", return_value=client):
            self.assertEqual(await running_servers(SHARED_PAPER_SERVERS, self.runtime), {"paper-new"})
        client.execute.assert_not_awaited()

    async def test_readable_process_cwd_identifies_the_server(self) -> None:
        with fake_process_table({}), patch("scripts.maintenance_reboot.find_running_servers", return_value={"paper"}):
            self.assertEqual(await running_servers(SHARED_PAPER_SERVERS, self.runtime), {"paper"})

    async def test_shared_rcon_endpoint_without_process_record_defers_reboot(self) -> None:
        with fake_process_table({}), patch("scripts.maintenance_reboot.get_rcon_client", return_value=shared_rcon_client()):
            with self.assertRaisesRegex(RuntimeError, "25565"):
                await running_servers(SHARED_PAPER_SERVERS, self.runtime)

    async def test_conflicting_process_records_defer_reboot(self) -> None:
        write_pid_record(self.runtime, "paper", 10, "/opt/minecraft/paper")
        write_pid_record(self.runtime, "paper-new", 11, "/opt/minecraft/paper-new")
        processes = {10: FakeProcess("java"), 11: FakeProcess("java")}
        with fake_process_table(processes), patch("scripts.maintenance_reboot.get_rcon_client", return_value=shared_rcon_client()):
            with self.assertRaises(RuntimeError):
                await running_servers(SHARED_PAPER_SERVERS, self.runtime)

    async def test_shared_game_port_uses_the_distinct_rcon_endpoint(self) -> None:
        servers = {"forge": {"cwd": "/opt/minecraft/forge", "port": 25566, "rcon_port": 25576}, "custom-1": {"cwd": "/opt/minecraft/custom-maps/slots/custom-1", "port": 25566, "rcon_port": 25578}}
        clients = {25576: SimpleNamespace(execute=AsyncMock(return_value=(True, "There are 0 players online:"))), 25578: SimpleNamespace(execute=AsyncMock(return_value=(False, "offline")))}
        with fake_process_table({}), patch("scripts.maintenance_reboot.get_rcon_client", side_effect=lambda cfg: clients[cfg["rcon_port"]]):
            self.assertEqual(await running_servers(servers, self.runtime), {"forge"})

    async def test_unidentified_shared_port_defers_reboot(self) -> None:
        servers = {"forge": {"cwd": "/opt/minecraft/forge", "port": 25566}, "custom-1": {"cwd": "/opt/minecraft/custom-maps/slots/custom-1", "port": 25566}}
        with fake_process_table({}), patch("scripts.maintenance_reboot.get_rcon_client", return_value=None):
            with self.assertRaises(RuntimeError):
                await running_servers(servers, self.runtime)


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
    def test_script_resolves_repository_imports_outside_working_directory(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "maintenance_reboot.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=temp_dir,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)

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

    async def test_dry_run_identifies_shared_port_server_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "config.json"
            config.write_text(json.dumps({"servers": SHARED_PAPER_SERVERS}), encoding="utf-8")
            runtime = root / "runtime"
            write_pid_record(runtime, "paper-new", 10, "/opt/minecraft/paper-new")
            args = SimpleNamespace(
                force=True,
                reboot_required=root / "reboot-required",
                config=config,
                state=runtime / "desired_servers.json",
                fingerprints=root / "fingerprints.json",
                backup_max_age=300,
                dry_run=True,
                marker=root / "pending.json",
                ready=root / "reboot-ready",
            )

            with (
                patch("scripts.maintenance_reboot.port_is_open", return_value=True),
                patch("scripts.maintenance_reboot.find_running_servers", return_value=set()),
                fake_process_table({10: FakeProcess("sudo", children=(FakeProcess("java"),))}),
                patch(
                    "scripts.maintenance_reboot.get_player_count",
                    new=AsyncMock(return_value=0),
                ) as player_count,
                patch("scripts.maintenance_reboot.ensure_backups") as backups,
                patch("scripts.maintenance_reboot.send_discord_notification") as notify,
                patch(
                    "scripts.maintenance_reboot.stop_server",
                    new=AsyncMock(return_value=True),
                ) as stop,
            ):
                result = await prepare_reboot(args)

            self.assertEqual(result, 0)
            player_count.assert_awaited_once_with("paper-new", SHARED_PAPER_SERVERS["paper-new"])
            self.assertEqual(backups.call_args.args[1], {"paper-new"})
            self.assertFalse(args.state.exists())
            self.assertFalse(args.marker.exists())
            self.assertFalse(args.ready.exists())
            notify.assert_not_called()
            stop.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
