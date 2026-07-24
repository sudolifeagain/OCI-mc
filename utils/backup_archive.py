"""Minecraftワールドのバックアップアーカイブを作成する。"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Sequence

try:
    import pwd
except ImportError:  # pragma: no cover - Windows
    pwd = None


def _resolve_backup_root(base_dir: str, root: str) -> Path:
    """バックアップ対象をベースディレクトリ配下に限定する。"""
    if not root or os.path.isabs(root):
        raise ValueError(f"不正なバックアップ対象である: {root}")

    base_path = Path(base_dir).resolve()
    target_path = (base_path / root).resolve()
    if os.path.commonpath((str(base_path), str(target_path))) != str(base_path):
        raise ValueError(f"バックアップ対象がサーバーディレクトリ外を参照している: {root}")
    return target_path


def create_zip_archive(base_dir: str, target_dirs: Sequence[str], archive_path: str) -> None:
    """指定された対象をZIPへ格納する。"""
    base_path = Path(base_dir).resolve()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root_name in target_dirs:
            root_path = _resolve_backup_root(str(base_path), root_name)
            if root_path.is_symlink():
                raise ValueError(f"シンボリックリンクのバックアップ対象は許可しない: {root_name}")
            if root_path.is_dir():
                for current_root, dirs, files in os.walk(root_path, followlinks=False):
                    dirs.sort()
                    files.sort()
                    for dirname in dirs:
                        directory_path = Path(current_root, dirname)
                        if directory_path.is_symlink():
                            raise ValueError(
                                f"シンボリックリンクを含むワールドはバックアップできない: "
                                f"{directory_path.relative_to(base_path)}"
                            )
                    for filename in files:
                        file_path = Path(current_root, filename)
                        if file_path.is_symlink():
                            raise ValueError(
                                f"シンボリックリンクを含むワールドはバックアップできない: "
                                f"{file_path.relative_to(base_path)}"
                            )
                        archive.write(file_path, file_path.relative_to(base_path))
            elif root_path.is_file():
                archive.write(root_path, root_path.relative_to(base_path))


def build_archive_command(
    run_as_user: str,
    base_dir: str,
    target_dirs: Sequence[str],
    archive_path: str,
) -> list[str]:
    """ゲームサーバーユーザーで実行する圧縮コマンドを生成する。"""
    sudo_path = shutil.which("sudo") or "/usr/bin/sudo"
    command = [
        sudo_path,
        "-n",
        "-H",
        "-u",
        run_as_user,
        "--",
        sys.executable,
        "-m",
        "utils.backup_archive",
        "--base-dir",
        base_dir,
        "--output",
        archive_path,
    ]
    for root in target_dirs:
        command.extend(("--root", root))
    return command


async def create_server_archive(
    base_dir: str,
    target_dirs: Sequence[str],
    archive_path: str,
    run_as_user: str | None,
) -> None:
    """必要に応じてゲームサーバーユーザーへ切り替えてZIPを作成する。"""
    if not run_as_user or os.name != "posix":
        await asyncio.to_thread(create_zip_archive, base_dir, target_dirs, archive_path)
        return

    if pwd is None:
        raise RuntimeError("ゲームサーバーユーザーの解決に失敗した")
    server_user = pwd.getpwnam(run_as_user)
    os.chmod(archive_path, 0o660)
    os.chown(archive_path, -1, server_user.pw_gid)

    command = build_archive_command(run_as_user, base_dir, target_dirs, archive_path)
    project_dir = str(Path(__file__).resolve().parent.parent)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    os.chmod(archive_path, 0o600)
    if process.returncode != 0:
        detail = output.decode("utf-8", errors="replace").strip()
        if len(detail) > 4000:
            detail = detail[-4000:]
        raise RuntimeError(
            f"バックアップ圧縮プロセスが終了コード{process.returncode}で失敗した"
            + (f": {detail}" if detail else "")
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", action="append", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    create_zip_archive(args.base_dir, args.root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
