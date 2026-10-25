#!/usr/bin/python3
"""固定された原本版のVanillaだけをゲーム用ユーザーで実行する。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


def main() -> int:
    cwd = Path.cwd().resolve()
    root = Path("/opt/minecraft/custom-maps")
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
    return subprocess.call([str(java), f"-Xms{launch['min_memory']}", f"-Xmx{launch['memory']}", "-jar", str(jar), "nogui"])


if __name__ == "__main__":
    raise SystemExit(main())
