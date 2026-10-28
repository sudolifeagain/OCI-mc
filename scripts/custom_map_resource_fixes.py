#!/usr/bin/python3
"""既知の配布データ不備を、原本を変えず起動枠の複製だけで修復する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid


DEFAULT_PLAN = Path(__file__).resolve().parents[1] / "infra" / "custom-map-resource-fixes.json"


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts or not path.resolve().is_relative_to(root):
        raise ValueError(f"修復対象が起動枠の外を参照している: {relative}")
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"修復対象にシンボリックリンクがある: {relative}")
    return path


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def apply_fixes(server: Path, version: str, plan_path: Path = DEFAULT_PLAN) -> list[str]:
    server = server.resolve(strict=True)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    applied = []
    for rule in plan["rules"]:
        if version != rule["version"]:
            continue
        marker = checked_path(server, rule["marker"]["path"])
        if not marker.is_file() or digest(marker) != rule["marker"]["sha256"]:
            continue
        if not re.fullmatch(r"[a-z0-9_-]+", rule["id"]):
            raise ValueError("修復規則のIDが不正である")
        pending = []
        # 全対象を先に検査し、未知の版へ修復を一部だけ適用しない。
        for item in rule["operations"]:
            path = checked_path(server, item["path"])
            if item["action"] == "create":
                replacement = item["content"].encode("utf-8")
                if hashlib.sha256(replacement).hexdigest() != item["result_sha256"]:
                    raise ValueError(f"追加リソースのSHA-256が異なる: {item['path']}")
                if path.exists():
                    if path.is_file() and digest(path) == item["result_sha256"]:
                        continue
                    raise ValueError(f"追加先に別のリソースがある: {item['path']}")
                pending.append((item, path, replacement))
                continue
            if not path.exists():
                if item["action"] == "quarantine":
                    continue
                raise ValueError(f"修復対象が見つからない: {item['path']}")
            if path.is_dir():
                actual = {}
                for child in path.rglob("*"):
                    checked_path(server, child.relative_to(server).as_posix())
                    if child.is_file():
                        actual[child.relative_to(path).as_posix()] = digest(child)
                tree_hash = hashlib.sha256(json.dumps(actual, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                matches = actual == item["files"] if "files" in item else tree_hash == item.get("tree_sha256")
                if item["action"] != "quarantine" or not matches:
                    raise ValueError(f"修復対象フォルダーの内容が異なる: {item['path']}")
                replacement = None
            else:
                original = path.read_bytes()
                original_hash = hashlib.sha256(original).hexdigest()
                if item["action"] == "replace" and original_hash == item["result_sha256"]:
                    continue
                if original_hash != item["sha256"]:
                    raise ValueError(f"修復対象のSHA-256が異なる: {item['path']}")
                replacement = None
                if item["action"] == "replace":
                    replacement = original
                    for edit in item["replacements"]:
                        before = edit["before"].encode("utf-8")
                        if replacement.count(before) != 1:
                            raise ValueError(f"修復する行が一意でない: {item['path']}")
                        replacement = replacement.replace(before, edit["after"].encode("utf-8"))
                    if hashlib.sha256(replacement).hexdigest() != item["result_sha256"]:
                        raise ValueError(f"修復結果のSHA-256が異なる: {item['path']}")
                elif item["action"] != "quarantine":
                    raise ValueError("未対応の修復操作である")
            pending.append((item, path, replacement))
        if not pending:
            continue
        backup = checked_path(server, f".resource-fixes/{rule['id']}/{uuid.uuid4().hex}")
        backup.mkdir(parents=True)
        for item, path, replacement in pending:
            saved = backup / item["path"]
            saved.parent.mkdir(parents=True, exist_ok=True)
            if item["action"] == "quarantine":
                os.replace(path, saved)
            else:
                mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
                if path.exists():
                    shutil.copy2(path, saved)
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, name = tempfile.mkstemp(dir=path.parent, prefix=".resource-fix-")
                try:
                    with os.fdopen(fd, "wb") as target:
                        target.write(replacement)
                        target.flush()
                        os.fsync(target.fileno())
                    os.chmod(name, mode)
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
            applied.append(item["path"])
        (backup / "repair.json").write_text(json.dumps({"id": rule["id"], "operations": [p[0] for p in pending]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return applied


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps({"repaired": apply_fixes(args.server, args.version)}, ensure_ascii=False))
