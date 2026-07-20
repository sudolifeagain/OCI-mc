import io
import os
import tarfile
import tempfile
import unittest
import zipfile

from cogs.backup_system import (
    _extract_tar_safely,
    _extract_zip_safely,
    _replace_backup_roots,
    _validate_archive_path,
)


class BackupArchiveSecurityTests(unittest.TestCase):
    def test_archive_path_is_limited_to_backup_roots(self) -> None:
        _validate_archive_path("/srv/paper", "world/region/r.0.0.mca", {"world"})
        with self.assertRaises(ValueError):
            _validate_archive_path("/srv/paper", "../bot/.env", {"world"})
        with self.assertRaises(ValueError):
            _validate_archive_path("/srv/paper", "/world/level.dat", {"world"})
        with self.assertRaises(ValueError):
            _validate_archive_path("/srv/paper", "server.properties", {"world"})

    def test_zip_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "backup.zip")
            info = zipfile.ZipInfo("world/link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, "/etc/passwd")

            with self.assertRaises(ValueError):
                _extract_zip_safely(archive_path, temp_dir, {"world"})

    def test_tar_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "backup.tar")
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo("world/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                archive.addfile(info, io.BytesIO())

            with self.assertRaises(ValueError):
                _extract_tar_safely(archive_path, temp_dir, {"world"})

    def test_validated_world_replaces_existing_world(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = os.path.join(temp_dir, "server")
            stage_dir = os.path.join(base_dir, ".rollback-stage")
            os.makedirs(os.path.join(base_dir, "world"))
            os.makedirs(os.path.join(stage_dir, "world"))
            with open(os.path.join(base_dir, "world", "level.dat"), "wb") as file_obj:
                file_obj.write(b"old")
            with open(os.path.join(stage_dir, "world", "level.dat"), "wb") as file_obj:
                file_obj.write(b"new")

            replaced = _replace_backup_roots(stage_dir, base_dir, ["world"])

            self.assertEqual(replaced, 1)
            with open(os.path.join(base_dir, "world", "level.dat"), "rb") as file_obj:
                self.assertEqual(file_obj.read(), b"new")

    def test_archive_without_configured_roots_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = os.path.join(temp_dir, "server")
            stage_dir = os.path.join(base_dir, ".rollback-stage")
            os.makedirs(stage_dir)
            with self.assertRaises(ValueError):
                _replace_backup_roots(stage_dir, base_dir, ["world"])


if __name__ == "__main__":
    unittest.main()
