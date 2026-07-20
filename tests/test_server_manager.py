import unittest

from utils.server_manager import ServerInstance


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


if __name__ == "__main__":
    unittest.main()
