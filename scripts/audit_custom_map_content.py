#!/usr/bin/env python3
"""原本を変更せず、プレイヤー操作の権限要件とリソースIDを監査する。"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import gzip
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.custom_maps.nbt import read_nbt_bytes  # noqa: E402


NON_OP_COMMANDS = {"trigger", "help", "list", "me", "msg", "tell", "w", "teammsg", "tm"}
DATA_LOAD_ERROR = re.compile(r"Failed to load (?:function|data.?packs)|Could(?:n't| not) (?:parse data file|load (?:tag|advancement|recipe|loot|predicate))|Parsing error loading (?:recipe|custom advancement)|Non \[a-z0-9/._-\] character|Invalid path in pack|ignored non-lowercase namespace|[Ii]nvalid namespace|Missing (?:required )?data pack file/|(?:Failed to|Couldn't) get pack info")
VALUE = re.compile(r'''["']?(?:value|command)["']?\s*:\s*("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''')
QUOTED = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.X)


def literal(text: str) -> str:
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            pass
    return re.sub(r"\\(['\"\\])", r"\1", text[1:-1])


def command_root(command: str) -> str:
    command = command.strip().lstrip("/")
    if not command:
        return ""
    root = command.split()[0]
    if root != "execute":
        return root
    quoted, escaped, depth = "", False, 0
    for i, char in enumerate(command):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quoted:
                quoted = ""
        elif char in "\"'":
            quoted = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif depth == 0 and command.startswith(" run ", i):
            return command_root(command[i + 5:])
    legacy = command.split(maxsplit=5)
    if len(legacy) == 6 and all(re.fullmatch(r"(?:~[-\d.]*|[-\d.]+)", c) for c in legacy[2:5]):
        nested = legacy[5]
        if nested.startswith("detect "):
            parts = nested.split(maxsplit=6)
            nested = parts[6] if len(parts) == 7 else ""
        return command_root(nested)
    return root


def requires_operator(command: str) -> bool:
    words = command.strip().lstrip("/").split()
    return bool(words) and words[0] not in NON_OP_COMMANDS


def click_commands(value: object, depth: int = 0) -> set[str]:
    if depth > 12:
        return set()
    found = set()
    if isinstance(value, dict):
        if value.get("action") == "run_command":
            command = value.get("command", value.get("value"))
            if isinstance(command, str) and command.strip():
                found.add(command)
        for child in value.values():
            found.update(click_commands(child, depth + 1))
    elif isinstance(value, list):
        for child in value:
            found.update(click_commands(child, depth + 1))
    elif isinstance(value, str) and "run_command" in value:
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = None
        if decoded is not None and decoded != value:
            return click_commands(decoded, depth + 1)
        # SNBTの本に埋め込まれたJSON文字列を先に復元する。
        tokens = list(QUOTED.finditer(value))
        for token in tokens:
            decoded = literal(token.group())
            if "run_command" in decoded and decoded != value:
                found.update(click_commands(decoded, depth + 1))
        # tellrawのJSON、および新形式のSNBT click_event。
        for event in re.finditer(r'(?:clickEvent|click_event)["\']?\s*:\s*\{', value):
            if any(token.start() <= event.start() < token.end() and literal(token.group()) not in ("clickEvent", "click_event") for token in tokens):
                continue
            start, quote, escape, level = event.end() - 1, "", False, 0
            for end in range(start, len(value)):
                char = value[end]
                if quote:
                    if escape:
                        escape = False
                    elif char == "\\":
                        escape = True
                    elif char == quote:
                        quote = ""
                elif char in "\"'":
                    quote = char
                elif char == "{":
                    level += 1
                elif char == "}":
                    level -= 1
                    if level == 0:
                        body = value[start:end + 1]
                        if re.search(r'''["']?action["']?\s*:\s*["']run_command["']''', body):
                            found.update(literal(m.group(1)) for m in VALUE.finditer(body))
                        break
    return {command for command in found if command.strip()}


def player_ui_commands(value: object, source: str) -> list[dict]:
    found = []
    if isinstance(value, dict):
        if value.get("id", value.get("name")) in ("minecraft:written_book", "written_book", 387) or "minecraft:written_book_content" in value:
            name = value.get("tag", {}).get("display", {}).get("Name", "")
            return [{"source": source, "kind": "book", "command": c, "item_name": name} for c in sorted(click_commands(value))]
        command = value.get("Command")
        if isinstance(command, str) and is_player_ui(command):
            found.extend({"source": source, "kind": "command_block_ui", "command": c} for c in sorted(click_commands(command)))
        for key, child in value.items():
            if key not in ("Sections", "sections", "Heightmaps", "Player", "player"):
                found.extend(player_ui_commands(child, source))
    elif isinstance(value, list):
        for child in value:
            found.extend(player_ui_commands(child, source))
    return found


def is_player_ui(command: str) -> bool:
    root = command_root(command)
    # 看板アイテムのclickEventは、設置後に看板の権限で実行される。
    return root == "tellraw" or (root == "give" and bool(re.search(r"\b(?:minecraft:)?written_book\b", command)))


def datapack_errors(log: Path) -> list[str]:
    if not log.is_file():
        raise RuntimeError("データパック検証用のサーバーログが見つからない")
    return [line for line in log.read_text(encoding="utf-8", errors="replace").splitlines() if DATA_LOAD_ERROR.search(line)]


def assert_verified_content(item: dict, server: Path) -> dict:
    errors = datapack_errors(server / "logs/latest.log")
    if errors:
        raise RuntimeError("起動・データパックのエラーがある: " + " | ".join(errors[:4]))
    report = audit_world(item, server / "world")
    if report["scan_errors"]:
        raise RuntimeError("原本の権限検査を完了できない: " + str(report["scan_errors"][:2]))
    if report["privileged_commands"] and not item.get("op_required"):
        raise RuntimeError("本・チャットのクリック操作にOP権限が必要である。op_requiredを確認する必要がある")
    return {"datapack_errors": [], "privileged_ui_commands": len(report["privileged_commands"]), "content_permissions_verified": True}


def region_chunks(path: Path) -> Iterator[dict]:
    with path.open("rb") as stream:
        header = stream.read(4096)
        for i in range(1024):
            offset = int.from_bytes(header[i * 4:i * 4 + 3], "big") * 4096
            if not offset:
                continue
            stream.seek(offset)
            length, compression = int.from_bytes(stream.read(4), "big"), stream.read(1)[0]
            raw = stream.read(length - 1)
            if compression == 2:
                raw = zlib.decompress(raw)
            elif compression == 1:
                raw = gzip.decompress(raw)
            elif compression != 3:
                raise ValueError(f"未対応のリージョン圧縮: {compression}")
            yield read_nbt_bytes(raw)


def pack_files(path: Path) -> Iterator[tuple[str, bytes]]:
    if path.is_dir():
        for file in path.rglob("*"):
            if file.is_file() and (file.relative_to(path).as_posix().startswith("data/") or file.name == "pack.mcmeta"):
                yield file.relative_to(path).as_posix(), file.read_bytes()
    elif path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name.startswith("data/") and not name.endswith("/") or name == "pack.mcmeta":
                    yield name, archive.read(name)


def audit_world(item: dict, world: Path) -> dict:
    result = {"id": item["id"], "version": item["mc_version"], "revision": item["revision"], "op_required": bool(item.get("op_required")), "clicks": [], "pack_issues": [], "chunks": 0, "scan_errors": []}
    if not world.is_dir():
        result["scan_errors"].append({"file": str(world), "error": "ワールドが見つからない"})
    for function in sorted((world / "data/functions").rglob("*.mcfunction")):
        for number, line in enumerate(function.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.lstrip().startswith("#") and is_player_ui(line):
                result["clicks"].extend({"source": f"{function.relative_to(world).as_posix()}:{number}", "kind": "legacy_function_ui", "command": c} for c in sorted(click_commands(line)))
    for pack in sorted((world / "datapacks").glob("*")):
        if not (pack.is_dir() or pack.suffix == ".zip"):
            continue
        files = list(pack_files(pack))
        if not any(name == "pack.mcmeta" for name, _ in files):
            result["pack_issues"].append({"pack": pack.name, "issue": "missing-pack-meta"})
        for name, raw in files:
            if not name.startswith("data/"):
                continue
            if not re.fullmatch(r"[a-z0-9/._-]+", name[5:]):
                result["pack_issues"].append({"pack": pack.name, "path": name, "issue": "invalid-resource-id", "sha256": hashlib.sha256(raw).hexdigest()})
            if name.endswith(".json") and not raw.strip():
                result["pack_issues"].append({"pack": pack.name, "path": name, "issue": "empty-json", "sha256": hashlib.sha256(raw).hexdigest()})
            if name.endswith(".mcfunction"):
                for number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if not line.lstrip().startswith("#") and is_player_ui(line):
                        result["clicks"].extend({"source": f"{pack.name}/{name}:{number}", "kind": "function_ui", "command": c} for c in sorted(click_commands(line)))
            elif name.endswith(".json"):
                try:
                    result["clicks"].extend(player_ui_commands(json.loads(raw), pack.name + "/" + name))
                except (ValueError, UnicodeDecodeError):
                    pass  # JSONの受理可否は指定版のMinecraftで確認する。
    for directory in sorted(world.rglob("region")) + sorted(world.rglob("entities")):
        if not directory.is_dir():
            continue
        for region in directory.glob("*.mca"):
            try:
                for chunk in region_chunks(region):
                    result["chunks"] += 1
                    level = chunk.get("Level", chunk)
                    for entry in level.get("TileEntities", level.get("block_entities", [])) + level.get("Entities", level.get("entities", [])):
                        location = region.relative_to(world).as_posix() + ":" + str([entry.get(k) for k in ("x", "y", "z")])
                        result["clicks"].extend(player_ui_commands(entry, location))
            except (ValueError, IndexError, OSError, EOFError, struct.error, zlib.error) as error:
                result["scan_errors"].append({"file": region.relative_to(world).as_posix(), "error": str(error)})
    result["clicks"] = list({(c["source"], c["command"]): c for c in result["clicks"]}.values())
    result["privileged_commands"] = sorted({c["command"] for c in result["clicks"] if requires_operator(c["command"])})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/minecraft/custom-maps"))
    parser.add_argument("--catalog", type=Path, default=Path("custom-maps.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--map", action="append")
    args = parser.parse_args()
    results = []
    for item in json.loads(args.catalog.read_text(encoding="utf-8"))["maps"]:
        if not item.get("published") or (args.map and item["id"] not in args.map):
            continue
        result = audit_world(item, args.root / "templates" / item["id"] / item["revision"] / "world")
        results.append(result)
        args.output.write_text(json.dumps({"maps": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"id": result["id"], "privileged_commands": len(result["privileged_commands"]), "pack_issues": len(result["pack_issues"]), "scan_errors": len(result["scan_errors"])}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
