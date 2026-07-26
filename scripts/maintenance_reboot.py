#!/usr/bin/env python3
"""Minecraftを安全に停止して、必要な場合だけUbuntuを再起動する。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from dotenv import load_dotenv

from scripts.capture_running_servers import load_json, write_state
from scripts.graceful_shutdown import port_is_open, stop_server
from utils.backup_fingerprint import backup_is_acceptable
from utils.rcon import get_rcon_client

try:
    import fcntl
except ImportError:  # pragma: no cover - Linux専用処理をWindowsでも単体テスト可能にする
    fcntl = None

PLAYER_COUNT_PATTERN = re.compile(r"There are (\d+)")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_path.chmod(0o600)
    temp_path.replace(path)


def parse_player_count(response: str) -> int | None:
    match = PLAYER_COUNT_PATTERN.search(response)
    return int(match.group(1)) if match else None


async def get_player_count(server_id: str, config: dict) -> int:
    client = get_rcon_client(config)
    if not client:
        raise RuntimeError(f"{server_id}: RCON設定が利用できない")
    success, response = await client.execute("list")
    if not success:
        raise RuntimeError(f"{server_id}: RCON listに失敗: {response}")
    count = parse_player_count(response)
    if count is None:
        raise RuntimeError(f"{server_id}: プレイヤー数を解析できない")
    return count


def send_discord_notification(message: str) -> bool:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    channel_id = os.getenv("DISCORD_CHANNEL_ID", "").strip()
    if not token or not channel_id:
        logging.warning("Discord通知設定がないため通知をスキップ")
        return False

    request = urllib.request.Request(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        data=json.dumps({"content": message, "allowed_mentions": {"parse": []}}).encode(),
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "OCI-mc-maintenance/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logging.warning("Discord通知に失敗: %s", exc)
        return False


def backup_targets(config: dict, server_id: str) -> list[str]:
    targets = config.get("backup", {}).get("target_dirs", {})
    if isinstance(targets, dict):
        value = targets.get(server_id, ["world"])
    else:
        value = targets
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else ["world"]


def ensure_backups(
    config: dict,
    running_servers: set[str],
    fingerprint_path: Path,
    max_age_seconds: int,
) -> None:
    servers = config.get("servers", {})
    for server_id in sorted(running_servers):
        server = servers.get(server_id, {})
        if not backup_is_acceptable(
            fingerprint_path,
            server_id,
            server["cwd"],
            backup_targets(config, server_id),
            max_age_seconds=max_age_seconds,
        ):
            raise RuntimeError(f"{server_id}: 直近または現行データと一致するバックアップがない")


async def prepare_reboot(args: argparse.Namespace) -> int:
    args.ready.unlink(missing_ok=True)
    if not args.force and not args.reboot_required.exists():
        logging.info("再起動要求がないため処理不要")
        return 0

    config = load_json(args.config)
    servers = config.get("servers", {})
    running_checks = await asyncio.gather(
        *(
            asyncio.to_thread(port_is_open, int(server["port"]))
            for server in servers.values()
        )
    )
    running = {
        server_id
        for server_id, is_running in zip(servers, running_checks, strict=True)
        if is_running
    }
    existing_desired = set(load_json(args.state).get("servers", [])) & set(servers)
    write_state(args.state, existing_desired | running)

    player_counts = await asyncio.gather(
        *(get_player_count(server_id, servers[server_id]) for server_id in sorted(running))
    )
    active = {
        server_id: count
        for server_id, count in zip(sorted(running), player_counts, strict=True)
        if count > 0
    }
    if active:
        summary = ", ".join(f"{server_id}={count}人" for server_id, count in active.items())
        send_discord_notification(f"OS再起動を延期した。オンラインプレイヤー: {summary}")
        logging.info("オンラインプレイヤーがいるため延期: %s", summary)
        return 0

    ensure_backups(config, running, args.fingerprints, args.backup_max_age)
    if args.dry_run:
        logging.info("再起動前検査に成功: %s", ", ".join(sorted(running)) or "稼働サーバーなし")
        return 0

    marker = {
        "started_at": int(time.time()),
        "kernel_before": platform.release(),
        "servers": sorted(existing_desired | running),
    }
    atomic_write_json(args.marker, marker)
    send_discord_notification(
        "Ubuntuの保守再起動を開始する。Minecraftの保存と正常停止を実行中。"
    )

    try:
        results = await asyncio.gather(
            *(
                stop_server(server_id, servers[server_id], allow_active_players=False)
                for server_id in sorted(running)
            )
        )
    except BaseException:
        args.marker.unlink(missing_ok=True)
        raise
    if not all(results):
        args.marker.unlink(missing_ok=True)
        raise RuntimeError("Minecraftサーバーを安全に停止できないため再起動を中止")

    args.ready.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.ready.write_text("ready\n", encoding="utf-8")
    args.ready.chmod(0o600)
    return 0


def post_boot(args: argparse.Namespace) -> int:
    if not args.marker.exists():
        logging.info("保守再起動マーカーがないため処理不要")
        return 0

    marker = load_json(args.marker)
    try:
        subprocess.run(
            [
                sys.executable,
                str(args.verify_script),
                "--config",
                str(args.config),
                "--state",
                str(args.state),
                "--timeout",
                str(args.verify_timeout),
            ],
            check=True,
            timeout=args.verify_timeout + 30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        send_discord_notification(f"Ubuntu保守再起動後のMinecraft復旧確認に失敗した: {exc}")
        raise

    elapsed = int(time.time()) - int(marker.get("started_at", time.time()))
    send_discord_notification(
        f"Ubuntu保守再起動とMinecraft復旧確認が完了した。"
        f" kernel={platform.release()}, elapsed={elapsed}秒"
    )
    args.marker.unlink()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("/opt/minecraft/bot/config.json"))
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/opt/minecraft/.bot-runtime/desired_servers.json"),
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=Path("/opt/minecraft/bot/.env"),
    )
    parser.add_argument(
        "--marker",
        type=Path,
        default=Path("/var/lib/oci-mc-maintenance/reboot-pending.json"),
    )
    parser.add_argument(
        "--ready",
        type=Path,
        default=Path("/run/oci-mc-maintenance/reboot-ready"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument(
        "--fingerprints",
        type=Path,
        default=Path("/opt/minecraft/bot/.backup_fingerprints.json"),
    )
    prepare.add_argument(
        "--reboot-required",
        type=Path,
        default=Path("/run/reboot-required"),
    )
    prepare.add_argument("--backup-max-age", type=int, default=3 * 60 * 60)
    prepare.add_argument("--force", action="store_true")
    prepare.add_argument("--dry-run", action="store_true")

    post = subparsers.add_parser("post-boot")
    post.add_argument(
        "--verify-script",
        type=Path,
        default=Path("/opt/minecraft/bot/scripts/verify_runtime.py"),
    )
    post.add_argument("--verify-timeout", type=int, default=600)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    load_dotenv(args.env)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if fcntl is None:
        raise RuntimeError("このスクリプトはLinux上でのみ実行可能である")

    args.marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = args.marker.parent / "maintenance.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logging.info("別の保守処理が実行中")
            return

        if args.command == "prepare":
            raise SystemExit(asyncio.run(prepare_reboot(args)))
        raise SystemExit(post_boot(args))


if __name__ == "__main__":
    main()
