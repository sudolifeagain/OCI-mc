import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.update_forge_mods import checked, install, read_plan, rollback


class Response(io.BytesIO):
    url = "https://example.org/mod.jar"


class ForgeModUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.server = Path(self.temp.name)
        (self.server / "mods").mkdir()
        (self.server / "mods/old.jar").write_bytes(b"old")
        (self.server / "mods/unrelated.jar").write_bytes(b"unrelated")
        self.backup = self.server / "backup"
        self.plan = {
            "schema_version": 1,
            "install": [{"file": "new.jar", "sha256": hashlib.sha256(b"new").hexdigest(),
                         "url": "https://example.org/mod.jar"}],
            "retire": [{"file": "old.jar", "sha256": hashlib.sha256(b"old").hexdigest()}],
        }

    def apply(self, payload: bytes = b"new") -> int:
        with patch("scripts.update_forge_mods.urllib.request.urlopen", return_value=Response(payload)):
            return install(self.server, self.backup, self.plan)

    def test_install_finalize_idempotence_and_rollback(self) -> None:
        self.assertEqual(self.apply(), 2)
        self.assertFalse((self.server / "mods/old.jar").exists())
        self.assertEqual((self.server / "mods/new.jar").read_bytes(), b"new")
        self.assertEqual((self.server / "mods/unrelated.jar").read_bytes(), b"unrelated")
        rollback(self.server, self.backup)
        self.assertEqual((self.server / "mods/old.jar").read_bytes(), b"old")
        self.assertFalse((self.server / "mods/new.jar").exists())
        self.apply()
        (self.backup / "pending.json").unlink()
        with patch("scripts.update_forge_mods.urllib.request.urlopen") as fetch:
            self.assertEqual(install(self.server, self.backup, self.plan), 0)
            fetch.assert_not_called()

    def test_corrupt_download_never_changes_mods(self) -> None:
        with self.assertRaises(ValueError):
            self.apply(b"corrupt")
        self.assertEqual((self.server / "mods/old.jar").read_bytes(), b"old")
        self.assertFalse((self.backup / "pending.json").exists())
        self.assertFalse((self.server / "mods/new.jar").exists())

    def test_unknown_source_and_post_install_change_fail_closed(self) -> None:
        (self.server / "mods/old.jar").write_bytes(b"unknown")
        with self.assertRaises(ValueError):
            self.apply()
        (self.server / "mods/old.jar").write_bytes(b"old")
        self.apply()
        (self.server / "mods/new.jar").write_bytes(b"changed by operator")
        with self.assertRaises(ValueError):
            rollback(self.server, self.backup)
        self.assertFalse((self.server / "mods/old.jar").exists())

    def test_interrupted_install_journal_restores_original_files(self) -> None:
        with patch("scripts.update_forge_mods.atomic_copy", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.apply()
        self.assertTrue((self.backup / "pending.json").exists())
        rollback(self.server, self.backup)
        self.assertEqual((self.server / "mods/old.jar").read_bytes(), b"old")
        self.assertFalse((self.server / "mods/new.jar").exists())

    def test_invalid_names_and_hashes_are_rejected(self) -> None:
        for name in ("../outside.jar", "bad.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checked(self.server / "mods", name)
        self.plan["install"][0]["sha256"] = "not-a-hash"
        plan_file = self.server / "plan.json"
        plan_file.write_text(json.dumps(self.plan))
        with self.assertRaises(ValueError):
            read_plan(plan_file)
