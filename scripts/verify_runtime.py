#!/usr/bin/env python3
"""デプロイ後にゲームポートとRCON応答で起動完了を確認する。"""

import argparse
import asyncio
import json
import socket
import time
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.rcon import get_rcon_client  # noqa: E402


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


async def server_ready(server: dict) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(server["port"])), timeout=2):
            pass
    except OSError:
        return False
    if not server.get("rcon_port"):
        return True
    client = get_rcon_client(server)
    if client is None:
        return False
    success, response = await client.execute("list")
    return success and "players online" in response


async def verify(config: dict, desired: set[str], timeout: int) -> None:
    servers = config.get("servers", {})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = []
        for server_id in sorted(desired):
            server = servers.get(server_id)
            if not server or not await server_ready(server):
                pending.append(server_id)
        if not pending:
            print("Runtime restored: " + (", ".join(sorted(desired)) or "no servers requested"))
            return
        print("Waiting for: " + ", ".join(pending), flush=True)
        await asyncio.sleep(5)
    raise SystemExit("Runtime restore timed out")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()

    config = load_json(args.config)
    desired = set(load_json(args.state).get("servers", []))
    load_dotenv(args.env or args.config.parent / ".env")
    asyncio.run(verify(config, desired, args.timeout))


if __name__ == "__main__":
    main()
