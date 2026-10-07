#!/usr/bin/env python3
"""起動済みForgeのデータ読み込み失敗をデプロイ確定前に検出する。"""

import argparse
import re
from pathlib import Path


DATA_ERRORS = re.compile(
    r"Parsing error loading (?:recipe|custom advancement) "
    r"|Couldn't parse element loot_tables:"
    r"|Couldn't (?:read tag list|load tag |load advancement )"
    r"|Invalid (?:biome|dimension) ID in config"
)


def validate_log(path: Path) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not any("Done (" in line for line in lines):
        raise ValueError("Forgeの起動完了ログがない")
    count = sum(bool(DATA_ERRORS.search(line)) for line in lines)
    if count:
        raise ValueError(f"Forgeデータ読み込みエラー: {count}件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    validate_log(args.log)
    print("Forge data loading verified")
