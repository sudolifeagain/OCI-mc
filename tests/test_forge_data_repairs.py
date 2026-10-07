import hashlib
import os
import tempfile
import shutil
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

from scripts.repair_forge_data import (
    apply_plan, decode, encode, repair_loot, repair_recipe, repair_tag, rollback,
)


class ForgeDataRepairTests(unittest.TestCase):
    def test_chest_loot_keeps_inventory_name_lock_and_function_conditions(self) -> None:
        condition = [{"condition": "minecraft:survives_explosion"}]
        data = {"functions": [{"function": "minecraft:copy_components", "source": "block_entity",
                              "include": ["minecraft:custom_name", "minecraft:container", "minecraft:lock"],
                              "conditions": condition}]}
        fixed = repair_loot(data, set())["functions"][0]
        self.assertEqual(fixed["function"], "minecraft:copy_nbt")
        self.assertEqual(fixed["conditions"], condition)
        self.assertEqual(fixed["ops"], [
            {"source": "CustomName", "target": "display.Name", "op": "replace"},
            {"source": "Items", "target": "BlockEntityTag.Items", "op": "replace"},
            {"source": "Lock", "target": "BlockEntityTag.Lock", "op": "replace"},
        ])

    def test_loot_converts_tag_predicate_and_table_reference(self) -> None:
        data = {"pools": [{"conditions": [{"condition": "minecraft:match_tool",
                                          "predicate": {"items": ["#c:tools/shears"]}}],
                           "entries": [{"type": "minecraft:loot_table", "value": "test:chests/demo"}]}]}
        fixed = repair_loot(data, set())["pools"][0]
        self.assertEqual(fixed["conditions"][0]["predicate"], {"tag": "c:tools/shears"})
        self.assertEqual(fixed["entries"][0], {"type": "minecraft:loot_table", "name": "test:chests/demo"})

    def test_potion_and_book_loot_preserve_content_in_legacy_nbt(self) -> None:
        data = {"functions": [
            {"function": "minecraft:set_components", "components": {
                "minecraft:item_name": {"text": "Wine"}, "minecraft:potion_contents": {
                    "potion": "minecraft:water", "custom_color": 123,
                    "custom_effects": [{"id": "minecraft:luck", "duration": 100, "amplifier": 2}]}}},
            {"function": "minecraft:set_written_book_pages", "pages": [{"text": "Lore"}]},
        ]}
        fixed = repair_loot(data, set())["functions"]
        self.assertEqual(fixed[0], {"function": "minecraft:set_name", "name": {"text": "Wine"}})
        self.assertIn('"Potion":"minecraft:water"', fixed[1]["tag"])
        self.assertIn('"Id":26,"Amplifier":2,"Duration":100', fixed[1]["tag"])
        self.assertIn('Lore', fixed[2]["tag"])
        self.assertIn('"resolved":1b', fixed[2]["tag"])
        with self.assertRaises(ValueError):
            repair_loot({"functions": [{"function": "minecraft:set_components",
                                        "components": {"test:unknown": {}}}]}, set())

    def test_null_tag_and_legacy_aliases_are_repaired(self) -> None:
        self.assertEqual(repair_tag(None, set()), {"values": []})
        self.assertEqual(repair_tag({"values": ["minecraft:groove"]}, set()),
                         {"values": ["minecraft:grove"]})
        self.assertEqual(repair_loot({"type": "minecraft:item", "name": "minecraft:turtle_scute"}, set()),
                         {"type": "minecraft:item", "name": "minecraft:scute"})

    def test_verified_intermediate_resource_can_be_upgraded_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            resource = "data/test/tags/items/test.json"
            intermediate = b'{"values":[]}'
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr(resource, intermediate)
            original = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": resource, "before_sha256": hashlib.sha256(b"null").hexdigest(),
                "accepted_before_sha256": [hashlib.sha256(intermediate).hexdigest()],
                "data": {"values": ["minecraft:stone"]},
            }]}
            backup = server / "backups"
            self.assertEqual(apply_plan(server, plan, backup), 1)
            self.assertEqual(apply_plan(server, plan, backup), 0)
            rollback(server, backup)
            self.assertEqual(jar.read_bytes(), original)

    def test_missing_item_recipe_is_conditional_and_keeps_ingredients(self) -> None:
        recipe = {"type": "minecraft:crafting_shaped", "key": {"A": {"item": "other:missing"}}}
        fixed = repair_recipe(recipe, "Unknown item 'other:missing'", {"other"})
        self.assertEqual(fixed["key"], recipe["key"])
        self.assertEqual(fixed["conditions"], [{"type": "forge:item_exists", "item": "other:missing"}])
        self.assertNotIn("conditions", recipe)

    def test_valid_alternative_recipe_is_unchanged(self) -> None:
        recipe = {"type": "minecraft:crafting_shaped", "result": {"item": "minecraft:stick"}}
        self.assertEqual(repair_recipe(recipe, "Unknown item 'other:missing'", set()), recipe)
        self.assertEqual(repair_recipe(recipe, "Missing type, expected to find a string", set()), recipe)

    def test_shapeless_recipe_preserves_count(self) -> None:
        recipe = {"type": "minecraft:crafting_shapeless", "key": {"A": {"item": "test:block"}},
                  "pattern": ["AA"], "result": {"item": "test:item", "count": 4}}
        fixed = repair_recipe(recipe, "Missing ingredients", {"test"})
        self.assertEqual(fixed["ingredients"], [{"item": "test:block"}] * 2)
        self.assertEqual(fixed["result"], recipe["result"])

    def test_loot_keeps_valid_fallback(self) -> None:
        data = {"type": "minecraft:alternatives", "children": [
            {"type": "minecraft:item", "name": "test:missing"},
            {"type": "minecraft:item", "name": "minecraft:glass_bottle"},
        ]}
        fixed = repair_loot(data, {"test:missing"})
        self.assertEqual(fixed["children"], [data["children"][1]])
        self.assertEqual(len(data["children"]), 2)

    def test_tag_keeps_required_valid_entries(self) -> None:
        data = {"values": ["minecraft:stone", "other:missing", {"id": "test:test:biome", "required": False}]}
        fixed = repair_tag(data, {"other:missing"})
        self.assertEqual(fixed["values"], ["minecraft:stone", {"id": "other:missing", "required": False},
                                          {"id": "test:biome", "required": False}])

    def test_json_comments_do_not_modify_quoted_urls(self) -> None:
        self.assertEqual(decode(b'{"url":"https://example.com/x", // comment\n "v":[1,],}'),
                         {"url": "https://example.com/x", "v": [1]})

    def test_archive_apply_is_idempotent_and_rollback_restores_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            original_data = b'{"values":["test:missing"]}'
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("data/test/tags/items/test.json", original_data)
                archive.writestr("test/Main.class", b"class bytes")
            original_jar = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": "data/test/tags/items/test.json",
                "before_sha256": hashlib.sha256(original_data).hexdigest(),
                "data": {"values": [{"id": "test:missing", "required": False}]},
            }]}
            backup = server / "backups"
            self.assertEqual(apply_plan(server, plan, backup, dry_run=True), 1)
            self.assertEqual(jar.read_bytes(), original_jar)
            self.assertFalse(backup.exists())
            self.assertEqual(apply_plan(server, plan, backup), 1)
            with zipfile.ZipFile(jar) as archive:
                self.assertEqual(archive.read("test/Main.class"), b"class bytes")
                self.assertEqual(archive.read(plan["changes"][0]["resource"]), encode(plan["changes"][0]["data"]))
            self.assertEqual(apply_plan(server, plan, backup), 0)
            rollback(server, backup)
            self.assertEqual(jar.read_bytes(), original_jar)

    def test_archive_source_mismatch_refuses_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("data/test/recipes/test.json", b"{}")
            original = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": "data/test/recipes/test.json",
                "before_sha256": "0" * 64, "data": {"conditions": [{"type": "forge:false"}]},
            }]}
            with self.assertRaises(ValueError):
                apply_plan(server, plan, server / "backups")
            self.assertEqual(jar.read_bytes(), original)
            self.assertFalse((server / "backups").exists())

    def test_signed_mod_is_preserved_and_datapack_can_be_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            resource = "data/test/recipes/test.json"
            original_data = b"{}"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr(resource, original_data)
                archive.writestr("META-INF/TEST.SF", b"signature")
            original = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": resource,
                "before_sha256": hashlib.sha256(original_data).hexdigest(),
                "data": {"conditions": [{"type": "forge:false"}]},
            }]}
            backup = server / "backups"
            self.assertEqual(apply_plan(server, plan, backup), 1)
            self.assertEqual(jar.read_bytes(), original)
            pack = server / "world/datapacks/oci-mc-data-compat.zip"
            with zipfile.ZipFile(pack) as archive:
                self.assertEqual(archive.read(resource), encode(plan["changes"][0]["data"]))
            self.assertEqual(apply_plan(server, plan, backup), 0)
            rollback(server, backup)
            self.assertEqual(jar.read_bytes(), original)
            self.assertFalse(pack.exists())

    def test_rollback_handles_datapack_replacement_failure(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            with zipfile.ZipFile(server / "mods/test.jar", "w") as archive:
                archive.writestr("data/test/recipes/test.json", b"{}")
                archive.writestr("META-INF/TEST.SF", b"signature")
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": "test.jar", "resource": "data/test/recipes/test.json",
                "before_sha256": hashlib.sha256(b"{}").hexdigest(),
                "data": {"conditions": [{"type": "forge:false"}]},
            }]}
            backup = server / "backups"
            real_replace = os.replace

            def fail_archive_replace(source: Path, target: Path) -> None:
                if target.name == "pending.json":
                    real_replace(source, target)
                else:
                    raise OSError("replacement failed")

            with patch("scripts.repair_forge_data.os.replace", side_effect=fail_archive_replace):
                with self.assertRaises(OSError):
                    apply_plan(server, plan, backup)
            rollback(server, backup)
            self.assertFalse((backup / "pending.json").exists())
            self.assertFalse((server / "world/datapacks/oci-mc-data-compat.zip").exists())

    def test_interrupted_restore_keeps_jar_complete_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("data/test/recipes/test.json", b"{}")
            original = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": "data/test/recipes/test.json",
                "before_sha256": hashlib.sha256(b"{}").hexdigest(),
                "data": {"conditions": [{"type": "forge:false"}]},
            }]}
            backup = server / "backups"
            apply_plan(server, plan, backup)
            repaired = jar.read_bytes()
            real_copy = shutil.copy2

            def interrupted_copy(source: Path, target: Path) -> None:
                target.write_bytes(b"partial")
                raise OSError("interrupted")

            with patch("scripts.repair_forge_data.shutil.copy2", side_effect=interrupted_copy):
                with self.assertRaises(OSError):
                    rollback(server, backup)
            self.assertEqual(jar.read_bytes(), repaired)
            self.assertIs(shutil.copy2, real_copy)
            rollback(server, backup)
            self.assertEqual(jar.read_bytes(), original)

    def test_failed_journal_write_leaves_no_pending_state_or_modified_jar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            server = Path(folder)
            (server / "mods").mkdir()
            jar = server / "mods/test.jar"
            with zipfile.ZipFile(jar, "w") as archive:
                archive.writestr("data/test/recipes/test.json", b"{}")
            original = jar.read_bytes()
            plan = {"schema_version": 1, "minecraft_version": "1.20.1", "changes": [{
                "jar": jar.name, "resource": "data/test/recipes/test.json",
                "before_sha256": hashlib.sha256(b"{}").hexdigest(),
                "data": {"conditions": [{"type": "forge:false"}]},
            }]}
            backup = server / "backups"
            with patch("scripts.repair_forge_data.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    apply_plan(server, plan, backup)
            self.assertEqual(jar.read_bytes(), original)
            self.assertFalse((backup / "pending.json").exists())
            rollback(server, backup)


if __name__ == "__main__":
    unittest.main()
