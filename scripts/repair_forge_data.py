#!/usr/bin/env python3
"""確認済みのForgeデータ不整合を、内容照合と退避付きで修復する。"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


RESOURCE_ID = r"[a-z0-9_.-]+:[a-z0-9_./-]+"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def decode(data: bytes) -> Any:
    # Gsonが受理するコメントと末尾カンマを、文字列内容を保って処理する。
    text = data.decode("utf-8-sig")
    quoted = r'("(?:[^"\\]|\\.)*")'
    text = re.sub(quoted + r'|/\*.*?\*/|//[^\r\n]*',
                  lambda match: match[1] or "", text, flags=re.S)
    text = re.sub(quoted + r'|,\s*(?=[}\]])',
                  lambda match: match[1] or "", text)
    return json.loads(text)


def add_condition(data: dict, condition: dict) -> dict:
    result = copy.deepcopy(data)
    conditions = result.setdefault("conditions", [])
    if condition not in conditions:
        conditions.insert(0, condition)
    return result


def tag_names(data: Any) -> set[str]:
    if isinstance(data, dict):
        return {data["tag"]} if isinstance(data.get("tag"), str) else set().union(
            *(tag_names(value) for value in data.values())
        )
    if isinstance(data, list):
        return set().union(*(tag_names(value) for value in data))
    return set()


def item_names(data: Any) -> set[str]:
    if isinstance(data, dict):
        items = set()
        for key in ("item", "result"):
            if isinstance(data.get(key), str) and re.fullmatch(RESOURCE_ID, data[key]):
                items.add(data[key])
        if data.get("type") == "minecraft:item" and isinstance(data.get("name"), str):
            items.add(data["name"])
        if isinstance(data.get("items"), list):
            items.update(value for value in data["items"] if isinstance(value, str))
        return items | set().union(*(item_names(value) for value in data.values()))
    if isinstance(data, list):
        return set().union(*(item_names(value) for value in data))
    return set()


def repair_recipe(data: dict, reason: str, loaded_mods: set[str]) -> dict:
    item = re.search(r"Unknown item '([^']+)'", reason)
    if item:
        if item[1] not in json.dumps(data):
            return data
        return add_condition(data, {"type": "forge:item_exists", "item": item[1]})
    serializer = re.search(r"Invalid or unsupported recipe type '([^']+)'", reason)
    if serializer and serializer[1].split(":")[0] not in loaded_mods:
        if data.get("type") != serializer[1]:
            return data
        return add_condition(data, {
            "type": "forge:mod_loaded", "modid": serializer[1].split(":")[0]
        })
    if not data:
        # 空JSONは互換MODによるレシピ無効化である。Forgeの条件で表現する。
        return {"conditions": [{"type": "forge:false"}]}
    if "Missing type" in reason and data.get("type"):
        return data
    if data.get("type") == "minecraft:crafting_shapeless" and "ingredients" not in data:
        if data.get("pattern") and data.get("key"):
            result = copy.deepcopy(data)
            result["ingredients"] = [
                copy.deepcopy(data["key"][symbol])
                for row in data["pattern"] for symbol in row if symbol != " "
            ]
            result.pop("pattern", None)
            result.pop("key", None)
            return result
    tags = tag_names(data)
    if tags and ("empty" in reason.lower() or "Empty ingredient" in reason):
        for tag in sorted(tags):
            data = add_condition(data, {
                "type": "forge:not", "value": {"type": "forge:tag_empty", "tag": tag}
            })
        return data
    # 既に読み込み不能なデータだけを対象とする。正常なレシピは変更しない。
    return add_condition(data, {"type": "forge:false"})


def repair_loot(data: Any, missing: set[str]) -> Any:
    if isinstance(data, dict):
        if data.get("type") == "minecraft:item" and data.get("name") in missing:
            return None
        result = {}
        for key, value in data.items():
            fixed = repair_loot(value, missing)
            if key in ("entries", "children") and isinstance(fixed, list) and not fixed:
                fixed = [{"type": "minecraft:empty"}]
            result[key] = fixed
        return result
    if isinstance(data, list):
        return [fixed for value in data if (fixed := repair_loot(value, missing)) is not None]
    return data


def repair_tag(data: dict, missing: set[str]) -> dict:
    result = copy.deepcopy(data)
    values = []
    for value in result.get("values", []):
        identifier = value if isinstance(value, str) else value.get("id", "")
        fixed = re.sub(r"^([a-z0-9_.-]+):\1:", r"\1:", identifier)
        if fixed != identifier:
            value = fixed if isinstance(value, str) else {**value, "id": fixed}
        if identifier in missing:
            value = {"id": fixed, "required": False} if isinstance(value, str) else {
                **value, "required": False
            }
        values.append(value)
    result["values"] = values
    return result


def build_plan(server_dir: Path, log_path: Path, known_missing: set[str] | None = None) -> dict:
    known_missing = known_missing or set()
    raw_log = gzip.decompress(log_path.read_bytes()) if log_path.suffix == ".gz" else log_path.read_bytes()
    lines = raw_log.decode(errors="replace").splitlines()
    loaded = {m[1] for line in lines if (m := re.match(r"\s+- ([a-z0-9_]+) \S+$", line))}
    recipes: dict[str, str] = {}
    loot: dict[str, set[str]] = {}
    missing_tags: dict[str, set[str]] = {}
    advancements: dict[str, str] = {}
    orphan_parents: dict[str, str] = {}
    for index, line in enumerate(lines):
        match = re.search(rf"Parsing error loading recipe ({RESOURCE_ID}): (.*)", line)
        if match:
            recipes[match[1]] = match[2]
        match = re.search(rf"Couldn't parse element loot_tables:({RESOURCE_ID})", line)
        if match:
            context = "\n".join(lines[index + 1:index + 6])
            missing = set(re.findall(r"unknown string '([^']+)'|Unknown item '([^']+)'", context))
            items = {item for pair in missing for item in pair if item}
            if items:
                loot.setdefault(match[1], set()).update(items)
        match = re.search(rf"Couldn't load tag ({RESOURCE_ID}) as it is missing following references: (.*)", line)
        if match:
            missing_tags.setdefault(match[1], set()).update(
                re.findall(rf"(#?{RESOURCE_ID}) \(from ", match[2])
            )
        match = re.search(rf"Parsing error loading custom advancement ({RESOURCE_ID}): (.*)", line)
        if match:
            advancements[match[1]] = match[2]
        match = re.search(rf"Couldn't load advancement ({RESOURCE_ID}): Task Advancement\{{parentId=({RESOURCE_ID})", line)
        if match and (match[1] == match[2] or match[2].endswith(":deleted_mod_element")):
            orphan_parents[match[1]] = match[2]
    changes = []
    for jar in sorted((server_dir / "mods").glob("*.jar")):
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                match = re.fullmatch(r"data/([a-z0-9_.-]+)/(recipes|loot_tables|advancements|tags/(?:items|blocks|entity_types|fluids|functions|game_events|worldgen/biome))/([a-z0-9_./-]+)\.json", name)
                if not match:
                    continue
                identifier = f"{match[1]}:{match[3]}"
                kind = match[2]
                if not (
                    (kind == "recipes" and identifier in recipes)
                    or (kind == "loot_tables" and identifier in loot)
                    or (kind == "advancements" and identifier in advancements | orphan_parents)
                    or (kind.startswith("tags/") and identifier in missing_tags)
                    or (kind == "tags/worldgen/biome" and jar.name.startswith("Epic Villages "))
                ):
                    continue
                before = archive.read(name)
                parse_bytes = before
                if kind == "tags/worldgen/biome" and jar.name.startswith("Epic Villages "):
                    parse_bytes = re.sub(rb'\{o(?=\s+"id")', b"{", before)
                try:
                    data = decode(parse_bytes)
                except ValueError as error:
                    raise ValueError(f"JSON解析不可: {jar.name}:{name}: {before[:300]!r}") from error
                if kind == "recipes":
                    fixed = repair_recipe(data, recipes[identifier], loaded)
                    for item in sorted(item_names(data) & known_missing):
                        fixed = add_condition(fixed, {"type": "forge:item_exists", "item": item})
                elif kind == "loot_tables":
                    fixed = repair_loot(data, loot[identifier] | known_missing)
                elif kind.startswith("tags/"):
                    missing = missing_tags.get(identifier, set())
                    if kind == "tags/items":
                        missing = missing | known_missing
                    fixed = repair_tag(data, missing)
                elif identifier in orphan_parents:
                    fixed = copy.deepcopy(data)
                    if fixed.get("parent") == orphan_parents[identifier]:
                        fixed.pop("parent")
                else:
                    reason = advancements[identifier]
                    fixed = copy.deepcopy(data)
                    item = re.search(r"(?:Unknown item id|unknown string) '([^']+)'", reason)
                    if item:
                        fixed = add_condition(fixed, {"type": "forge:item_exists", "item": item[1]})
                    elif "Expected items to be a JsonArray" in reason:
                        for criterion in fixed.get("criteria", {}).values():
                            for predicate in criterion.get("conditions", {}).get("items", []):
                                if isinstance(predicate.get("items"), str):
                                    predicate["items"] = [predicate["items"]]
                    else:
                        fixed = add_condition(fixed, {"type": "forge:false"})
                if fixed != data or parse_bytes != before:
                    changes.append({"jar": jar.name, "resource": name,
                                    "before_sha256": digest(before), "data": fixed})
    return {"schema_version": 1, "minecraft_version": "1.20.1", "changes": changes}


def checked_jar(server_dir: Path, name: str) -> Path:
    if Path(name).name != name or not name.endswith(".jar"):
        raise ValueError("不正なMODファイル名")
    root = (server_dir / "mods").resolve()
    path = root / name
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("MODディレクトリ外のパス")
    return path


def compatibility_pack(server_dir: Path) -> Path:
    root = (server_dir / "world/datapacks").resolve()
    path = root / "oci-mc-data-compat.zip"
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError("不正な互換データパックのパス")
    return path


def mod_inventory(server_dir: Path, replacements: dict[Path, Path] | None = None) -> dict:
    replacements = replacements or {}
    lines = []
    for path in sorted((server_dir / "mods").glob("*.jar")):
        source = replacements.get(path, path)
        lines.append(f"{digest(source.read_bytes())}  {path.name}\n")
    return {"file_count": len(lines), "manifest_sha256": digest("".join(lines).encode()),
            "manifest_format": "sha256sum lines sorted by filename, UTF-8, LF"}


def apply_plan(server_dir: Path, plan: dict, backup_dir: Path, dry_run: bool = False,
               inventory_output: Path | None = None) -> int:
    if plan.get("schema_version") != 1 or plan.get("minecraft_version") != "1.20.1":
        raise ValueError("未対応の修復計画")
    grouped: dict[str, list[dict]] = {}
    for change in plan["changes"]:
        if not re.fullmatch(r"data/[a-z0-9_./-]+\.json", change["resource"]):
            raise ValueError("データリソース以外の変更は禁止")
        grouped.setdefault(change["jar"], []).append(change)
    prepared = []
    signed_resources: dict[str, bytes] = {}
    try:
        for filename, changes in grouped.items():
            path = checked_jar(server_dir, filename)
            edits = {}
            with zipfile.ZipFile(path) as source:
                for change in changes:
                    current = source.read(change["resource"])
                    replacement = encode(change["data"])
                    if current == replacement:
                        continue
                    if digest(current) != change["before_sha256"]:
                        raise ValueError(f"元データとの不一致: {filename}:{change['resource']}")
                    edits[change["resource"]] = replacement
                if not edits:
                    continue
                if any(re.match(r"META-INF/.*\.(SF|RSA|DSA)$", name, re.I) for name in source.namelist()):
                    # 署名を保ち、上位データパックでJSONだけを上書きする。
                    signed_resources.update(edits)
                    continue
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".pending", delete=False) as temp:
                    temporary = Path(temp.name)
                prepared.append((path, temporary))
                with zipfile.ZipFile(temporary, "w") as target:
                    for entry in source.infolist():
                        target.writestr(entry, edits.get(entry.filename, source.read(entry.filename)))
                    target.comment = source.comment
        if signed_resources:
            pack = compatibility_pack(server_dir)
            pack.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=pack.parent, suffix=".pending", delete=False) as temp:
                temporary = Path(temp.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                payload = {"pack.mcmeta": encode({"pack": {
                    "pack_format": 15, "description": "OCI-mc verified Forge data compatibility"
                }}), **signed_resources}
                for name, contents in sorted(payload.items()):
                    archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), contents)
            if pack.exists() and pack.read_bytes() == temporary.read_bytes():
                temporary.unlink()
            else:
                prepared.append((pack, temporary))
        if inventory_output:
            inventory_output.write_bytes(encode(mod_inventory(server_dir, dict(prepared))))
        if not prepared:
            return 0
        if dry_run:
            return len(prepared)
        backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        state_path = backup_dir / "pending.json"
        if state_path.exists():
            raise ValueError("未確定の修復状態がある")
        originals = []
        for path, temporary in prepared:
            original_digest = digest(path.read_bytes()) if path.exists() else None
            backup = backup_dir / f"{original_digest}.jar" if original_digest else None
            if backup:
                if not backup.exists():
                    shutil.copy2(path, backup)
                if digest(backup.read_bytes()) != original_digest:
                    raise ValueError("退避ファイルのハッシュ不一致")
            stat = path.stat() if path.exists() else path.parent.stat()
            temporary.chmod(stat.st_mode & 0o777 if path.exists() else 0o644)
            if hasattr(os, "chown"):
                os.chown(temporary, stat.st_uid, stat.st_gid)
            originals.append({"jar": path.name, "backup": backup.name if backup else None,
                              "datapack": path == compatibility_pack(server_dir),
                              "before": original_digest, "after": digest(temporary.read_bytes())})
        with tempfile.NamedTemporaryFile(dir=backup_dir, suffix=".state", delete=False) as state_file:
            temporary_state = Path(state_file.name)
            try:
                state_file.write(encode({"files": originals}))
                state_file.flush()
                os.fsync(state_file.fileno())
            except BaseException:
                state_file.close()
                temporary_state.unlink(missing_ok=True)
                raise
        try:
            temporary_state.chmod(0o600)
            os.replace(temporary_state, state_path)
        finally:
            temporary_state.unlink(missing_ok=True)
        for path, temporary in prepared:
            os.replace(temporary, path)
        return len(prepared)
    finally:
        for _, temporary in prepared:
            temporary.unlink(missing_ok=True)


def rollback(server_dir: Path, backup_dir: Path) -> None:
    state_path = backup_dir / "pending.json"
    if not state_path.exists():
        return
    state = json.loads(state_path.read_bytes())
    for entry in state["files"]:
        path = compatibility_pack(server_dir) if entry.get("datapack") else checked_jar(server_dir, entry["jar"])
        if entry["before"] is None:
            if not path.exists():
                continue
            if digest(path.read_bytes()) != entry["after"]:
                raise ValueError("修復後にデータパックが変更されているため復元不可")
            path.unlink()
            continue
        backup = backup_dir / entry["backup"]
        if backup.parent != backup_dir or not re.fullmatch(r"[0-9a-f]{64}\.jar", backup.name):
            raise ValueError("不正な退避パス")
        if digest(backup.read_bytes()) != entry["before"]:
            raise ValueError("退避ファイルのハッシュ不一致")
        if digest(path.read_bytes()) not in (entry["before"], entry["after"]):
            raise ValueError("修復後にMODが変更されているため復元不可")
        stat = path.stat()
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".restore", delete=False) as temporary_file:
            temporary = Path(temporary_file.name)
        try:
            shutil.copy2(backup, temporary)
            if digest(temporary.read_bytes()) != entry["before"]:
                raise ValueError("復元用コピーのハッシュ不一致")
            if hasattr(os, "chown"):
                os.chown(temporary, stat.st_uid, stat.st_gid)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    state_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "validate", "apply", "rollback", "finalize", "inventory"))
    parser.add_argument("--server-dir", type=Path, default=Path("/opt/minecraft/forge"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=Path("/opt/minecraft/.forge-data-backups"))
    parser.add_argument("--missing-items", type=Path)
    parser.add_argument("--inventory-output", type=Path)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.action == "plan":
        missing = set(json.loads(args.missing_items.read_bytes())) if args.missing_items else set()
        plan = build_plan(args.server_dir, args.log or args.server_dir / "logs/latest.log", missing)
        args.plan.write_bytes(encode(plan))
        print(f"Planned resources: {len(plan['changes'])}")
    elif args.action in ("apply", "validate"):
        count = apply_plan(args.server_dir, json.loads(args.plan.read_bytes()), args.backup_dir,
                           dry_run=args.action == "validate", inventory_output=args.inventory_output)
        print(f"Changed jars: {count}")
    elif args.action == "rollback":
        rollback(args.server_dir, args.backup_dir)
    elif args.action == "inventory":
        inventory = mod_inventory(args.server_dir)
        if args.manifest:
            manifest = json.loads(args.manifest.read_bytes())
            expected = next(row["mods"] for row in manifest["mod_loaders"] if row["server_id"] == "forge")
            if inventory != expected:
                raise ValueError("Forge MOD一覧がartifact manifestと一致しない")
        print(json.dumps(inventory))
    else:
        (args.backup_dir / "pending.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
