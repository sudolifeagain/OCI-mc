import json
from pathlib import Path
import struct
import tempfile
import unittest

from scripts.audit_custom_map_content import assert_verified_content, audit_world, click_commands, command_root, datapack_errors, is_player_ui, player_ui_commands, region_chunks, requires_operator
from utils.custom_maps.nbt import read_nbt_bytes


class ContentAuditTests(unittest.TestCase):
    def test_signs_do_not_require_player_op(self) -> None:
        event = {"clickEvent": {"action": "run_command", "value": "/setblock 1 2 3 stone"}}
        sign = {"id": "minecraft:sign", "Text1": json.dumps(event)}
        self.assertEqual(player_ui_commands(sign, "sign"), [])
        for command in ('setblock 1 2 3 oak_sign{Text1:xx}', 'give @p oak_sign[block_entity_data={id:"minecraft:sign"}]', 'execute as @a run give @s acacia_sign{BlockEntityTag:{Text1:xx}}'):
            self.assertFalse(is_player_ui(command))
        self.assertTrue(is_player_ui('execute as @a run give @s written_book{pages:[]}'))
        self.assertEqual(command_root('execute if entity @a[nbt={SelectedItem:{tag:{text:"run tellraw"}}}] run setblock 1 2 3 stone'), "setblock")
        self.assertTrue(is_player_ui('execute @a ~ ~ ~ tellraw @s {"text":"hello"}'))
        self.assertTrue(is_player_ui('execute @a ~ ~ ~ detect ~ ~-1 ~ stone 0 tellraw @a {"text":"hello"}'))
        self.assertFalse(requires_operator('/trigger menu set 1'))
        self.assertTrue(requires_operator('/execute run trigger menu set 1'))

    def test_book_formats_and_chat_extract_the_complete_command(self) -> None:
        command = '/execute as @s run summon marker ~ ~ ~ {Tags:["one"]}'
        event = {"clickEvent": {"action": "run_command", "value": command}}
        page = json.dumps(event)
        old = {"id": "minecraft:written_book", "tag": {"pages": [page]}}
        new = {"id": "minecraft:written_book", "components": {"minecraft:written_book_content": {"pages": [{"raw": {"click_event": {"action": "run_command", "command": command}}}]}}}
        for book in (old, new):
            self.assertEqual({r["command"] for r in player_ui_commands(book, "chest")}, {command})
        self.assertEqual(click_commands('tellraw @a ' + page), {command})
        self.assertEqual(click_commands("give @p written_book{pages:['" + page.replace("\\", "\\\\") + "']}"), {command})
        self.assertEqual(click_commands('give @p written_book[written_book_content={pages:[{raw:{click_event:{action:"run_command",command:"/function map:start"}}}]}]'), {"/function map:start"})

    def test_log_errors_are_required_and_vanilla_argument_warnings_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "latest.log"
            with self.assertRaises(RuntimeError):
                datapack_errors(log)
            log.write_text('[main/WARN]: Ambiguity between arguments [teleport]\n[Worker-Main-1/ERROR]: Failed to load function map:start\n[main/WARN]: ResourcePack: ignored non-lowercase namespace: Backup\n[Server thread/ERROR]: Recoverable errors when loading section: unknown biome\n[Server thread/ERROR]: Block-attached entity at invalid position\n[main/WARN]: Missing data pack fabric\n[main/WARN]: Missing data pack file/required.zip\n')
            self.assertEqual(len(datapack_errors(log)), 3)

    def test_verification_rejects_unacknowledged_privileged_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            server = Path(temporary)
            (server / "logs").mkdir()
            (server / "logs/latest.log").write_text('[Server thread/INFO]: Done\n')
            pack = server / "world/datapacks/map"
            function = pack / "data/map/function/menu.mcfunction"
            function.parent.mkdir(parents=True)
            (pack / "pack.mcmeta").write_text('{"pack":{"pack_format":61,"description":"test"}}')
            function.write_text('tellraw @a {"clickEvent":{"action":"run_command","value":"/function map:start"}}\n')
            item = {"id": "one", "mc_version": "1.21.4", "revision": "1", "op_required": False}
            with self.assertRaisesRegex(RuntimeError, "OP権限"):
                assert_verified_content(item, server)
            item["op_required"] = True
            self.assertTrue(assert_verified_content(item, server)["content_permissions_verified"])
            (server / "logs/latest.log").write_text('[Worker-Main-1/ERROR]: Failed to load function map:start\n')
            with self.assertRaisesRegex(RuntimeError, "データパック"):
                assert_verified_content(item, server)

    def test_uncompressed_region_nbt_is_read(self) -> None:
        # TAG_Compound root {marker: TAG_String("book")} の最小フィクスチャ。
        raw = b'\x0a\x00\x00\x08\x00\x06marker\x00\x04book\x00'
        self.assertEqual(read_nbt_bytes(raw), {"marker": "book"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "r.0.0.mca"
            locations = bytearray(4096)
            locations[:4] = b'\x00\x00\x02\x01'
            path.write_bytes(locations + bytes(4096) + struct.pack('>I', len(raw) + 1) + b'\x03' + raw)
            self.assertEqual(list(region_chunks(path)), [{"marker": "book"}])

    def test_missing_world_and_legacy_functions_cannot_bypass_permission_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            world = Path(temporary) / "world"
            item = {"id": "old", "mc_version": "1.12.2", "revision": "1"}
            self.assertTrue(audit_world(item, world)["scan_errors"])
            function = world / "data/functions/map/menu.mcfunction"
            function.parent.mkdir(parents=True)
            function.write_text('execute @a ~ ~ ~ tellraw @s {"clickEvent":{"action":"run_command","value":"/gamemode 2"}}\n')
            report = audit_world(item, world)
            self.assertEqual(report["scan_errors"], [])
            self.assertEqual(report["privileged_commands"], ["/gamemode 2"])


if __name__ == "__main__":
    unittest.main()
