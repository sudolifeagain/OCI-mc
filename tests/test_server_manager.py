import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from utils.server_manager import MultiServerManager, ServerInstance


class ServerInstanceCommandTests(unittest.TestCase):
    def test_default_java_command(self) -> None:
        server = ServerInstance(
            "paper",
            {
                "jar": "paper.jar",
                "cwd": "/opt/minecraft/paper",
                "memory": "4G",
            },
        )

        self.assertEqual(
            server._build_start_command(),
            ["java", "-Xmx4G", "-Xms4G", "-jar", "paper.jar", "nogui"],
        )

    def test_configured_java_command(self) -> None:
        server = ServerInstance(
            "paper",
            {
                "jar": "paper.jar",
                "java_command": "/usr/lib/jvm/java-25-openjdk-arm64/bin/java",
                "cwd": "/opt/minecraft/paper",
                "memory": "4G",
            },
        )

        self.assertEqual(
            server._build_start_command(),
            [
                "/usr/lib/jvm/java-25-openjdk-arm64/bin/java",
                "-Xmx4G",
                "-Xms4G",
                "-jar",
                "paper.jar",
                "nogui",
            ],
        )

    def test_script_command_ignores_java_command(self) -> None:
        server = ServerInstance(
            "forge",
            {
                "use_script": "./run.sh",
                "java_command": "/unused/java",
                "cwd": "/opt/minecraft/forge",
            },
        )

        self.assertEqual(server._build_start_command(), ["./run.sh", "nogui"])

    def test_run_as_user_uses_non_interactive_sudo_on_posix(self) -> None:
        server = ServerInstance(
            "paper",
            {
                "jar": "paper.jar",
                "cwd": "/opt/minecraft/paper",
                "run_as_user": "mc-paper",
            },
        )

        with patch("utils.server_manager.os.name", "posix"):
            command = server._build_start_command()

        self.assertEqual(
            command[:7],
            ["/usr/bin/sudo", "-n", "-H", "-u", "mc-paper", "--", "java"],
        )

    def test_child_environment_excludes_bot_secrets(self) -> None:
        env = {
            "HOME": "/home/ubuntu",
            "PATH": "/usr/bin",
            "DISCORD_TOKEN": "secret",
            "NOTION_TOKEN": "secret",
            "PAPER_RCON_PASSWORD": "secret",
            "UNRELATED": "value",
        }
        with patch.dict(os.environ, env, clear=True):
            child_env = ServerInstance._build_child_environment()

        self.assertEqual(child_env, {"HOME": "/home/ubuntu", "PATH": "/usr/bin"})

    def test_log_queue_is_bounded_and_drops_oldest_line(self) -> None:
        server = ServerInstance(
            "paper",
            {
                "jar": "paper.jar",
                "cwd": "/opt/minecraft/paper",
                "log_queue_size": 2,
            },
        )

        server._enqueue_log("one")
        server._enqueue_log("two")
        server._enqueue_log("three")

        self.assertEqual(server.log_queue.qsize(), 2)
        self.assertEqual(server.log_queue.get_nowait(), ("paper", "two"))
        self.assertEqual(server.log_queue.get_nowait(), ("paper", "three"))
        self.assertEqual(server.dropped_log_lines, 1)


class ServerManagerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_uses_rcon(self) -> None:
        server = ServerInstance(
            "paper",
            {
                "jar": "paper.jar",
                "cwd": "/opt/minecraft/paper",
                "rcon_port": 25575,
                "rcon_password_env": "PAPER_RCON_PASSWORD",
            },
        )
        process = SimpleNamespace(returncode=None)
        server.process = process
        server._generation = 1
        client = SimpleNamespace(execute=AsyncMock(return_value=(True, "There are 0")))

        with patch("utils.server_manager.get_rcon_client", return_value=client):
            ready = await server._wait_until_ready(process, 1)

        self.assertTrue(ready)
        client.execute.assert_awaited_once_with("list")

    async def test_concurrent_start_only_spawns_once(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            manager = MultiServerManager(
                {"paper": {"jar": "paper.jar", "cwd": "/opt/minecraft/paper"}},
                runtime_dir,
            )
            server = manager.servers["paper"]
            state = {"running": False, "starts": 0}

            def is_running() -> bool:
                return state["running"]

            async def start() -> bool:
                state["starts"] += 1
                await asyncio.sleep(0)
                state["running"] = True
                return True

            server.is_running = Mock(side_effect=is_running)
            server.start = AsyncMock(side_effect=start)
            manager.check_memory_for_start = Mock(return_value=(True, ""))

            results = await asyncio.gather(
                manager.start_server("paper"),
                manager.start_server("paper"),
            )

            self.assertEqual(results.count(True), 1)
            self.assertEqual(state["starts"], 1)
            self.assertEqual(manager.get_desired_servers(), ["paper"])

    async def test_stop_refuses_when_rcon_is_unavailable_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            manager = MultiServerManager(
                {
                    "paper": {
                        "jar": "paper.jar",
                        "cwd": "/opt/minecraft/paper",
                        "rcon_port": 25575,
                        "rcon_password_env": "MISSING_RCON_PASSWORD",
                    }
                },
                runtime_dir,
            )
            server = manager.servers["paper"]
            server.stop = AsyncMock(return_value=True)

            with patch("utils.server_manager.get_rcon_client", return_value=None):
                self.assertFalse(await manager.stop_server("paper"))
                self.assertTrue(await manager.stop_server("paper", force=True))

            server.stop.assert_awaited_once()

    async def test_maintenance_rejects_external_lifecycle_operation(self) -> None:
        with tempfile.TemporaryDirectory() as runtime_dir:
            manager = MultiServerManager(
                {"paper": {"jar": "paper.jar", "cwd": "/opt/minecraft/paper"}},
                runtime_dir,
            )
            self.assertTrue(manager.begin_maintenance("paper"))
            self.assertFalse(await manager.start_server("paper"))
            self.assertIn("メンテナンス", manager.servers["paper"].last_error)
            manager.end_maintenance("paper")


if __name__ == "__main__":
    unittest.main()
