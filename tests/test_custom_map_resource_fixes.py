import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.custom_map_resource_fixes import apply_fixes


class ResourceFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / "slot"
        self.server.mkdir()
        self.marker = self.server / "world/marker"
        self.marker.parent.mkdir()
        self.marker.write_bytes(b"known map")
        self.function = self.server / "world/function.mcfunction"
        self.function.write_bytes(b"say valid\nschedule function missing:page 9s\n")
        self.scratch = self.server / "world/scratch"
        self.scratch.mkdir()
        (self.scratch / "old.txt").write_bytes(b"unused")
        self.plan_path = self.root / "plan.json"
        def sha(value: bytes) -> str:
            return hashlib.sha256(value).hexdigest()
        self.original = self.function.read_bytes()
        self.fixed = b"say valid\n"
        self.plan = {"rules": [{"id": "known-map", "version": "1.16.5", "marker": {"path": "world/marker", "sha256": sha(self.marker.read_bytes())}, "operations": [
            {"action": "replace", "path": "world/function.mcfunction", "sha256": sha(self.original), "result_sha256": sha(self.fixed), "replacements": [{"before": "schedule function missing:page 9s\n", "after": ""}]},
            {"action": "quarantine", "path": "world/scratch", "files": {"old.txt": sha(b"unused")}},
        ]}]}
        self.save_plan()

    def save_plan(self) -> None:
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def apply(self) -> list[str]:
        return apply_fixes(self.server, "1.16.5", self.plan_path)

    def test_preserves_progress_and_originals_and_is_idempotent(self) -> None:
        progress = self.server / "world/scoreboard.dat"
        progress.write_bytes(b"in-progress")
        self.assertEqual(len(self.apply()), 2)
        self.assertEqual(self.function.read_bytes(), self.fixed)
        self.assertEqual(progress.read_bytes(), b"in-progress")
        self.assertFalse(self.scratch.exists())
        backups = list((self.server / ".resource-fixes").glob("known-map/*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "world/function.mcfunction").read_bytes(), self.original)
        self.assertEqual((backups[0] / "world/scratch/old.txt").read_bytes(), b"unused")
        self.assertEqual(self.apply(), [])
        self.assertEqual(len(list((self.server / ".resource-fixes").glob("known-map/*"))), 1)

    def test_checks_all_files_before_mutating_any(self) -> None:
        (self.scratch / "unexpected.txt").write_bytes(b"new upstream content")
        with self.assertRaisesRegex(ValueError, "内容が異なる"):
            self.apply()
        self.assertEqual(self.function.read_bytes(), self.original)
        self.assertFalse((self.server / ".resource-fixes").exists())

    def test_rejects_modified_function(self) -> None:
        self.function.write_bytes(b"custom content")
        with self.assertRaisesRegex(ValueError, "SHA-256が異なる"):
            self.apply()
        self.assertTrue(self.scratch.exists())

    def test_tree_hash_quarantine_requires_the_complete_original_directory(self) -> None:
        item = self.plan["rules"][0]["operations"][1]
        item["tree_sha256"] = hashlib.sha256(json.dumps(item.pop("files"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        self.save_plan()
        (self.scratch / "extra.txt").write_bytes(b"not in the approved tree")
        with self.assertRaisesRegex(ValueError, "内容が異なる"):
            self.apply()
        (self.scratch / "extra.txt").unlink()
        self.assertEqual(len(self.apply()), 2)

    def test_skips_other_maps_and_versions(self) -> None:
        self.assertEqual(apply_fixes(self.server, "1.21", self.plan_path), [])
        self.marker.write_bytes(b"another map")
        self.assertEqual(self.apply(), [])
        self.assertEqual(self.function.read_bytes(), self.original)

    def test_rejects_path_outside_slot(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_bytes(b"untouched")
        self.plan["rules"][0]["operations"][1]["path"] = "../outside.txt"
        self.save_plan()
        with self.assertRaisesRegex(ValueError, "外を参照"):
            self.apply()
        self.assertEqual(outside.read_bytes(), b"untouched")
        self.assertEqual(self.function.read_bytes(), self.original)

    def test_creates_overlay_idempotently_and_preserves_conflicting_files(self) -> None:
        overlay = {"action": "create", "path": "world/datapacks/repair/pack.mcmeta", "content": "patch", "result_sha256": hashlib.sha256(b"patch").hexdigest()}
        self.plan["rules"][0]["operations"].append(overlay)
        self.save_plan()
        self.assertEqual(len(self.apply()), 3)
        target = self.server / overlay["path"]
        self.assertEqual(target.read_bytes(), b"patch")
        self.assertEqual(self.apply(), [])
        target.write_bytes(b"user edit")
        with self.assertRaisesRegex(ValueError, "別のリソース"):
            self.apply()
        self.assertEqual(target.read_bytes(), b"user edit")


if __name__ == "__main__":
    unittest.main()
