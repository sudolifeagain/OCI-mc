#!/usr/bin/env python3
"""デプロイ後に稼働希望サーバーのポート復旧を確認する。"""

import argparse
import json
import socket
import time
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    config = load_json(args.config)
    servers = config.get("servers", {})
    desired = set(load_json(args.state).get("servers", []))
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        pending = []
        for server_id in sorted(desired):
            server = servers.get(server_id)
            if not server:
                pending.append(server_id)
                continue
            try:
                with socket.create_connection(("127.0.0.1", int(server["port"])), timeout=2):
                    pass
            except OSError:
                pending.append(server_id)
        if not pending:
            print("Runtime restored: " + (", ".join(sorted(desired)) or "no servers requested"))
            return
        print("Waiting for: " + ", ".join(pending), flush=True)
        time.sleep(5)

    raise SystemExit("Runtime restore timed out")


if __name__ == "__main__":
    main()
