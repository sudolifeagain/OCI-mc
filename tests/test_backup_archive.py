import asyncio
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from utils.backup_archive import (
    build_archive_command,
    create_server_archive,
    create_zip_archive,
)


class BackupArchiveTests(unittest.TestCase):
    def test_create_zip_archive_contains_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir, "server")
            world_dir = Path(base_dir, "world", "region")
            world_dir.mkdir(parents=True)
            Path(base_dir, "world", "level.dat").write_bytes(b"level")
            Path(world_dir, "r.0.0.mca").write_bytes(b"region")
            archive_path = Path(temp_dir, "backup.zip")

            create_zip_archive(str(base_dir), ["world"], str(archive_path))

            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    ["world/level.dat", "world/region/r.0.0.mca"],
                )

    def test_create_zip_archive_rejects_parent_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir, "server")
            base_dir.mkdir()
            archive_path = Path(temp_dir, "backup.zip")

            with self.assertRaisesRegex(ValueError, "サーバーディレクトリ外"):
                create_zip_archive(str(base_dir), ["../outside"], str(archive_path))

    @unittest.skipIf(os.name == "nt", "Windowsではシンボリックリンク権限が一定でない")
    def test_create_zip_archive_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir, "server")
            world_dir = Path(base_dir, "world")
            world_dir.mkdir(parents=True)
            outside = Path(temp_dir, "secret")
            outside.write_text("secret", encoding="utf-8")
            Path(world_dir, "secret-link").symlink_to(outside)
            archive_path = Path(temp_dir, "backup.zip")

            with self.assertRaisesRegex(ValueError, "シンボリックリンク"):
                create_zip_archive(str(base_dir), ["world"], str(archive_path))

    def test_build_archive_command_uses_server_user(self) -> None:
        with patch("utils.backup_archive.shutil.which", return_value="/usr/bin/sudo"):
            command = build_archive_command(
                "mc-paper",
                "/opt/minecraft/paper",
                ["world"],
                "/tmp/backup.zip",
            )

        self.assertEqual(command[:7], [
            "/usr/bin/sudo",
            "-n",
            "-H",
            "-u",
            "mc-paper",
            "--",
            os.sys.executable,
        ])
        self.assertEqual(command[-2:], ["--root", "world"])

    def test_create_server_archive_runs_in_process_without_server_user(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir, "server")
            Path(base_dir, "world").mkdir(parents=True)
            Path(base_dir, "world", "level.dat").write_bytes(b"level")
            archive_path = Path(temp_dir, "backup.zip")

            asyncio.run(
                create_server_archive(
                    str(base_dir),
                    ["world"],
                    str(archive_path),
                    None,
                )
            )

            self.assertTrue(zipfile.is_zipfile(archive_path))


if __name__ == "__main__":
    unittest.main()
