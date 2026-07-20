#!/usr/bin/env python3
"""Paper 26.2の最新STABLEを検知してartifact manifestを更新する。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


TARGET_MINECRAFT_VERSION = "26.2"
PAPER_API_URL = (
    "https://fill.papermc.io/v3/projects/paper/versions/"
    f"{TARGET_MINECRAFT_VERSION}/builds"
)
USER_AGENT = "OCI-mc-paper-updater/1.0 (+https://github.com/sudolifeagain/OCI-mc)"
DOWNLOAD_HOST = "fill-data.papermc.io"
MAX_API_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PaperBuild:
    """検証済みPaper buildメタデータ。"""

    build_id: int
    channel: str
    name: str
    url: str
    sha256: str
    size: int


def _request(url: str) -> Request:
    return Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})


def _read_url(url: str, timeout: float, max_bytes: int) -> bytes:
    """一時的な通信障害を再試行し、上限付きでURLを読み取る。"""

    last_error: BaseException | None = None
    for attempt in range(3):
        try:
            with urlopen(_request(url), timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise ValueError(f"応答サイズが上限を超過: {content_length}")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("応答サイズが上限を超過")
                return data
        except (HTTPError, URLError, HTTPException, TimeoutError, OSError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(2**attempt)
    raise RuntimeError(f"Paper APIへの接続に失敗: {last_error}")


def fetch_builds(timeout: float = 30.0) -> list[dict[str, Any]]:
    """Paper Downloads Serviceから26.2のbuild一覧を取得する。"""

    raw = _read_url(PAPER_API_URL, timeout, MAX_API_BYTES)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("Paper API応答が配列ではない")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("Paper API応答に不正なbuildが存在")
    return data


def _parse_stable_build(raw: dict[str, Any]) -> PaperBuild:
    if raw.get("channel") != "STABLE":
        raise ValueError("STABLE以外のbuildは選択不可")

    build_id = raw.get("id")
    if not isinstance(build_id, int) or isinstance(build_id, bool) or build_id < 1:
        raise ValueError("Paper build IDが不正")

    downloads = raw.get("downloads")
    if not isinstance(downloads, dict):
        raise ValueError("Paper download情報が不正")
    download = downloads.get("server:default")
    if not isinstance(download, dict):
        raise ValueError("Paper server downloadが存在しない")

    expected_name = f"paper-{TARGET_MINECRAFT_VERSION}-{build_id}.jar"
    name = download.get("name")
    if name != expected_name:
        raise ValueError(f"Paperファイル名が不正: {name}")

    url = download.get("url")
    if not isinstance(url, str):
        raise ValueError("Paper download URLが不正")
    parsed_url = urlparse(url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != DOWNLOAD_HOST
        or parsed_url.query
        or parsed_url.fragment
        or Path(parsed_url.path).name != expected_name
    ):
        raise ValueError(f"許可されていないPaper download URL: {url}")

    checksums = download.get("checksums")
    if not isinstance(checksums, dict):
        raise ValueError("Paper checksum情報が不正")
    sha256 = checksums.get("sha256")
    if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError("Paper SHA-256が不正")

    size = download.get("size")
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or size > MAX_ARTIFACT_BYTES
    ):
        raise ValueError("Paper artifact sizeが不正")

    return PaperBuild(build_id, "STABLE", name, url, sha256, size)


def select_latest_stable(builds: list[dict[str, Any]]) -> PaperBuild | None:
    """26.2のSTABLEから最大build IDを選択する。"""

    stable_builds = [
        _parse_stable_build(build)
        for build in builds
        if build.get("channel") == "STABLE"
    ]
    return max(stable_builds, key=lambda build: build.build_id, default=None)


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("artifact manifestがオブジェクトではない")
    if data.get("minecraft_version") != TARGET_MINECRAFT_VERSION:
        raise ValueError(
            f"対象Minecraft versionは{TARGET_MINECRAFT_VERSION}に固定されている"
        )
    paper = data.get("paper")
    if not isinstance(paper, dict):
        raise ValueError("artifact manifestにPaper情報が存在しない")
    version = paper.get("version")
    if not isinstance(version, str) or re.fullmatch(
        rf"{re.escape(TARGET_MINECRAFT_VERSION)}-\d+", version
    ) is None:
        raise ValueError("artifact manifestのPaper versionが26.2ではない")
    return data


def update_required(manifest: dict[str, Any], build: PaperBuild) -> bool:
    """BETAからSTABLEへの移行、または新しいSTABLEへの更新を判定する。"""

    paper = manifest["paper"]
    current_build_id = int(paper["version"].rsplit("-", 1)[1])
    return paper.get("channel") != "STABLE" or current_build_id < build.build_id


def verify_download(build: PaperBuild, timeout: float = 60.0) -> None:
    """候補jarを一時取得し、サイズとSHA-256を独立検証する。"""

    digest = hashlib.sha256()
    total = 0
    last_error: BaseException | None = None
    for attempt in range(3):
        try:
            with urlopen(_request(build.url), timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) != build.size:
                    raise ValueError("Paper artifactのContent-LengthがAPI情報と不一致")
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARTIFACT_BYTES:
                        raise ValueError("Paper artifactが上限サイズを超過")
                    digest.update(chunk)
            break
        except (HTTPError, URLError, HTTPException, TimeoutError, OSError) as error:
            last_error = error
            total = 0
            digest = hashlib.sha256()
            if attempt < 2:
                time.sleep(2**attempt)
    else:
        raise RuntimeError(f"Paper artifactの取得に失敗: {last_error}")

    if total != build.size:
        raise ValueError("Paper artifact sizeがAPI情報と不一致")
    if digest.hexdigest() != build.sha256:
        raise ValueError("Paper artifactのSHA-256検証に失敗")


def build_updated_manifest(
    manifest: dict[str, Any], build: PaperBuild, updated_at: str
) -> dict[str, Any]:
    updated = copy.deepcopy(manifest)
    updated["updated_at"] = updated_at
    updated["minecraft_version"] = TARGET_MINECRAFT_VERSION
    updated["paper"] = {
        "version": f"{TARGET_MINECRAFT_VERSION}-{build.build_id}",
        "channel": "STABLE",
        "filename": "paper.jar",
        "source_filename": build.name,
        "url": build.url,
        "size": build.size,
        "hash": {"algorithm": "sha256", "value": build.sha256},
    }
    return updated


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """manifestを同一ディレクトリ内でatomicに置換する。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(manifest, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def write_outputs(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("GitHub Actions outputに改行は使用不可")
            output.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("server-artifacts.json"))
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = args.github_output
    if output_path is None and os.environ.get("GITHUB_OUTPUT"):
        output_path = Path(os.environ["GITHUB_OUTPUT"])

    try:
        manifest = load_manifest(args.manifest)
        build = select_latest_stable(fetch_builds())
        if build is None:
            write_outputs(output_path, {"update_available": "false"})
            print(f"Paper {TARGET_MINECRAFT_VERSION}のSTABLEは未公開")
            return 0
        if not update_required(manifest, build):
            write_outputs(
                output_path,
                {"update_available": "false", "build": str(build.build_id)},
            )
            print(f"Paper {TARGET_MINECRAFT_VERSION}-{build.build_id} STABLEは適用済み")
            return 0

        verify_download(build)
        updated_at = datetime.now(timezone.utc).date().isoformat()
        write_manifest(args.manifest, build_updated_manifest(manifest, build, updated_at))
        write_outputs(
            output_path,
            {
                "update_available": "true",
                "build": str(build.build_id),
                "version": f"{TARGET_MINECRAFT_VERSION}-{build.build_id}",
                "sha256": build.sha256,
            },
        )
        print(f"Paper {TARGET_MINECRAFT_VERSION}-{build.build_id} STABLEを検知")
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as error:
        print(f"Paper STABLE検知に失敗: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
