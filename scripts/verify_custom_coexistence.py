"""保守中のPaper複製との並列動作、バックアップ、Bot復帰経路を検証する。"""

import asyncio
import json
from pathlib import Path
import shutil

from scripts.verify_custom_maps import VerificationService, clients, assert_saved_score
from utils.rcon import get_rcon_client
from utils.server_manager import MultiServerManager, ServerInstance


async def run(service, manager, room, args, runtime: Path) -> None:
    slot = "custom-2"
    directory = Path(manager.get_server(slot).cwd)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Paper検証用の枠に既存データがある")
    directory.mkdir(parents=True, exist_ok=True)
    source_config = manager._servers_config["paper"]
    source_jar = Path(source_config["cwd"]) / source_config["jar"]
    shutil.copyfile(source_jar, directory / "paper.jar")
    config = {**manager._servers_config[slot], "workload_type": "paper", "use_script": None, "jar": "paper.jar", "memory": "4G", "min_memory": "512M", "reservation_mb": 5632, "java_command": source_config["java_command"]}
    password = get_rcon_client(config).password
    properties = {"server-ip": "127.0.0.1", "server-port": config["port"], "online-mode": "false", "enable-rcon": "true", "rcon.port": config["rcon_port"], "rcon.password": password, "level-name": "world", "view-distance": 6, "simulation-distance": 6, "spawn-protection": 0, "difficulty": "peaceful"}
    (directory / "server.properties").write_text("\n".join(f"{k}={v}" for k, v in properties.items()) + "\n")
    (directory / "eula.txt").write_text("eula=true\n")
    (directory / "launch.json").write_text(json.dumps({"version": "26.2"}))
    service._permissions(slot)
    manager._servers_config[slot] = config
    manager.servers[slot] = ServerInstance(slot, config, manager.runtime_dir)
    service.slots = ["custom-1"]
    result = {"paper_is_disposable_clone": True}
    managers = [manager]
    try:
        if not await manager.start_server(slot):
            raise RuntimeError(manager.get_server(slot).last_error)
        item = service.catalog.get("karakurari")
        prepared = await room(item)
        session = await service.start(prepared["id"], "qa", prepared["generation"])
        result["parallel_clients"] = await asyncio.gather(clients(args.node, manager.get_server(slot), first=True), clients(args.node, manager.get_server(session["slot"]), first=True))
        result["forge_blocked"] = not manager.check_memory_for_start("forge")[0]
        result["third_server_blocked"] = not manager.check_memory_for_start("paper")[0]
        for sid in [slot, session["slot"]]:
            ok, text = await get_rcon_client(manager.get_server(sid).config).execute("tick query")
            if sid == slot:
                result["paper_tick_query"] = text if ok else "unavailable"
        session = await service.pause(session["id"], "qa")
        assert_saved_score(service._session_path(session["id"]))
        backup = await service.backup(session["id"], "qa", upload=False)
        restored = await service.restore_backup(backup["id"], "qa")
        assert_saved_score(service._session_path(restored["id"]))
        if restored["id"] == session["id"]:
            raise RuntimeError("復元IDが元のプレイと同一である")
        prepared = await room(item, restored)
        restored = await service.start(prepared["id"], "qa", prepared["generation"])
        result["backup_restored_clients"] = await clients(args.node, manager.get_server(restored["slot"]), first=False)
        if not await manager.stop_server(restored["slot"], preserve_desired=True) or not manager.get_server(restored["slot"]).last_stop_graceful:
            raise RuntimeError("Bot復帰検証前の正常停止に失敗した")
        reloaded_manager = MultiServerManager(manager._servers_config, manager.runtime_dir)
        managers.append(reloaded_manager)
        reloaded_service = VerificationService(service.config, reloaded_manager, runtime)
        reloaded_service.slots = ["custom-1"]
        if not await reloaded_service.restore_slot("custom-1"):
            raise RuntimeError("永続状態と起動記録からの復帰に失敗した")
        result["bot_recovery_clients"] = await clients(args.node, reloaded_manager.get_server("custom-1"), first=False)
        completed = await reloaded_service.pause(restored["id"], "qa", finish=True)
        assert_saved_score(reloaded_service._session_path(restored["id"]))
        paused = await reloaded_service.undo(completed["id"], "qa")
        await reloaded_service.delete(paused["id"], "qa", expected=paused["control_generation"])
        undone = await reloaded_service.undo(paused["id"], "qa")
        await reloaded_service.delete(undone["id"], "qa", expected=undone["control_generation"], purge=True)
        await service.delete(session["id"], "qa", expected=session["control_generation"], purge=True)
        result.update(backup_restore=True, bot_recovery=True, finish_and_trash_undo=True, passed=True)
        if not result["forge_blocked"] or not result["third_server_blocked"]:
            raise RuntimeError("排他・同時起動数の制限が機能していない")
    except Exception as error:
        result.update(passed=False, error=str(error))
        raise
    finally:
        for instance in reversed(managers):
            for sid in ["custom-1", slot]:
                if instance.is_running(sid):
                    await instance.stop_server(sid, force=True)
        service._permissions(slot)
        if result.get("passed"):
            shutil.rmtree(directory)
            directory.mkdir()
        (runtime / "coexistence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
