import hashlib
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from utils import plugin_manager


class PluginManagerApiTests(unittest.TestCase):
    @patch("utils.plugin_manager.requests.get")
    def test_geyser_compatible_version_downloads_resolved_build(self, mock_get: Mock) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {
            "version": "2.11.3",
            "build": 1254,
            "downloads": {"spigot": {"name": "Geyser-Spigot.jar", "sha256": "abc123"}},
        }
        mock_get.return_value = response

        info = plugin_manager.get_geysermc_latest_info("geyser", "spigot", "2.11.3")

        self.assertIsNotNone(info)
        self.assertIn("/versions/2.11.3/builds/latest", mock_get.call_args.args[0])
        self.assertTrue(info["download_url"].endswith("/versions/2.11.3/builds/1254/downloads/spigot"))
        self.assertEqual(info["sha256"], "abc123")

    @patch("utils.plugin_manager.requests.get")
    def test_github_release_tag_and_digest(self, mock_get: Mock) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {
            "tag_name": "v5.16",
            "published_at": "2026-02-13T13:38:40Z",
            "assets": [
                {
                    "name": "bluemap-5.16-paper.jar",
                    "browser_download_url": "https://example.invalid/bluemap.jar",
                    "size": 123,
                    "digest": "sha256:abc123",
                }
            ],
        }
        mock_get.return_value = response

        info = plugin_manager.get_github_latest_info(
            "BlueMap-Minecraft/BlueMap",
            "bluemap-*-paper.jar",
            "v5.16",
        )

        self.assertIsNotNone(info)
        self.assertEqual(info["tag_name"], "v5.16")
        self.assertEqual(info["sha256"], "abc123")
        requested_url = mock_get.call_args.args[0]
        self.assertTrue(requested_url.endswith("/releases/tags/v5.16"))

    @patch("utils.plugin_manager.requests.get")
    def test_modrinth_version_pin_selects_compatible_release(self, mock_get: Mock) -> None:
        response = Mock(status_code=200)
        response.json.return_value = [
            {
                "version_type": "release",
                "version_number": "7.4.4",
                "date_published": "2026-07-04T03:01:16Z",
                "files": [
                    {
                        "primary": True,
                        "filename": "worldedit-bukkit-7.4.4.jar",
                        "url": "https://example.invalid/worldedit-7.4.4.jar",
                        "size": 456,
                        "hashes": {"sha1": "newer-java-hash"},
                    }
                ],
            },
            {
                "version_type": "release",
                "version_number": "7.4.2",
                "date_published": "2026-04-01T10:55:26Z",
                "files": [
                    {
                        "primary": True,
                        "filename": "worldedit-bukkit-7.4.2.jar",
                        "url": "https://example.invalid/worldedit-7.4.2.jar",
                        "size": 456,
                        "hashes": {"sha1": "def456"},
                    }
                ],
            },
        ]
        mock_get.return_value = response

        info = plugin_manager.get_modrinth_latest_info("worldedit", "paper", "1.21.11", "7.4.2")

        self.assertIsNotNone(info)
        self.assertEqual(info["version"], "7.4.2")
        self.assertEqual(info["sha1"], "def456")


class PluginManagerUpdateTests(unittest.TestCase):
    def test_geyser_update_and_check_keep_compatible_version(self) -> None:
        content = b"compatible geyser jar"
        info = {
            "version": "2.11.3", "build": 1254,
            "sha256": hashlib.sha256(content).hexdigest(),
            "download_url": "https://example.invalid/geyser.jar",
        }
        config = {
            "source": "geysermc", "project": "geyser", "platform": "spigot",
            "version": "2.11.3", "filename": "Geyser-Spigot.jar",
        }
        with tempfile.TemporaryDirectory() as plugins_dir:
            def download(url: str, destination: str) -> bool:
                with open(destination, "wb") as output:
                    output.write(content)
                return True

            with (
                patch("utils.plugin_manager.get_geysermc_latest_info", return_value=info) as latest,
                patch("utils.plugin_manager.download_file", side_effect=download),
                patch("utils.plugin_manager.parse_plugin_yml", return_value={"version": "2.11.3"}),
            ):
                self.assertTrue(plugin_manager.update_plugin(plugins_dir, config)["success"])
                latest.assert_called_with("geyser", "spigot", "2.11.3")
                plugin_manager.check_plugin_update(plugins_dir, "geyser", config)
                self.assertEqual(latest.call_count, 2)
                latest.assert_called_with("geyser", "spigot", "2.11.3")

    def test_verified_update_replaces_versioned_jar(self) -> None:
        new_content = b"verified plugin content"
        expected_sha1 = hashlib.sha1(new_content).hexdigest()

        with tempfile.TemporaryDirectory() as plugins_dir:
            old_path = os.path.join(plugins_dir, "worldedit-bukkit-7.4.0.jar")
            with open(old_path, "wb") as file_obj:
                file_obj.write(b"old plugin content")

            def fake_download(url: str, dest_path: str, timeout: int = 300) -> bool:
                with open(dest_path, "wb") as file_obj:
                    file_obj.write(new_content)
                return True

            info = {
                "name": "worldedit-bukkit-7.4.4.jar",
                "download_url": "https://example.invalid/worldedit.jar",
                "version": "7.4.4",
                "sha1": expected_sha1,
            }
            config = {
                "source": "modrinth",
                "project": "worldedit",
                "loader": "paper",
                "game_version": "1.21.11",
                "filename_pattern": "worldedit-bukkit-*.jar",
            }

            with (
                patch("utils.plugin_manager.get_modrinth_latest_info", return_value=info),
                patch("utils.plugin_manager.download_file", side_effect=fake_download),
            ):
                result = plugin_manager.update_plugin(plugins_dir, config)

            new_path = os.path.join(plugins_dir, "worldedit-bukkit-7.4.4.jar")
            self.assertTrue(result["success"])
            self.assertFalse(os.path.exists(old_path))
            self.assertTrue(os.path.exists(new_path))

    def test_checksum_failure_preserves_installed_jar(self) -> None:
        with tempfile.TemporaryDirectory() as plugins_dir:
            old_path = os.path.join(plugins_dir, "worldedit-bukkit-7.4.0.jar")
            with open(old_path, "wb") as file_obj:
                file_obj.write(b"old plugin content")

            def fake_download(url: str, dest_path: str, timeout: int = 300) -> bool:
                with open(dest_path, "wb") as file_obj:
                    file_obj.write(b"tampered content")
                return True

            info = {
                "name": "worldedit-bukkit-7.4.4.jar",
                "download_url": "https://example.invalid/worldedit.jar",
                "version": "7.4.4",
                "sha1": "0" * 40,
            }
            config = {
                "source": "modrinth",
                "project": "worldedit",
                "loader": "paper",
                "game_version": "1.21.11",
                "filename_pattern": "worldedit-bukkit-*.jar",
            }

            with (
                patch("utils.plugin_manager.get_modrinth_latest_info", return_value=info),
                patch("utils.plugin_manager.download_file", side_effect=fake_download),
            ):
                result = plugin_manager.update_plugin(plugins_dir, config)

            self.assertFalse(result["success"])
            self.assertTrue(os.path.exists(old_path))
            self.assertFalse(os.path.exists(os.path.join(plugins_dir, info["name"])))


if __name__ == "__main__":
    unittest.main()
