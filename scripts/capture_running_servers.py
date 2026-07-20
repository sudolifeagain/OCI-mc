#!/usr/bin/env python3
"""デプロイ前の稼働サーバーを永続化された希望状態へ反映する。"""

import argparse
import json
import os
import time
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def find_running_servers(servers: dict) -> set[str]:
    running: set[str] = set()
    expected_cwds = {
        os.path.normpath(config["cwd"]): server_id
        for server_id, config in servers.items()
    }
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            cmdline = (proc_dir / "cmdline").read_bytes().replace(b"\0", b" ").lower()
            if b"java" not in cmdline:
                continue
            cwd = os.path.normpath(os.readlink(proc_dir / "cwd"))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        server_id = expected_cwds.get(cwd)
        if server_id:
            running.add(server_id)
    return running


def write_state(path: Path, servers: set[str]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps({"servers": sorted(servers), "updated_at": int(time.time())}),
        encoding="utf-8",
    )
    temp_path.chmod(0o600)
    temp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()

    config = load_json(args.config)
    servers = config.get("servers", {})
    known_ids = set(servers)
    existing = set(load_json(args.state).get("servers", [])) & known_ids
    desired = existing | find_running_servers(servers)
    write_state(args.state, desired)
    print("Preserved desired servers: " + ", ".join(sorted(desired)))


if __name__ == "__main__":
    main()
