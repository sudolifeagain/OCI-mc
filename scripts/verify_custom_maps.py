#!/usr/bin/env python3
"""保守中の本番ARMホストで、複製だけを使って接続・保存復元を確認する。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import shutil
from pathlib import Path
import subprocess
import sys
import time
import uuid

from dotenv import load_dotenv
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.custom_maps.service import CustomMapService  # noqa: E402
from utils.rcon import get_rcon_client  # noqa: E402
from utils.server_manager import MultiServerManager  # noqa: E402


class VerificationService(CustomMapService):
    def available(self) -> set[str]:
        return {x["id"] for x in self.catalog.items.values() if (self.root / "templates" / x["id"] / x["revision"] / "manifest.json").exists()}

    def _attach(self, session: dict) -> None:
        super()._attach(session)
        props = Path(self.manager.get_server(session["slot"]).cwd) / "server.properties"
        # この変更は検証用複製だけへ適用する。通常のアタッチは常にonline-mode=trueである。
        text = props.read_text()
        text = text.replace("server-ip=\n", "server-ip=127.0.0.1\n").replace("online-mode=true", "online-mode=false").replace("white-list=true", "white-list=false").replace("enforce-whitelist=true", "enforce-whitelist=false")
        props.write_text(text)
        self._permissions(session["slot"])


async def clients(node: str, server, *, first: bool) -> dict:
    version = json.loads((Path(server.cwd) / "launch.json").read_text())["version"]
    process = await asyncio.create_subprocess_exec(node, str(Path(__file__).with_name("custom_map_clients.cjs")), version, str(server.port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    client = get_rcon_client(server.config)
    await asyncio.sleep(10)
    stats = server.get_stats()
    success, response = await client.execute("list")
    online = success and all(f"OCI_QA{n}" in response for n in range(1, 5))
    if first:
        await client.execute("scoreboard objectives add oci_qa dummy")
        await client.execute("scoreboard players set OCI_QA1 oci_qa 117")
    else:
        success, response = await client.execute("scoreboard players list OCI_QA1")
        if not success or "117" not in response:
            raise RuntimeError("保存したスコアを再開後に確認できない: " + response)
    stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
    try:
        result = json.loads(stdout.decode().splitlines()[-1])
    except (ValueError, IndexError):
        result = {"error": stderr.decode()[-700:]}
    result["rcon_four_players"] = online
    result["server_memory_mb"] = stats["memory_mb"] if stats else None
    result["host_available_mb"] = round(psutil.virtual_memory().available / 1048576)
    if process.returncode != 0 or not online:
        raise RuntimeError("4人同時接続を確認できない: " + json.dumps(result, ensure_ascii=False))
    return result


async def verify(args) -> None:
    if not args.allow_production_maintenance or subprocess.run(["systemctl", "is-active", "--quiet", "discord-bot"], check=False).returncode == 0:
        raise ValueError("Botを正常停止した保守時間だけに実行できる")
    if any(p.info["name"] == "java" for p in psutil.process_iter(["name"])):
        raise ValueError("既存Minecraftプロセスが稼働中である")
    config = json.loads(Path("config.json").read_text())
    for entry in config["servers"].values():
        entry["auto_start"] = False
    root = Path(config["custom_maps"]["root"])
    launcher = root / "qa-launch.py"
    shutil.copyfile(Path(__file__).with_name("launch_custom_map.py"), launcher)
    launcher.chmod(0o755)
    for entry in config["servers"].values():
        if entry.get("workload_type") == "custom_map":
            entry["use_script"] = str(launcher)
    runtime = root / "qa" / ("run-" + str(time.time_ns()))
    manager = MultiServerManager(config["servers"], str(runtime))
    service = VerificationService(config["custom_maps"], manager, runtime)
    for n in range(1, 5):
        digest = bytearray(hashlib.md5(f"OfflinePlayer:OCI_QA{n}".encode()).digest())
        digest[6] = digest[6] & 0x0F | 0x30
        digest[8] = digest[8] & 0x3F | 0x80
        service.store.approve_identity(0, n, f"OCI_QA{n}", str(uuid.UUID(bytes=bytes(digest))), "qa")
    async def room(item, session=None):
        result = await service.create_room(0, 0, 1, item["id"], "接続・保存復元検証", 4, session)
        for n in range(1, 5):
            service.store.put("preparation", {"id": f"0-{n}-{result['map']['requirements_hash']}", "ready": True})
            if n > 1:
                result = await service.membership(result["id"], n, True, result["generation"])
        return result
    outcomes = []
    selected = [service.catalog.get(args.map)] if args.map else list(service.catalog.items.values())
    try:
        if args.parallel:
            sessions = []
            for map_id in ("deep-sea", "black-cat"):
                prepared = await room(service.catalog.get(map_id))
                sessions.append(await service.start(prepared["id"], "qa", prepared["generation"]))
            evidence = await asyncio.gather(*(clients(args.node, manager.get_server(s["slot"]), first=True) for s in sessions))
            (runtime / "parallel.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            for session in sessions:
                session = await service.pause(session["id"], "qa")
                await service.delete(session["id"], "qa", expected=session["control_generation"], purge=True)
            print("Two custom maps / eight synthetic clients: passed", flush=True)
        async def verify_one(item):
            started = time.time()
            evidence = {"map_id": item["id"], "mc_version": item["mc_version"], "host_arch": "aarch64", "checked_at": started, "synthetic_clients": True, "puzzle_completion_verified": False}
            ident = None
            try:
                manifest_file = root / "templates" / item["id"] / item["revision"] / "manifest.json"
                original = manifest_file.parent / "world/level.dat"
                digest = hashlib.sha256(original.read_bytes()).hexdigest()
                prepared = await room(item)
                session = await service.start(prepared["id"], "qa", prepared["generation"])
                ident = session["id"]
                evidence["rcon_ready"] = True
                evidence["first_clients"] = await clients(args.node, manager.get_server(session["slot"]), first=True)
                await service.poll()
                session = await service.pause(ident, "qa")
                prepared = await room(item, session)
                session = await service.start(prepared["id"], "qa", prepared["generation"])
                evidence["resumed_clients"] = await clients(args.node, manager.get_server(session["slot"]), first=False)
                session = await service.pause(ident, "qa", finish=True)
                evidence.update(save_resume=True, multiple_players=True, original_unchanged=hashlib.sha256(original.read_bytes()).hexdigest() == digest)
                if not evidence["original_unchanged"]:
                    raise RuntimeError("原本が変更された")
                manifest = json.loads(manifest_file.read_text())
                manifest.update(smoke_verified=True, verification=evidence)
                manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
                await service.delete(ident, "qa", expected=session["control_generation"], purge=True)
                evidence["passed"] = True
            except Exception as error:
                evidence.update(passed=False, error=str(error))
                if ident:
                    current = await service.get("session", ident)
                    if current["state"] == "RUNNING":
                        await service.pause(ident, "qa")
                for current in await service.rows("session"):
                    if current.get("slot") and current["map_id"] == item["id"]:
                        slot = current["slot"]
                        if manager.is_running(slot):
                            await manager.stop_server(slot)
                        source = Path(manager.get_server(slot).cwd)
                        destination = runtime / "failed" / current["id"]
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        if source.exists() and any(source.iterdir()):
                            source.rename(destination)
                            source.mkdir()
                        current.update(slot=None, state="FAILED")
                        await service.put("session", current)
            evidence["elapsed_seconds"] = round(time.time() - started, 1)
            outcomes.append(evidence)
            (runtime / "results.json").write_text(json.dumps(outcomes, ensure_ascii=False, indent=2))
            print(json.dumps({k: evidence[k] for k in ("map_id", "passed", "elapsed_seconds", "error") if k in evidence}, ensure_ascii=False), flush=True)
        for offset in range(0, len(selected), 2):
            await asyncio.gather(*(verify_one(item) for item in selected[offset:offset + 2]))
    finally:
        for slot in service.slots:
            if manager.is_running(slot):
                await manager.stop_server(slot)
    print("Evidence: " + str(runtime / "results.json"), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-production-maintenance", action="store_true")
    parser.add_argument("--node", required=True)
    parser.add_argument("--map")
    parser.add_argument("--parallel", action="store_true")
    args = parser.parse_args()
    load_dotenv("/opt/minecraft/bot/.env")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(verify(args))


if __name__ == "__main__":
    main()
