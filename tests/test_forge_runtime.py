import tempfile
import unittest
from pathlib import Path

from scripts.check_forge_runtime import validate_log


class ForgeRuntimeTests(unittest.TestCase):
    def test_ready_port_does_not_hide_data_loading_failure(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "latest.log"
            for error in ["Couldn't parse element loot_tables:test:loot",
                          "Couldn't read tag list test:tag", "Parsing error loading recipe test:item",
                          "Couldn't load advancement test:advance"]:
                path.write_text(error + '\nDone (5.0s)!\n', encoding="utf-8")
                with self.assertRaises(ValueError):
                    validate_log(path)

    def test_completed_start_with_optional_mod_diagnostic_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "latest.log"
            path.write_text("Native backend failed to load\nDone (5.0s)!\n", encoding="utf-8")
            validate_log(path)
            path.write_text("Starting Minecraft server\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_log(path)


if __name__ == "__main__":
    unittest.main()
