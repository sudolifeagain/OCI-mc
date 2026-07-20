#!/usr/bin/env python3
"""Paper jarの検証済み配置、ロールバック、後片付けを管理する。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


TARGET_MINECRAFT_VERSION = "26.2"
USER_AGENT = "OCI-mc-paper-updater/1.0 (+https://github.com/sudolifeagain/OCI-mc)"
DOWNLOAD_HOST = "fill-data.papermc.io"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PaperArtifact:
    version: str
    build_id: int
    channel: str
    url: str
    sha256: str
    size: int | None


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_artifact(manifest_path: Path) -> PaperArtifact:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("artifact manifestがオブジェクトではない")
    if manifest.get("minecraft_version") != TARGET_MINECRAFT_VERSION:
        raise ValueError("Paperの対象Minecraft versionは26.2に固定されている")

    paper = manifest.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("artifact manifestにPaper情報が存在しない")
    version = paper.get("version")
    match = (
        re.fullmatch(r"26\.2-(\d+)", version) if isinstance(version, str) else None
    )
    if match is None:
        raise ValueError("Paper versionが26.2ではない")
    build_id = int(match.group(1))

    channel = paper.get("channel")
    if channel not in {"BETA", "STABLE"}:
        raise ValueError("Paper channelが不正")
    if paper.get("filename") != "paper.jar":
        raise ValueError("Paper配置ファイル名が不正")

    url = paper.get("url")
    expected_source_name = f"paper-{TARGET_MINECRAFT_VERSION}-{build_id}.jar"
    if not isinstance(url, str):
        raise ValueError("Paper download URLが不正")
    parsed_url = urlparse(url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != DOWNLOAD_HOST
        or parsed_url.query
        or parsed_url.fragment
        or Path(parsed_url.path).name != expected_source_name
    ):
        raise ValueError("Paper download URLが許可範囲外")

    hash_info = paper.get("hash")
    if not isinstance(hash_info, dict) or hash_info.get("algorithm") != "sha256":
        raise ValueError("Paper hash algorithmが不正")
    sha256 = hash_info.get("value")
    if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("Paper SHA-256が不正")

    size = paper.get("size")
    if size is not None and (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or size > MAX_ARTIFACT_BYTES
    ):
        raise ValueError("Paper artifact sizeが不正")

    return PaperArtifact(version, build_id, channel, url, sha256, size)


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": USER_AGENT})


def download_artifact(artifact: PaperArtifact, destination: Path) -> None:
    """jarを一時ファイルへ取得し、サイズとSHA-256を検証する。"""

    last_error: BaseException | None = None
    for attempt in range(3):
        digest = hashlib.sha256()
        total = 0
        try:
            with urlopen(_request(artifact.url), timeout=60.0) as response:
                content_length = response.headers.get("Content-Length")
                if artifact.size is not None and content_length is not None:
                    if int(content_length) != artifact.size:
                        raise ValueError("Paper artifact sizeがmanifestと不一致")
                with destination.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_ARTIFACT_BYTES:
                            raise ValueError("Paper artifactが上限サイズを超過")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            break
        except (HTTPError, URLError, HTTPException, TimeoutError, OSError) as error:
            last_error = error
            destination.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(2**attempt)
    else:
        raise RuntimeError(f"Paper artifactの取得に失敗: {last_error}")

    if artifact.size is not None and total != artifact.size:
        destination.unlink(missing_ok=True)
        raise ValueError("Paper artifact sizeがmanifestと不一致")
    if digest.hexdigest() != artifact.sha256:
        destination.unlink(missing_ok=True)
        raise ValueError("Paper artifactのSHA-256検証に失敗")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(data, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        with temporary_path.open("ab") as copied:
            copied.flush()
            os.fsync(copied.fileno())
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _set_permissions(path: Path, owner: str | None, group: str | None) -> None:
    path.chmod(0o640)
    if owner is not None or group is not None:
        shutil.chown(path, user=owner, group=group)


def _validate_state_paths(
    state: dict[str, Any], target: Path, backup: Path
) -> tuple[str | None, str]:
    if state.get("target") != str(target.resolve()):
        raise ValueError("Paper rollback stateのtargetが不正")
    if state.get("backup") != str(backup.resolve()):
        raise ValueError("Paper rollback stateのbackupが不正")
    previous_sha256 = state.get("previous_sha256")
    if previous_sha256 is not None and (
        not isinstance(previous_sha256, str)
        or SHA256_PATTERN.fullmatch(previous_sha256) is None
    ):
        raise ValueError("Paper rollback stateの旧SHA-256が不正")
    expected_sha256 = state.get("expected_sha256")
    if not isinstance(expected_sha256, str) or SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise ValueError("Paper rollback stateの新SHA-256が不正")
    return previous_sha256, expected_sha256


def rollback_artifact(
    target: Path,
    state_path: Path,
    backup_path: Path,
    owner: str | None,
    group: str | None,
) -> bool:
    """未確定のPaper更新を旧jarへ戻す。"""

    if not state_path.exists():
        backup_path.unlink(missing_ok=True)
        return False

    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Paper rollback stateが不正")
    previous_sha256, _ = _validate_state_paths(state, target, backup_path)

    if previous_sha256 is None:
        target.unlink(missing_ok=True)
    else:
        if not backup_path.is_file():
            raise FileNotFoundError("Paper旧jarが存在しない")
        if hash_file(backup_path) != previous_sha256:
            raise ValueError("Paper旧jarのSHA-256検証に失敗")
        _copy_atomic(backup_path, target)
        _set_permissions(target, owner, group)

    backup_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return True


def install_artifact(
    artifact: PaperArtifact,
    target: Path,
    state_path: Path,
    backup_path: Path,
    owner: str | None,
    group: str | None,
) -> bool:
    """検証済みPaper jarを配置し、ロールバック情報を残す。"""

    target = target.resolve()
    state_path = state_path.resolve()
    backup_path = backup_path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if state_path.exists():
        rollback_artifact(target, state_path, backup_path, owner, group)

    current_sha256 = hash_file(target) if target.is_file() else None
    if current_sha256 == artifact.sha256:
        backup_path.unlink(missing_ok=True)
        return False

    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix=".paper-download-", suffix=".jar", delete=False
    ) as temporary:
        downloaded_path = Path(temporary.name)
    downloaded_path.unlink()

    try:
        download_artifact(artifact, downloaded_path)
        if current_sha256 is not None:
            _copy_atomic(target, backup_path)
            if hash_file(backup_path) != current_sha256:
                raise ValueError("Paper旧jarの退避検証に失敗")
        else:
            backup_path.unlink(missing_ok=True)

        _write_json_atomic(
            state_path,
            {
                "schema_version": 1,
                "target": str(target),
                "backup": str(backup_path),
                "previous_sha256": current_sha256,
                "expected_sha256": artifact.sha256,
                "version": artifact.version,
            },
        )
        os.replace(downloaded_path, target)
        _set_permissions(target, owner, group)
        if hash_file(target) != artifact.sha256:
            raise ValueError("配置後のPaper jar検証に失敗")
        return True
    except BaseException:
        if state_path.exists():
            rollback_artifact(target, state_path, backup_path, owner, group)
        raise
    finally:
        downloaded_path.unlink(missing_ok=True)


def finalize_artifact(target: Path, state_path: Path, backup_path: Path) -> bool:
    """起動確認後に旧jarとrollback stateを削除する。"""

    target = target.resolve()
    state_path = state_path.resolve()
    backup_path = backup_path.resolve()
    if not state_path.exists():
        backup_path.unlink(missing_ok=True)
        return False

    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise ValueError("Paper rollback stateが不正")
    _, expected_sha256 = _validate_state_paths(state, target, backup_path)
    if not target.is_file() or hash_file(target) != expected_sha256:
        raise ValueError("稼働確認後のPaper jar検証に失敗")

    backup_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "rollback", "finalize"))
    parser.add_argument("--manifest", type=Path, default=Path("server-artifacts.json"))
    parser.add_argument("--target", type=Path, default=Path("/opt/minecraft/paper/paper.jar"))
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("/opt/minecraft/.bot-runtime/paper_artifact_state.json"),
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=Path("/opt/minecraft/.bot-runtime/paper.jar.previous"),
    )
    parser.add_argument("--owner", default="mc-paper")
    parser.add_argument("--group", default="mc-paper")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "install":
            artifact = load_artifact(args.manifest)
            changed = install_artifact(
                artifact,
                args.target,
                args.state,
                args.backup,
                args.owner,
                args.group,
            )
            print(f"Paper {artifact.version}: {'更新' if changed else '変更なし'}")
        elif args.command == "rollback":
            changed = rollback_artifact(
                args.target,
                args.state,
                args.backup,
                args.owner,
                args.group,
            )
            print(f"Paper rollback: {'復元' if changed else '対象なし'}")
        else:
            changed = finalize_artifact(args.target, args.state, args.backup)
            print(f"Paper旧jar削除: {'完了' if changed else '対象なし'}")
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"Paper artifact管理に失敗: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
