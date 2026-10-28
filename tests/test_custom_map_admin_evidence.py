import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class AdminEvidenceTests(unittest.TestCase):
    def test_publication_requires_content_and_wire_permissions_for_both_starts(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/custom_map_admin.py"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"maps": [{"id": "one", "revision": "1", "mc_version": "1.21.4", "source_url": "https://example.com/map"}]}))
            manifest = root / "templates/one/1/manifest.json"
            manifest.parent.mkdir(parents=True)
            evidence = root / "evidence.json"
            valid = {"map_id": "one", "rcon_ready": True, "save_resume": True, "multiple_players": True, "datapack_errors": [], "content_permissions_verified": True, "resumed_content": {"datapack_errors": [], "content_permissions_verified": True}, "first_clients": {"permissions_verified": True}, "resumed_clients": {"permissions_verified": True}}
            cases = []
            for key in ("datapack_errors", "content_permissions_verified", "resumed_content", "first_clients", "resumed_clients"):
                broken = copy.deepcopy(valid)
                broken.pop(key)
                cases.append(broken)
            broken = copy.deepcopy(valid)
            broken["resumed_content"]["datapack_errors"] = ["Failed to load function"]
            cases.append(broken)
            for case in [*cases, valid]:
                manifest.write_text('{"smoke_verified": false}')
                evidence.write_text(json.dumps(case))
                result = subprocess.run([sys.executable, str(script), "verify", "--root", str(root), "--catalog", str(catalog), "--map", "one", "--evidence", str(evidence)], capture_output=True, text=True, check=True)
                if case is valid:
                    self.assertTrue(json.loads(manifest.read_text())["smoke_verified"])
                else:
                    self.assertIn("error", result.stdout)
                    self.assertFalse(json.loads(manifest.read_text())["smoke_verified"])


if __name__ == "__main__":
    unittest.main()
