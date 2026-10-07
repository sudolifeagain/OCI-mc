import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_public_secrets import findings
from scripts.migrate_runtime_config import migrate
from utils.runtime_config import load_reaction_roles


class RuntimeConfigTests(unittest.TestCase):
    def test_migration_preserves_runtime_config_and_existing_private_changes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            roles = {"channel_id": 123, "mappings": {"check": {"channel_id": 456, "label": "dev"}}}
            path.write_text(json.dumps({"reaction_roles": roles}), encoding="utf-8")
            self.assertTrue(migrate(path))
            private = path.parent / "reaction_roles.local.json"
            self.assertEqual(load_reaction_roles({}, private), roles)
            roles["channel_id"] = 789
            private.write_text(json.dumps(roles), encoding="utf-8")
            self.assertFalse(migrate(path))
            self.assertEqual(load_reaction_roles({}, private), roles)

    def test_malformed_private_config_fails_without_logging_its_content(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reaction_roles.local.json"
            path.write_text('{"channel_id":"private-value"}', encoding="utf-8")
            with self.assertRaises(ValueError) as error:
                load_reaction_roles({}, path)
            self.assertNotIn("private-value", str(error.exception))

    def test_policy_detects_private_file_and_public_metadata(self) -> None:
        self.assertEqual(findings(".env.production", b""), [("private-file", 1)])
        self.assertEqual(findings(".env.example", b"DISCORD_TOKEN=your_token"), [])
        self.assertEqual(findings("config.json", b'{"reaction_roles":{"channel_id":123}}'),
                         [("private-operational-metadata", 1)])

    def test_policy_detects_keys_tokens_and_passwords_without_returning_values(self) -> None:
        payloads = [
            ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private-key"),
            ("gh" + "p_" + "A" * 36, "github-token"),
            ('RCON_PASSWORD = "' + "actual-credential" + '"', "literal-credential"),
        ]
        for payload, kind in payloads:
            self.assertIn((kind, 1), findings("config.py", payload.encode()))


if __name__ == "__main__":
    unittest.main()
