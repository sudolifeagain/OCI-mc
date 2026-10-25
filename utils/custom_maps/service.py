"""カスタムマップの準備、起動、保存、復旧を一元管理する。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import tempfile
import zipfile
from pathlib import Path

from utils.custom_maps.artifacts import ensure_vanilla, safe_extract, MAX_EXPANDED
from utils.backup_archive import create_server_archive
from utils.custom_maps.catalog import Catalog, valid_id
from utils.custom_maps.store import Store
from utils.rcon import get_rcon_client
from utils.server_manager import save_failed_log_line


class CustomMapService:
    def __init__(self, config: dict, manager, runtime: Path):
        self.config = config
        self.manager = manager
        self.root = Path(config.get("root", "/opt/minecraft/custom-maps")).resolve()
        self.catalog = Catalog(Path(config.get("catalog", "custom-maps.json")))
        self.store = Store(runtime / "custom-maps.sqlite3")
        self.lock = asyncio.Lock()
        self.slots = list(config.get("slots", ["custom-1", "custom-2"]))
        self.public_address = os.getenv("CUSTOM_MAP_PUBLIC_ADDRESS", "")
        self.jobs: set[asyncio.Task] = set()

    async def rows(self, kind: str, guild: int | str | None = None) -> list[dict]:
        return await asyncio.to_thread(self.store.list, kind, guild)

    async def get(self, kind: str, ident: str) -> dict:
        value = await asyncio.to_thread(self.store.get, kind, ident)
        if value is None:
            raise ValueError("対象が見つからない")
        return value

    async def put(self, kind: str, value: dict, expected: int | None = None) -> dict:
        return await asyncio.to_thread(self.store.put, kind, value, expected=expected)

    def available(self) -> set[str]:
        result = set()
        for item in self.catalog.items.values():
            path = self.root / "templates" / item["id"] / item["revision"] / "manifest.json"
            try:
                verified = json.loads(path.read_text(encoding="utf-8"))
                if verified.get("smoke_verified") and verified["mc_version"] == item["mc_version"]:
                    result.add(item["id"])
            except (OSError, ValueError, KeyError):
                continue
        return result

    async def map_info(self, ident: str) -> dict:
        item = dict(self.catalog.get(ident))
        def pinned() -> dict:
            path = self.root / "templates" / item["id"] / item["revision"] / "manifest.json"
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if manifest.get("smoke_verified") and manifest["mc_version"] == item["mc_version"]:
                    return manifest["catalog"]
            except (OSError, ValueError, KeyError):
                pass
            return item
        return await asyncio.to_thread(pinned)

    async def create_room(self, guild: int, channel: int, owner: int, map_id: str, name: str, players: int, session: dict | None = None) -> dict:
        item = session["map"] if session else dict(self.catalog.get(map_id))
        if not session and (not item["published"] or map_id not in await asyncio.to_thread(self.available)):
            raise ValueError("このカスタムマップは導入検証待ちである")
        if not session:
            manifest_path = self.root / "templates" / item["id"] / item["revision"] / "manifest.json"
            manifest = await asyncio.to_thread(lambda: json.loads(manifest_path.read_text(encoding="utf-8")))
            item = manifest["catalog"]
        if not item["min_players"] <= players <= item["max_players"]:
            raise ValueError(f"予定人数は{item['min_players']}～{item['max_players']}人にする必要がある")
        if not name.strip() or len(name) > 60:
            raise ValueError("プレイ名は1～60文字で指定する必要がある")
        return await asyncio.to_thread(self.store.create, "room", guild=str(guild), channel=session["channel"] if session else str(channel), owner=str(owner), map_id=item["id"], map=item, name=name.strip(), players=players, members=[str(owner)], state="OPEN", session_id=session["id"] if session else None)

    async def membership(self, room_id: str, user: int | str, join: bool, expected: int) -> dict:
        async with self.lock:
            room = await self.get("room", room_id)
            if room["state"] != "OPEN":
                raise ValueError("参加者の確定処理中である")
            members = list(room["members"])
            if join and str(user) not in members:
                if len(members) >= room["map"]["max_players"]:
                    raise ValueError("対応人数の上限に達している")
                if room.get("session_id"):
                    previous = await self.get("session", room["session_id"])
                    if str(user) not in previous["participants"] and not room["map"].get("roster_changes", False):
                        raise ValueError("この作品の途中参加・入れ替えは未検証である")
                members.append(str(user))
            elif not join and str(user) in members:
                members.remove(str(user))
            room["members"] = members
            return await self.put("room", room, expected)

    async def identity(self, guild: int | str, user: int | str) -> dict | None:
        return await asyncio.to_thread(self.store.identity, guild, user)

    async def readiness(self, room: dict) -> list[dict]:
        result = []
        for user in room["members"]:
            identity = await self.identity(room["guild"], user)
            prep = await asyncio.to_thread(self.store.get, "preparation", f"{room['guild']}-{user}-{room['map']['requirements_hash']}")
            result.append({"user": user, "identity": identity, "ready": bool(prep and prep.get("ready"))})
        return result

    async def start(self, room_id: str, actor: int | str, expected: int) -> dict:
        async with self.lock:
            room = await self.get("room", room_id)
            if room["state"] != "OPEN" or room["generation"] != expected:
                raise ValueError("開始状態が変わった。最新の準備カードを確認する必要がある")
            people = await self.readiness(room)
            item = room["map"]
            if not item["min_players"] <= len(people) <= item["max_players"]:
                raise ValueError(f"今回の参加登録は{len(people)}人である。対応人数を満たす必要がある")
            if any(not person["identity"] or not person["identity"].get("approved") for person in people):
                raise ValueError("Minecraft IDが未承認の参加者がいる。参加準備から登録・承認を確認する必要がある")
            if any(not person["ready"] for person in people):
                raise ValueError("参加準備が未確認の人がいる。各自で今回の版・指定パックを確認し、準備状況を設定する必要がある")
            if room.get("session_id"):
                session = await self.get("session", room["session_id"])
                if session["state"] != "PAUSED":
                    raise ValueError("正常に保存された中断データだけを再開できる")
            else:
                current = self.catalog.get(item["id"])
                if not current["published"] or current["revision"] != item["revision"] or current["mc_version"] != item["mc_version"]:
                    raise ValueError("公開状態またはマップの改訂が変わった。新しい準備ルームを作る必要がある")
                if item["id"] not in await asyncio.to_thread(self.available):
                    raise ValueError("原本の導入検証が完了していない")
                session = None
            slot = await self._free_slot()
            allowed, reason = self.manager.check_memory_for_start(slot, custom_session=True)
            if not allowed:
                raise ValueError(reason)
            room["state"] = "STARTING"
            room = await self.put("room", room, expected)
            if not session:
                session = await asyncio.to_thread(self.store.create, "session", guild=room["guild"], channel=room["channel"], owner=room["owner"], name=room["name"], map_id=item["id"], map=item, state="PREPARING", result="unknown", slot=None, actual_players={}, active_seconds=0, missing_seconds=0, participants={}, last_saved=None)
            session["room_id"] = room["id"]
            session["attach_mode"] = "resume" if room.get("session_id") else "new"
            session["participants"].update({p["user"]: p["identity"] for p in people})
            session["current_members"] = [p["user"] for p in people]
            session["slot"] = slot
            session["last_slot"] = slot
            session["control_generation"] = session.get("control_generation", 0) + 1
            session["state"] = "PREPARING"
            session = await self.put("session", session)
            try:
                await asyncio.to_thread(self._attach, session)
                session["state"] = "STARTING"
                session = await self.put("session", session)
                success = await self.manager.start_server(slot, custom_session=True)
                if not success:
                    raise RuntimeError(self.manager.get_server(slot).last_error)
                session["state"] = "RUNNING"
                session["last_poll"] = time.time()
                session = await self.put("session", session)
                room["state"] = "STARTED"
                room["session_id"] = session["id"]
                await self.put("room", room)
                await asyncio.to_thread(self.store.event, "started", session["id"], actor, slot=slot)
                return session
            except Exception as error:
                session["state"] = "FAILED"
                session["error"] = str(error)[:500]
                destination = Path(self.manager.get_server(slot).cwd)
                has_data = destination.exists() and any(destination.iterdir())
                if not has_data:
                    session["slot"] = None
                await self.put("session", session)
                room["state"] = "OPEN"
                room["session_id"] = session["id"] if has_data or session["attach_mode"] == "resume" else None
                await self.put("room", room)
                raise

    async def _free_slot(self) -> str:
        occupied = {s.get("slot") for s in await self.rows("session") if s.get("slot")}
        for slot in self.slots:
            path = Path(self.manager.get_server(slot).cwd)
            if slot not in occupied and not self.manager.is_running(slot) and not (path.exists() and any(path.iterdir())):
                return slot
        raise ValueError("カスタムマップ枠が使用中である。参加準備は継続できる")

    def _session_path(self, session_id: str) -> Path:
        return self.root / "sessions" / valid_id(session_id) / "server"

    def _attach(self, session: dict) -> None:
        item = session["map"]
        slot = self.manager.get_server(session["slot"])
        destination = Path(slot.cwd)
        destination.parent.mkdir(parents=True, exist_ok=True)
        saved = self._session_path(session["id"])
        if session.get("attach_mode") == "resume" and not (saved / "world" / "level.dat").is_file():
            raise ValueError("中断データが見つからない。原本による初期化は行わず管理者の確認を待つ")
        artifact = ensure_vanilla(self.root, item["mc_version"])
        client = get_rcon_client(slot.config)
        if client is None:
            raise ValueError("カスタムマップ枠のRCON設定が不足している")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("起動枠に未整理の保存データがある。管理者の確認が必要である")
        if destination.exists():
            destination.rmdir()
        if saved.exists():
            os.replace(saved, destination)
        else:
            template = self.root / "templates" / valid_id(item["id"]) / valid_id(item["revision"])
            if not (template / "world" / "level.dat").is_file():
                raise ValueError("原本ワールドが見つからない")
            required = sum(p.stat().st_size for p in template.rglob("*") if p.is_file())
            if shutil.disk_usage(self.root).free < required + 1024 ** 3:
                raise ValueError("新規ワールド用のディスク容量が不足している")
            shutil.copytree(template / "world", destination / "world")
            if (template / "author.properties").exists():
                shutil.copyfile(template / "author.properties", destination / "server.properties")
        props = {}
        file = destination / "server.properties"
        if file.exists():
            for line in file.read_text(encoding="utf-8", errors="replace").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    props[key.strip()] = value.strip()
        props.setdefault("gamemode", "adventure")
        props.setdefault("spawn-protection", "0")
        props.setdefault("view-distance", "10")
        props.setdefault("simulation-distance", "10")
        props.update(item.get("server_properties", {}))
        version_numbers = tuple(int(v) for v in item["mc_version"].split("."))
        if version_numbers[0] == 1 and version_numbers[1] <= 12:
            props["gamemode"] = {"survival": "0", "creative": "1", "adventure": "2", "spectator": "3"}.get(str(props["gamemode"]), str(props["gamemode"]))
        props.update({"server-port": str(slot.port), "server-ip": "", "level-name": "world", "online-mode": "true", "white-list": "true", "enforce-whitelist": "true", "max-players": str(item["max_players"]), "enable-command-block": "true", "enable-rcon": "true", "rcon.port": str(slot.config["rcon_port"]), "rcon.password": client.password, "broadcast-rcon-to-ops": "false", "motd": "カスタムマップ | " + item["title"], "require-resource-pack": "true" if props.get("resource-pack") else "false"})
        def property_value(value: str) -> str:
            result = []
            for char in str(value):
                if ord(char) < 128:
                    result.append(char)
                else:
                    data = char.encode("utf-16-be")
                    result.extend(f"\\u{int.from_bytes(data[n:n + 2], 'big'):04x}" for n in range(0, len(data), 2))
            return "".join(result)
        file.write_text("\n".join(f"{key}={property_value(value)}" for key, value in props.items()) + "\n", encoding="utf-8")
        people = [session["participants"][u] for u in session["current_members"]]
        entries = [{"uuid": p["uuid"], "name": p["name"]} for p in people]
        (destination / "whitelist.json").write_text(json.dumps(entries), encoding="utf-8")
        ops = [{**p, "level": 2, "bypassesPlayerLimit": False} for p in entries] if item.get("op_required") else []
        (destination / "ops.json").write_text(json.dumps(ops), encoding="utf-8")
        (destination / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        (destination / "user_jvm_args.txt").write_text(f"-Xms{slot.config.get('min_memory', '512M')}\n-Xmx{slot.memory}\n", encoding="utf-8")
        launch = {"session_id": session["id"], "version": item["mc_version"], "java": artifact["java"], "sha256": artifact["sha256"], "memory": slot.memory, "min_memory": slot.config.get("min_memory", "512M")}
        (destination / "launch.json").write_text(json.dumps(launch), encoding="utf-8")
        self._permissions(session["slot"])

    def _permissions(self, slot: str) -> None:
        if os.name == "posix" and self.config.get("permission_helper", True):
            import subprocess
            subprocess.run(["sudo", "-n", "/usr/local/sbin/oci-mc-custom-permissions", slot], check=True, capture_output=True)

    async def pause(self, ident: str, actor: int | str, *, finish: bool = False, expected: int | None = None) -> dict:
        async with self.lock:
            session = await self.get("session", ident)
            if expected is not None and session.get("control_generation", 0) != expected:
                raise ValueError("表示後にプレイ状態が変わった。最新のカードから操作する必要がある")
            if session["state"] not in ("RUNNING", "PAUSED"):
                raise ValueError("プレイ中または中断中のデータだけを保存・完了できる")
            if session["state"] == "RUNNING":
                session["state"] = "SAVING"
                session = await self.put("session", session)
                slot = session["slot"]
                server = self.manager.get_server(slot)
                if not await self.manager.stop_server(slot):
                    session["state"] = "RUNNING" if self.manager.is_running(slot) else "FAILED"
                    await self.put("session", session)
                    raise RuntimeError(server.last_error)
                if not server.last_stop_graceful:
                    session["state"] = "FAILED"
                    await self.put("session", session)
                    raise RuntimeError("正常停止を確認できない。保存データを保持して管理者の確認を待つ")
                await asyncio.to_thread(self._detach, session)
                session["slot"] = None
                session["last_saved"] = time.time()
            session["state"] = "COMPLETED" if finish else "PAUSED"
            session["online"] = []
            session["control_generation"] = session.get("control_generation", 0) + 1
            if finish:
                session["result"] = "clear_reported"
            session = await self.put("session", session)
            await asyncio.to_thread(self.store.event, "finished" if finish else "paused", ident, actor)
            return session

    def _detach(self, session: dict) -> None:
        source = Path(self.manager.get_server(session["slot"]).cwd)
        self._permissions(session["slot"])
        target = self._session_path(session["id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("中断先に既存の保存データがある。上書きしない")
        os.replace(source, target)
        source.mkdir()

    async def delete(self, ident: str, actor: int | str, *, expected: int, purge: bool = False) -> dict:
        async with self.lock:
            session = await self.get("session", ident)
            if session.get("control_generation", 0) != expected:
                raise ValueError("確認後にプレイ状態が変わった。最新のカードから消去を確認する必要がある")
            if session["state"] not in ("PAUSED", "COMPLETED", "DELETED"):
                raise ValueError("正常に停止したデータだけを消去できる")
            target = self.root / "trash" / valid_id(ident)
            if session["state"] != "DELETED":
                source = self._session_path(ident)
                session["state"] = "DELETING"
                session = await self.put("session", session)
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(os.replace, source, target)
                session["state"] = "DELETED"
                session["trash_until"] = time.time() + 7 * 86400
                session["data_available"] = "trash"
                if session["result"] != "clear_reported":
                    session["result"] = "abandoned"
            if purge:
                if not target.resolve().is_relative_to((self.root / "trash").resolve()) or target.is_symlink():
                    raise ValueError("消去対象が不正である")
                if target.exists():
                    await asyncio.to_thread(shutil.rmtree, target)
                session["data_available"] = "none"
            session = await self.put("session", session)
            await asyncio.to_thread(self.store.event, "purged" if purge else "deleted", ident, actor)
            return session

    async def restore_slot(self, slot: str) -> bool:
        candidates = [s for s in await self.rows("session") if s.get("slot") == slot]
        if len(candidates) != 1:
            return False

        session = candidates[0]
        if session["state"] != "RUNNING":
            return False
        try:
            marker = json.loads((Path(self.manager.get_server(slot).cwd) / "launch.json").read_text())
            if marker["session_id"] != session["id"]:
                return False
            if not self.manager.is_running(slot):
                if not await self.manager.start_server(slot, custom_session=True):
                    return False
            session["state"] = "RUNNING"
            session["last_poll"] = time.time()
            await self.put("session", session)
            return True
        except (OSError, ValueError, KeyError):
            return False

    async def cancel_room(self, ident: str, actor: int | str) -> dict:
        async with self.lock:
            room = await self.get("room", ident)
            if room["state"] != "OPEN":
                raise ValueError("参加準備中のルームだけを取り消せる")
            room["state"] = "CANCELLED"
            room = await self.put("room", room)
            await asyncio.to_thread(self.store.event, "room_cancelled", ident, actor)
            return room

    async def undo(self, ident: str, actor: int | str) -> dict:
        async with self.lock:
            session = await self.get("session", ident)
            if session["state"] == "DELETED":
                source = self.root / "trash" / valid_id(ident)
                if session.get("data_available") != "trash" or not source.is_dir():
                    raise ValueError("完全消去済み、または保持期限を過ぎている")
                destination = self._session_path(ident)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise ValueError("復元先に保存データがあるため上書きしない")
                await asyncio.to_thread(os.replace, source, destination)
                session.update(data_available="saved", trash_until=None)
            elif session["state"] != "COMPLETED":
                raise ValueError("クリア申告済みか、ごみ箱で保持中のデータだけを戻せる")
            session.update(state="PAUSED", result="unknown", control_generation=session.get("control_generation", 0) + 1)
            session = await self.put("session", session)
            await asyncio.to_thread(self.store.event, "undo", ident, actor)
            return session

    async def reconcile(self) -> None:
        """停止中に途切れた処理を、データを捨てずに可視化する。"""
        async with self.lock:
            for session in await self.rows("session"):
                if session["state"] == "RUNNING" and session.get("slot"):
                    log = Path(self.manager.get_server(session["slot"]).cwd) / "logs/latest.log"
                    def failed_save() -> bool:
                        if not log.exists():
                            return False
                        with log.open("rb") as stream:
                            stream.seek(max(0, log.stat().st_size - 2 * 1024 ** 2))
                            return any(save_failed_log_line(line) for line in stream.read().decode("utf-8", errors="replace").splitlines())
                    if session.get("save_error_seen") or await asyncio.to_thread(failed_save):
                        session.update(state="FAILED", error="前回の保存エラーを検出した。自動復帰を止め、保存データを保持して確認を待つ")
                        await self.put("session", session)
                if session["state"] == "DELETING":
                    source, trash = self._session_path(session["id"]), self.root / "trash" / valid_id(session["id"])
                    if trash.is_dir() and not source.exists():
                        session.update(state="DELETED", data_available="trash", trash_until=time.time() + 7 * 86400)
                    else:
                        session.update(state="FAILED", error="消去処理中に停止した。保存先の確認が必要である")
                    await self.put("session", session)
                elif session["state"] in ("PREPARING", "STARTING", "SAVING"):
                    session.update(state="FAILED", error="処理中にBotが停止した。保存データを保持して復旧を待つ")
                    await self.put("session", session)
            for job in await self.rows("job"):
                if job["state"] == "RUNNING":
                    job.update(state="FAILED", error="Bot再起動により処理結果の再確認が必要である")
                    await self.put("job", job)
            for room in await self.rows("room"):
                if room["state"] == "STARTING":
                    room["state"] = "OPEN"
                    await self.put("room", room)

    async def expire_trash(self) -> None:
        async with self.lock:
            for session in await self.rows("session"):
                if session["state"] == "DELETED" and session.get("data_available") == "trash" and session.get("trash_until", float("inf")) < time.time():
                    await asyncio.to_thread(shutil.rmtree, self.root / "trash" / valid_id(session["id"]), True)
                    session["data_available"] = "none"
                    await self.put("session", session)

    async def poll(self) -> None:
        for session in await self.rows("session"):
            if session["state"] != "RUNNING" or not session.get("slot"):
                continue
            server = self.manager.get_server(session["slot"])
            if server._maintenance:
                continue
            session["save_error_seen"] = bool(getattr(server, "_save_error_seen", False)) or session.get("save_error_seen", False)
            if not server.is_running():
                session["state"] = "FAILED"
                session["error"] = "サーバープロセスの異常終了を検出した"
                try:
                    await self.put("session", session, session["generation"])
                except ValueError:
                    pass
                continue
            client = get_rcon_client(server.config)
            ok, response = await client.execute("list") if client else (False, "")
            now = time.time()
            elapsed = now - session.get("last_poll", now)
            session["last_poll"] = now
            if not ok or elapsed > 60:
                session["missing_seconds"] += max(0, elapsed)
            else:
                names = re.split(r",\s*", response.partition(":")[2].strip())
                session["online"] = [name for name in names if name]
                if session["online"]:
                    session["active_seconds"] += max(0, elapsed)
                for user, identity in session["participants"].items():
                    if identity["name"] in session["online"]:
                        session["actual_players"][user] = now
            try:
                await self.put("session", session, session["generation"])
            except ValueError:
                pass  # RCON待機中の保存・終了結果を古いRUNNING状態で上書きしない。

    def address(self, session: dict) -> str:
        if session["state"] != "RUNNING" or not session.get("slot"):
            return "接続停止中。再開時は今回の接続案内を確認する必要がある"
        if not self.public_address:
            return "接続ホストの設定待ち（管理者がCUSTOM_MAP_PUBLIC_ADDRESSを設定）"
        return f"{self.public_address}:{self.manager.get_server(session['slot']).port}"

    async def backup(self, ident: str, actor: int | str, *, upload: bool = True) -> dict:
        async with self.lock:
            session = await self.get("session", ident)
            if session["state"] not in ("RUNNING", "PAUSED", "COMPLETED"):
                raise ValueError("保存状態を確認できるプレイだけをバックアップできる")
            slot = session.get("slot")
            running = bool(slot and self.manager.is_running(slot))
            maintenance = False
            safe_restart = False
            try:
                if running:
                    maintenance = self.manager.begin_maintenance(slot)
                    if not maintenance:
                        raise ValueError("このプレイは保守処理中である")
                    if not await self.manager.stop_server(slot, preserve_desired=True, maintenance=True):
                        raise RuntimeError("バックアップのための停止に失敗した")
                    if not self.manager.get_server(slot).last_stop_graceful:
                        session.update(state="FAILED", error="バックアップ時の正常停止を確認できない。保存データと復帰予約を保持している")
                        await self.put("session", session)
                        raise RuntimeError("正常保存を確認できないためバックアップを中止した")
                    safe_restart = True
                base = Path(self.manager.get_server(slot).cwd) if slot else self._session_path(ident)
                directory = self.root / "backups" / valid_id(ident)
                directory.mkdir(parents=True, exist_ok=True)
                stamp = str(time.time_ns())
                archive = directory / (stamp + ".zip")
                roots = [name for name in ("world", "whitelist.json", "ops.json", "launch.json", "user_jvm_args.txt") if (base / name).exists()]
                required = sum(p.stat().st_size for name in roots for p in ([base / name] if (base / name).is_file() else (base / name).rglob("*")) if p.is_file())
                entries = sum(1 for name in roots for _ in ([base / name] if (base / name).is_file() else (base / name).rglob("*")))
                if required > MAX_EXPANDED - 16 * 1024 ** 2 or entries > 149900:
                    raise ValueError("復元可能なバックアップ容量・項目数の上限を超えている")
                if shutil.disk_usage(directory).free < required + 1024 ** 3:
                    raise ValueError("バックアップ用の空き容量が不足している")
                temporary_archive = archive.with_suffix(".partial")
                try:
                    await create_server_archive(str(base), roots, str(temporary_archive), None)
                    await asyncio.to_thread(os.replace, temporary_archive, archive)
                finally:
                    temporary_archive.unlink(missing_ok=True)
                def complete() -> str:
                    with zipfile.ZipFile(archive, "a", zipfile.ZIP_DEFLATED) as zipped:
                        zipped.writestr("custom-session.json", json.dumps({"schema": 1, "session": session}, ensure_ascii=False))
                        props = base / "server.properties"
                        if props.exists():
                            content = "\n".join(line for line in props.read_text(encoding="utf-8", errors="replace").splitlines() if not line.startswith("rcon.password="))
                            zipped.writestr("server.properties", content)
                    with archive.open("rb") as stream:
                        return hashlib.file_digest(stream, "sha256").hexdigest()
                digest = await asyncio.to_thread(complete)
                record = await asyncio.to_thread(self.store.create, "backup", guild=session["guild"], channel=session["channel"], owner=session["owner"], session_id=ident, map_id=session["map_id"], revision=session["map"]["revision"], path=str(archive), sha256=digest, saved_at=session.get("last_saved"), filename=f"backup_custom-session-{ident}_{stamp}.zip")
                if upload:
                    from utils.notion_api import upload_to_notion, register_to_database
                    file_id = await asyncio.to_thread(upload_to_notion, str(archive), record["filename"])
                    await asyncio.to_thread(register_to_database, file_id, record["filename"], archive.stat().st_size / 1048576)
                    record["uploaded"] = True
                    record = await self.put("backup", record)
                if record.get("uploaded"):
                    older = [r for r in await self.rows("backup", session["guild"]) if r["session_id"] == ident and r.get("uploaded")]
                    for old in older[3:]:
                        old_path = Path(old["path"])
                        if old_path.resolve().is_relative_to(directory.resolve()) and not old_path.is_symlink():
                            await asyncio.to_thread(old_path.unlink, missing_ok=True)
                await asyncio.to_thread(self.store.snapshot, self.root / "backups" / "state.sqlite3")
                await asyncio.to_thread(self.store.event, "backup", ident, actor, backup_id=record["id"])
                return record
            finally:
                if maintenance:
                    if safe_restart and not self.manager.is_running(slot):
                        restored = await self.manager.start_server(slot, maintenance=True, custom_session=True)
                        if not restored:
                            session.update(state="FAILED", error="バックアップ後の復帰に失敗した。復帰予約と保存データを保持している")
                            await self.put("session", session)
                    self.manager.end_maintenance(slot)

    async def backup_all(self, channel=None) -> None:
        for session in await self.rows("session"):
            if session["state"] in ("RUNNING", "PAUSED", "COMPLETED"):
                try:
                    previous = [r for r in await self.rows("backup", session["guild"]) if r["session_id"] == session["id"] and r.get("uploaded")]
                    if session["state"] != "RUNNING" and previous and previous[0].get("saved_at") == session.get("last_saved"):
                        continue
                    await self.backup(session["id"], "scheduled")
                except Exception:
                    import logging
                    logging.exception("カスタムマップのセッション別バックアップに失敗: %s", session["id"])

    async def restore_backup(self, backup_id: str, actor: int | str) -> dict:
        async with self.lock:
            record = await self.get("backup", backup_id)
            original = await self.get("session", record["session_id"])
            archive = Path(record["path"])
            if not archive.resolve().is_relative_to((self.root / "backups").resolve()):
                raise ValueError("バックアップの保存先が不正である")
            if not archive.exists():
                from utils.notion_api import get_backups_list, download_file
                remote = next((x for x in await asyncio.to_thread(get_backups_list, 1, record["filename"]) if x.get("filename") == record["filename"]), None)
                if not remote or not remote.get("url"):
                    raise ValueError("復元用のローカル・遠隔バックアップが見つからない")
                archive.parent.mkdir(parents=True, exist_ok=True)
                partial = archive.with_suffix(".download")
                try:
                    await asyncio.to_thread(download_file, remote["url"], str(partial))
                    with partial.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                            raise ValueError("遠隔バックアップのSHA-256が一致しない")
                    await asyncio.to_thread(os.replace, partial, archive)
                finally:
                    partial.unlink(missing_ok=True)
            with archive.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                    raise ValueError("バックアップのSHA-256が一致しない")
            with tempfile.TemporaryDirectory(dir=archive.parent, prefix=".restore-") as temporary:
                stage = Path(temporary)
                await asyncio.to_thread(safe_extract, archive, stage)
                data = json.loads((stage / "custom-session.json").read_text(encoding="utf-8"))["session"]
                if data["id"] != record["session_id"] or data["map_id"] != record["map_id"] or data["map"]["revision"] != record["revision"] or not (stage / "world" / "level.dat").is_file():
                    raise ValueError("セッションID・原本版・ワールドがバックアップ記録と一致しない")
                data = {**data, "name": data["name"][:52] + "（復元）", "state": "PAUSED", "slot": None, "result": "unknown", "actual_players": {}, "active_seconds": 0, "restored_from": data["id"], "last_saved": time.time(), "control_generation": 1}
                data.update({key: original[key] for key in ("guild", "channel", "owner")})
                for key in ("id", "created", "updated", "generation"):
                    data.pop(key, None)
                restored = await asyncio.to_thread(self.store.create, "session", **data)
                destination = self._session_path(restored["id"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                (stage / "custom-session.json").unlink()
                await asyncio.to_thread(os.replace, stage, destination)
            await asyncio.to_thread(self.store.event, "restore_backup", restored["id"], actor, backup_id=backup_id)
            return restored
