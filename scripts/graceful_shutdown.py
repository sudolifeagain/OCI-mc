#!/usr/bin/env python3
"""systemd停止前に稼働希望のMinecraftサーバーをRCONで安全に停止する。"""

import argparse
import asyncio
import json
import logging
import re
import socket
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


async def wait_for_port_close(port: int, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await asyncio.to_thread(port_is_open, port):
            return True
        await asyncio.sleep(2)
    return False


async def stop_server(server_id: str, config: dict) -> bool:
    from utils.rcon import get_rcon_client

    port = int(config["port"])
    if not await asyncio.to_thread(port_is_open, port):
        logging.info("Server '%s' is already offline", server_id)
        return True

    client = get_rcon_client(config)
    if not client:
        logging.error("Server '%s' has no usable RCON configuration", server_id)
        return False

    success, response = await client.execute("list")
    if not success:
        logging.error("Server '%s' RCON readiness check failed: %s", server_id, response)
        return False

    match = re.search(r"There are (\d+)", response)
    player_count = int(match.group(1)) if match else 0
    if player_count:
        await client.execute("say サーバーメンテナンスのため60秒後に停止します")
        await asyncio.sleep(50)
        await client.execute("say サーバーメンテナンスのため10秒後に停止します")
        await asyncio.sleep(10)

    # stopは応答前に接続が閉じる場合があるため、終了判定はゲームポートで行う。
    await client.execute("stop")
    if await wait_for_port_close(port):
        logging.info("Server '%s' stopped", server_id)
        return True
    logging.error("Server '%s' did not stop within the timeout", server_id)
    return False


async def async_main(config_path: Path, state_path: Path) -> int:
    config = load_json(config_path)
    servers = config.get("servers", {})
    desired = set(load_json(state_path).get("servers", []))
    tasks = [
        stop_server(server_id, servers[server_id])
        for server_id in sorted(desired)
        if server_id in servers
    ]
    if not tasks:
        logging.info("No desired servers need shutdown")
        return 0
    results = await asyncio.gather(*tasks)
    return 0 if all(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--env", type=Path)
    args = parser.parse_args()

    if args.env:
        load_dotenv(args.env)
    sys.path.insert(0, str(args.config.resolve().parent))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(async_main(args.config, args.state)))


if __name__ == "__main__":
    main()
