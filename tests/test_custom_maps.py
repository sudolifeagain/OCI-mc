import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from utils.custom_maps.artifacts import safe_extract
from utils.custom_maps.catalog import Catalog
from utils.custom_maps.service import CustomMapService
from scripts.launch_custom_map import LOGGING_CHECKSUMS, logging_arguments
from utils.custom_maps.artifacts import LEGACY_LOGGING
from utils.custom_maps.store import Store
from utils.server_manager import MultiServerManager


def fixture_item(ident: str = "one", **extra) -> dict:
    return {"id": ident, "title": ident, "source": "test", "source_url": "https://example.com/map", "mc_version": "1.21", "revision": "1", "rank": 1, "published": True, "min_players": 2, "max_players": 4, **extra}


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.json"

    def catalog(self, items: list[dict]) -> Catalog:
        self.path.write_text(json.dumps({"maps": items}))
        return Catalog(self.path)

    def test_combined_filters_and_normalization(self) -> None:
        catalog = self.catalog([fixture_item("one", title="ＡＢＣ", difficulty=2, duration_min=60, duration_max=90), fixture_item("two", difficulty=4, duration_max=90), fixture_item("three", max_players=3, difficulty=2, duration_max=90)])
        found = catalog.search({"players": 4, "difficulty_max": 3, "max_minutes": 120, "keyword": "abc"})
        self.assertEqual([x["id"] for x in found], ["one"])

    def test_unknown_duration_does_not_override_known_lower_bound(self) -> None:
        catalog = self.catalog([fixture_item("one", duration_min=150), fixture_item("two", duration_min=90)])
        found = catalog.search({"max_minutes": 120, "unknown_duration": True})
        self.assertEqual([x["id"] for x in found], ["two"])

    def test_unknown_is_last_in_both_sort_directions(self) -> None:
        catalog = self.catalog([fixture_item("unknown"), fixture_item("easy", difficulty=1), fixture_item("hard", difficulty=4)])
        self.assertEqual([x["id"] for x in catalog.search({"sort": "easy"})], ["easy", "hard", "unknown"])
        self.assertEqual([x["id"] for x in catalog.search({"sort": "hard"})], ["hard", "easy", "unknown"])

    def test_unpublished_and_unverified_are_not_results(self) -> None:
        catalog = self.catalog([fixture_item("one"), fixture_item("two", published=False)])
        self.assertEqual(catalog.search({}, available=set()), [])
        self.assertEqual([x["id"] for x in catalog.search({}, available={"one", "two"})], ["one"])


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "state.sqlite3")

    def test_cas_prevents_stale_state_overwrite(self) -> None:
        record = self.store.create("session", state="RUNNING")
        newer = self.store.put("session", {**record, "state": "PAUSED"}, expected=record["generation"])
        with self.assertRaises(ValueError):
            self.store.put("session", record, expected=record["generation"])
        self.assertEqual(self.store.get("session", record["id"]), newer)

    def test_concurrent_uuid_approval_is_unique(self) -> None:
        def approve(user: str) -> bool:
            try:
                self.store.approve_identity(1, user, "Player", "same-uuid", "admin")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, ["one", "two"]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len([x for x in self.store.list("identity", 1) if x["approved"]]), 1)

    def test_history_requires_real_presence_and_preserves_clear(self) -> None:
        self.store.create("session", guild="1", map_id="one", actual_players={}, result="clear_reported")
        self.assertEqual(self.store.played(1, 2), {})
        self.store.create("session", guild="1", map_id="one", actual_players={"2": 123.0}, result="clear_reported", state="DELETED")
        self.assertEqual(self.store.played(1, 2)["one"], {"last_played": 123.0, "cleared": True})


class ArchiveTests(unittest.TestCase):
    def test_zip_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("../outside", "bad")
            with self.assertRaises(ValueError):
                safe_extract(archive, Path(root) / "out")
            self.assertFalse((Path(root).parent / "outside").exists())

    def test_zip_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "bad.zip"
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr(info, "/etc/passwd")
            with self.assertRaises(ValueError):
                safe_extract(archive, Path(root) / "out")


class PolicyTests(unittest.TestCase):
    def manager(self, root: str) -> MultiServerManager:
        manager = MultiServerManager({"forge": {"cwd": root, "memory": "14G"}, "paper": {"cwd": root, "memory": "4G"}, "custom-1": {"cwd": root, "memory": "2G", "workload_type": "custom_map", "reservation_mb": 3072}}, root)
        for server in manager.servers.values():
            server.is_running = Mock(return_value=False)
            server._check_memory_available = Mock(return_value=(True, "", {}))
        return manager

    def test_forge_exclusion_and_preserved_desired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            manager = self.manager(root)
            manager._set_desired("forge", True)
            allowed, _ = manager.check_memory_for_start("custom-1", custom_session=True)
            self.assertFalse(allowed)
            manager._set_desired("forge", False)
            with patch("utils.server_manager.psutil.virtual_memory", return_value=SimpleNamespace(total=18 * 1024 ** 3, available=15 * 1024 ** 3)):
                self.assertTrue(manager.check_memory_for_start("custom-1", custom_session=True)[0])

    def test_generic_custom_start_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(self.manager(root).check_memory_for_start("custom-1")[0])


class LegacyLaunchTests(unittest.TestCase):
    def test_logging_workaround_checksums_match_the_installer(self) -> None:
        self.assertEqual(LOGGING_CHECKSUMS, {name: values[1] for name, values in LEGACY_LOGGING.items()})

    def test_legacy_launch_refuses_missing_security_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(FileNotFoundError):
                logging_arguments(Path(root), "1.12.2")
            self.assertEqual(logging_arguments(Path(root), "1.17.1"), ["-Dlog4j2.formatMsgNoLookups=true"])
            self.assertEqual(logging_arguments(Path(root), "1.21.4"), [])


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        path = self.root / "catalog.json"
        path.write_text(json.dumps({"maps": [fixture_item()]}))
        cfg = {f"custom-{n}": {"cwd": str(self.root / "slots" / f"custom-{n}"), "memory": "2G", "port": 25567 + n, "rcon_port": 25577 + n, "rcon_password_env": "TEST_CUSTOM_RCON", "workload_type": "custom_map"} for n in (1, 2)}
        self.manager = MultiServerManager(cfg, str(self.root / "runtime"))
        self.running = set()
        for sid, server in self.manager.servers.items():
            server.is_running = Mock(side_effect=lambda sid=sid: sid in self.running)
            server.last_stop_graceful = True
        async def start(sid: str, **kwargs) -> bool:
            self.running.add(sid)
            return True
        async def stop(sid: str, **kwargs) -> bool:
            self.running.discard(sid)
            return True
        self.manager.start_server = AsyncMock(side_effect=start)
        self.manager.stop_server = AsyncMock(side_effect=stop)
        self.manager.check_memory_for_start = Mock(return_value=(True, ""))
        self.service = CustomMapService({"root": str(self.root), "catalog": str(path), "permission_helper": False}, self.manager, self.root / "runtime")
        item = self.service.catalog.get("one")
        template = self.root / "templates" / "one" / "1"
        (template / "world").mkdir(parents=True)
        (template / "world" / "level.dat").write_bytes(b"original")
        (template / "manifest.json").write_text(json.dumps({"smoke_verified": True, "mc_version": "1.21", "catalog": item}))
        self.env = patch.dict(os.environ, {"TEST_CUSTOM_RCON": "test-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.artifact = patch("utils.custom_maps.service.ensure_vanilla", return_value={"java": 21, "sha256": "test"})
        self.artifact.start()
        self.addCleanup(self.artifact.stop)
        self.client = patch("utils.custom_maps.service.get_rcon_client", return_value=SimpleNamespace(password="test", execute=AsyncMock(return_value=(True, "Saved"))))
        self.client.start()
        self.addCleanup(self.client.stop)
        for user in (1, 2):
            self.service.store.approve_identity(1, user, f"Player{user}", f"uuid-{user}", 9)
            self.service.store.put("preparation", {"id": f"1-{user}-{item['requirements_hash']}", "ready": True})

    async def room(self, session: dict | None = None) -> dict:
        room = await self.service.create_room(1, 100, 1, "one", "test", 2, session)
        return await self.service.membership(room["id"], 2, True, room["generation"])

    async def test_pause_resume_preserves_data_and_keeps_template_immutable(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        active = Path(self.manager.get_server(session["slot"]).cwd)
        (active / "world" / "progress").write_text("halfway")
        paused = await self.service.pause(session["id"], 1)
        self.assertEqual(paused["state"], "PAUSED")
        self.assertIsNone(paused["slot"])
        room2 = await self.room(paused)
        resumed = await self.service.start(room2["id"], 1, room2["generation"])
        self.assertEqual((Path(self.manager.get_server(resumed["slot"]).cwd) / "world" / "progress").read_text(), "halfway")
        self.assertFalse((self.root / "templates/one/1/world/progress").exists())

    async def test_missing_resume_data_never_resets_from_template(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        paused = await self.service.pause(session["id"], 1)
        (self.service._session_path(paused["id"]) / "world" / "level.dat").unlink()
        room2 = await self.room(paused)
        with self.assertRaisesRegex(ValueError, "原本による初期化は行わず"):
            await self.service.start(room2["id"], 1, room2["generation"])

    async def test_failed_save_cannot_be_reported_as_completed(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        self.manager.get_server(session["slot"]).last_stop_graceful = False
        with self.assertRaises(RuntimeError):
            await self.service.pause(session["id"], 1, finish=True)
        current = await self.service.get("session", session["id"])
        self.assertEqual(current["state"], "FAILED")
        self.assertNotEqual(current["result"], "clear_reported")
        self.assertTrue((Path(self.manager.get_server(session["slot"]).cwd) / "world/level.dat").exists())

    async def test_stale_delete_confirmation_is_rejected(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        paused = await self.service.pause(session["id"], 1)
        old_generation = paused["control_generation"]
        room2 = await self.room(paused)
        await self.service.start(room2["id"], 1, room2["generation"])
        paused = await self.service.pause(session["id"], 1)
        with self.assertRaises(ValueError):
            await self.service.delete(session["id"], 1, expected=old_generation, purge=True)
        self.assertTrue(self.service._session_path(session["id"]).exists())
        deleted = await self.service.delete(session["id"], 1, expected=paused["control_generation"])
        self.assertEqual(deleted["result"], "abandoned")
        self.assertTrue((self.root / "trash" / session["id"] / "world/level.dat").exists())

    async def test_poll_cannot_overwrite_a_concurrent_pause(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        entered, release = asyncio.Event(), asyncio.Event()
        async def query(command: str) -> tuple:
            entered.set()
            await release.wait()
            return True, "There are 0 of a max of 4 players online: "
        client = SimpleNamespace(execute=query)
        with patch("utils.custom_maps.service.get_rcon_client", return_value=client):
            poll = asyncio.create_task(self.service.poll())
            await entered.wait()
            latest = await self.service.get("session", session["id"])
            latest.update(state="PAUSED", slot=None)
            await self.service.put("session", latest)
            release.set()
            await poll
        self.assertEqual((await self.service.get("session", session["id"]))["state"], "PAUSED")

    async def test_backup_restores_new_session_and_omits_rcon_secret(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        await self.service.pause(session["id"], 1)
        backup = await self.service.backup(session["id"], 1, upload=False)
        with zipfile.ZipFile(backup["path"]) as zipped:
            self.assertNotIn("rcon.password", zipped.read("server.properties").decode())
        restored = await self.service.restore_backup(backup["id"], 1)
        self.assertNotEqual(restored["id"], session["id"])
        self.assertEqual(restored["state"], "PAUSED")
        self.assertTrue((self.service._session_path(restored["id"]) / "world/level.dat").is_file())

    async def test_backup_restore_inherits_recovered_channel_and_owner(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        paused = await self.service.pause(session["id"], 1)
        backup = await self.service.backup(session["id"], 1, upload=False)
        paused.update(channel="101", owner="2")
        await self.service.put("session", paused)
        restored = await self.service.restore_backup(backup["id"], 2)
        self.assertEqual((restored["channel"], restored["owner"]), ("101", "2"))

    async def test_new_start_rechecks_current_publication(self) -> None:
        room = await self.room()
        self.service.catalog.get("one")["published"] = False
        with self.assertRaisesRegex(ValueError, "公開状態"):
            await self.service.start(room["id"], 1, room["generation"])
        self.manager.start_server.assert_not_awaited()

    async def test_stale_pause_and_finish_cannot_affect_resumed_play(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        old = session["control_generation"]
        paused = await self.service.pause(session["id"], 1, expected=old)
        room = await self.room(paused)
        resumed = await self.service.start(room["id"], 1, room["generation"])
        with self.assertRaises(ValueError):
            await self.service.pause(session["id"], 1, finish=True, expected=old)
        self.assertEqual((await self.service.get("session", resumed["id"]))["state"], "RUNNING")

    async def test_unclaimed_slot_data_is_never_adopted_as_a_new_session(self) -> None:
        for slot in self.service.slots:
            path = Path(self.manager.get_server(slot).cwd)
            path.mkdir(parents=True)
            (path / "unknown-progress").write_bytes(b"keep")
        room = await self.room()
        with self.assertRaises(ValueError):
            await self.service.start(room["id"], 1, room["generation"])
        self.manager.start_server.assert_not_awaited()

    async def test_trash_undo_preserves_world_and_finish_undo_restores_paused(self) -> None:
        room = await self.room()
        session = await self.service.start(room["id"], 1, room["generation"])
        finished = await self.service.pause(session["id"], 1, finish=True)
        paused = await self.service.undo(finished["id"], 1)
        self.assertEqual(paused["state"], "PAUSED")
        deleted = await self.service.delete(paused["id"], 1, expected=paused["control_generation"])
        self.assertEqual(deleted["state"], "DELETED")
        restored = await self.service.undo(paused["id"], 1)
        self.assertEqual(restored["state"], "PAUSED")
        self.assertEqual((self.service._session_path(restored["id"]) / "world/level.dat").read_bytes(), b"original")
