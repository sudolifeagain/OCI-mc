#!/usr/bin/python3
"""固定された原本版のVanillaだけをゲーム用ユーザーで実行する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.custom_map_resource_fixes import apply_fixes  # noqa: E402


LOGGING_CHECKSUMS = {
    "log4j2_112-116.xml": "29534615b561487bccd3f2ec859b172bc642096cef4f8606754ead7eca5050dd",
    "log4j2_17-111.xml": "c04eb15a3b6b0cc5642a439793c295172491a86da34c176403752fac61bafd72",
}


def logging_arguments(root: Path, version: str) -> list[str]:
    numbers = tuple(int(v) for v in version.split("."))
    if numbers[0] == 1 and numbers[1] <= 16:
        name = "log4j2_112-116.xml" if numbers[1] >= 12 else "log4j2_17-111.xml"
        path = root / "artifacts" / "logging" / name
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != LOGGING_CHECKSUMS[name]:
                raise ValueError("旧版用の公式ログ設定が一致しない")
        return [f"-Dlog4j.configurationFile={path}"]
    if numbers[0] == 1 and (numbers[1] == 17 or numbers == (1, 18)):
        return ["-Dlog4j2.formatMsgNoLookups=true"]
    return []


def launch_server(cwd: Path, root: Path) -> int:
    cwd, root = cwd.resolve(), root.resolve()
    if cwd not in (root / "slots" / "custom-1", root / "slots" / "custom-2"):
        raise ValueError("カスタムマップの起動枠ではない")
    launch = json.loads((cwd / "launch.json").read_text())
    version = launch["version"]
    if not re.fullmatch(r"(?:1\.\d+(?:\.\d+)?|\d{2}\.\d+(?:\.\d+)?)", version):
        raise ValueError("Minecraft版が不正である")
    for field in ("memory", "min_memory"):
        if not re.fullmatch(r"\d+[GM]", launch[field]):
            raise ValueError("ヒープ指定が不正である")
    jar = root / "artifacts" / version / "server.jar"
    with jar.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != launch["sha256"]:
            raise ValueError("サーバーjarのSHA-256が一致しない")
    major = int(launch["java"])
    if major == 8:
        java = root / "java8" / "bin" / "java"
    elif major in (16, 17, 21, 25):
        java = Path(f"/usr/lib/jvm/java-{17 if major == 16 else major}-openjdk-arm64/bin/java")
    else:
        raise ValueError("未対応のJava実行環境である")
    repaired = apply_fixes(cwd, version)
    if repaired:
        print(f"カスタムマップの既知リソース不備を修復: {len(repaired)}件", flush=True)
    return subprocess.call([str(java), *logging_arguments(root, version), f"-Xms{launch['min_memory']}", f"-Xmx{launch['memory']}", "-jar", str(jar), "nogui"])


def main() -> int:
    return launch_server(Path.cwd(), Path("/opt/minecraft/custom-maps"))


if __name__ == "__main__":
    raise SystemExit(main())
