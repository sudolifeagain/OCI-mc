#!/usr/bin/env python3
"""配布版Forge MODをハッシュ照合、退避、復元可能な状態記録付きで更新する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def checked(mods: Path, name: str) -> Path:
    if Path(name).name != name or not name.endswith(".jar"):
        raise ValueError("不正なMODファイル名")
    path = mods / name
    if path.is_symlink() or path.resolve().parent != mods.resolve():
        raise ValueError("mods外への書き込みは禁止")
    return path


def read_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_bytes())
    if plan.get("schema_version") != 1:
        raise ValueError("未対応の更新計画")
    for group in ("install", "retire"):
        names = [entry["file"] for entry in plan[group]]
        if len(set(names)) != len(names):
            raise ValueError("更新計画のファイル名が重複")
        for entry in plan[group]:
            checked(Path("mods"), entry["file"])
            if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("不正なSHA256")
    if {x["file"] for x in plan["install"]} & {x["file"] for x in plan["retire"]}:
        raise ValueError("追加と削除のファイル名が重複")
    if any(not x["url"].startswith("https://") for x in plan["install"]):
        raise ValueError("HTTPS配布元が必要")
    return plan


def atomic_copy(source: Path, target: Path, mode: int, uid: int, gid: int) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".pending", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(source, temporary)
        temporary.chmod(mode)
        if hasattr(os, "chown"):
            os.chown(temporary, uid, gid)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def install(server: Path, backup: Path, plan: dict[str, Any]) -> int:
    mods = (server / "mods").resolve()
    state = backup / "pending.json"
    if state.exists():
        raise ValueError("未確定のMOD更新がある")
    for entry in plan["install"] + plan["retire"]:
        path = checked(mods, entry["file"])
        if path.exists() and sha(path) != entry["sha256"]:
            raise ValueError(f"元MODのハッシュ不一致: {path.name}")
    missing = [x for x in plan["install"] if not checked(mods, x["file"]).exists()]
    retired = [x for x in plan["retire"] if checked(mods, x["file"]).exists()]
    if not missing and not retired:
        return 0
    # 一部の旧MODだけが失われた未知の状態から更新しない。
    if missing and len(retired) != len(plan["retire"]):
        raise ValueError("更新元のMOD構成が不完全")
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=backup) as stage_dir:
        stage = Path(stage_dir)
        for entry in missing:
            path = stage / entry["file"]
            with urllib.request.urlopen(entry["url"], timeout=120) as response, path.open("wb") as output:
                if not response.url.startswith("https://"):
                    raise ValueError("HTTPS以外へのリダイレクトは禁止")
                shutil.copyfileobj(response, output)
            if sha(path) != entry["sha256"]:
                raise ValueError(f"配布物のハッシュ不一致: {path.name}")
        records = []
        for entry in retired:
            original = checked(mods, entry["file"])
            saved = backup / entry["sha256"]
            shutil.copyfile(original, saved)
            saved.chmod(0o600)
            if sha(saved) != entry["sha256"]:
                raise ValueError("MOD退避コピーのハッシュ不一致")
            stat = original.stat()
            records.append({**entry, "mode": stat.st_mode & 0o777,
                            "uid": stat.st_uid, "gid": stat.st_gid})
        # 状態記録の永続化後にだけmodsを変更する。
        with tempfile.NamedTemporaryFile(dir=backup, delete=False) as stream:
            journal = Path(stream.name)
            stream.write(json.dumps({"retired": records, "added": missing}).encode())
            stream.flush()
            os.fsync(stream.fileno())
        journal.chmod(0o600)
        os.replace(journal, state)
        stat = mods.stat()
        for entry in missing:
            atomic_copy(stage / entry["file"], checked(mods, entry["file"]),
                        0o640, stat.st_uid, stat.st_gid)
        for entry in retired:
            checked(mods, entry["file"]).unlink()
    return len(missing) + len(retired)


def rollback(server: Path, backup: Path) -> None:
    state = backup / "pending.json"
    if not state.exists():
        return
    data = json.loads(state.read_bytes())
    mods = (server / "mods").resolve()
    # 全コピーと変更後MODを照合してから復元する。
    for entry in data["retired"]:
        saved = backup / entry["sha256"]
        if saved.parent != backup or not re.fullmatch(r"[0-9a-f]{64}", saved.name) or sha(saved) != saved.name:
            raise ValueError("MOD退避コピーが破損")
    for entry in data["retired"] + data["added"]:
        path = checked(mods, entry["file"])
        if path.exists() and sha(path) != entry["sha256"]:
            raise ValueError("更新後にMODが変更されたため復元不可")
    for entry in data["retired"]:
        atomic_copy(backup / entry["sha256"], checked(mods, entry["file"]),
                    entry["mode"], entry["uid"], entry["gid"])
    for entry in data["added"]:
        checked(mods, entry["file"]).unlink(missing_ok=True)
    state.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "rollback", "finalize"))
    parser.add_argument("--server-dir", type=Path, default=Path("/opt/minecraft/forge"))
    parser.add_argument("--backup-dir", type=Path, default=Path("/opt/minecraft/.forge-startup-mod-backups"))
    parser.add_argument("--plan", type=Path, default=Path("infra/forge-startup-updates.json"))
    args = parser.parse_args()
    if args.action == "install":
        print(f"Changed mods: {install(args.server_dir, args.backup_dir, read_plan(args.plan))}")
    elif args.action == "rollback":
        rollback(args.server_dir, args.backup_dir)
    else:
        (args.backup_dir / "pending.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
