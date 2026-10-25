"""原本と公式Vanilla実行環境の検証付き配置。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import socket
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

import requests

from utils.custom_maps.catalog import valid_id

MAX_DOWNLOAD = 1024 ** 3
MAX_EXPANDED = 5 * 1024 ** 3


def public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("取得元は公開HTTPS URLである必要がある")
    for address in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("非公開ネットワークへの取得は許可しない")
    return url


def download(url: str, destination: Path, *, sha1: str | None = None, sha256: str | None = None) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        session.headers["User-Agent"] = "Mozilla/5.0 (compatible; OCI-mc map installer)"
        response = None
        for _ in range(8):
            response = session.get(public_url(url), stream=True, allow_redirects=False, timeout=(15, 90))
            if response.is_redirect:
                url = urljoin(url, response.headers["Location"])
                response.close()
                continue
            response.raise_for_status()
            break
        else:
            raise ValueError("取得元のリダイレクトが多すぎる")
        fd, tmp_name = tempfile.mkstemp(dir=destination.parent, prefix=".download-")
        digest, legacy, size = hashlib.sha256(), hashlib.sha1(), 0
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise ValueError("取得ファイルが上限を超えた")
                    digest.update(chunk)
                    legacy.update(chunk)
                    out.write(chunk)
            if sha1 and legacy.hexdigest() != sha1:
                raise ValueError("公式配布物のSHA-1が一致しない")
            if sha256 and digest.hexdigest() != sha256:
                raise ValueError("配布物のSHA-256が一致しない")
            os.replace(tmp_name, destination)
            return digest.hexdigest()
        finally:
            response.close()
            Path(tmp_name).unlink(missing_ok=True)


def safe_extract(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        total = sum(item.file_size for item in members)
        if total > MAX_EXPANDED or len(members) > 150000:
            raise ValueError("展開量が上限を超える")
        if shutil.disk_usage(destination.parent).free < total + 512 * 1024 ** 2:
            raise ValueError("展開に必要なディスク容量が不足している")
        for item in members:
            name = item.filename.replace("\\", "/")
            path = PurePosixPath(name)
            mode = item.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or ":" in name or "\x00" in name or stat.S_ISLNK(mode):
                raise ValueError("不正なアーカイブパスを検出した")
            target = destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("展開先の外部参照を検出した")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, target.open("wb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)


def import_archive(root: Path, item: dict, archive: Path, *, world_path: str | None = None) -> dict:
    map_id, revision = valid_id(item["id"]), valid_id(item["revision"])
    target = root / "templates" / map_id / revision
    if target.exists():
        raise ValueError("原本の同じ改訂は上書きできない")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".import-") as temporary:
        stage = Path(temporary)
        unpacked = stage / "unpacked"
        unpacked.mkdir()
        safe_extract(archive, unpacked)
        if not any(unpacked.rglob("level.dat")):
            nested = []
            for candidate in unpacked.rglob("*.zip"):
                with zipfile.ZipFile(candidate) as zipped:
                    if any(n.endswith("level.dat") for n in zipped.namelist()):
                        nested.append(candidate)
            if len(nested) == 1:
                inside = unpacked / "_nested_world"
                inside.mkdir()
                safe_extract(nested[0], inside)
        if world_path:
            world = (unpacked / world_path).resolve()
            if not world.is_relative_to(unpacked.resolve()) or not (world / "level.dat").is_file():
                raise ValueError("指定されたワールド位置が不正である")
        else:
            worlds = [p.parent for p in unpacked.rglob("level.dat") if not any(s.startswith(".") for s in p.relative_to(unpacked).parts)]
            if len(worlds) != 1:
                raise ValueError(f"ワールド候補が{len(worlds)}件ある。world_pathの指定が必要である")
            world = worlds[0]
        final = stage / "template"
        final.mkdir()
        shutil.copytree(world, final / "world")
        props = next((p for p in (world / "server.properties", world.parent / "server.properties", unpacked / "server.properties") if p.is_file()), None)
        if props:
            shutil.copyfile(props, final / "author.properties")
        item = dict(item)
        from utils.custom_maps.nbt import world_metadata
        metadata = world_metadata(world / "level.dat")
        if metadata["version"] and metadata["version"] != item["mc_version"]:
            raise ValueError(f"原本の保存版 {metadata['version']} と指定版が異なる。管理者の確認が必要である")
        settings = dict(item.get("server_properties", {}))
        if metadata["difficulty"] is not None:
            settings.setdefault("difficulty", str(metadata["difficulty"]))
        item["server_properties"] = settings
        packs = []
        for candidate in unpacked.rglob("*.zip"):
            with zipfile.ZipFile(candidate) as zipped:
                if any(n.endswith("pack.mcmeta") for n in zipped.namelist()):
                    packs.append(candidate)
        if item.get("resource_pack") and packs:
            pack = next((p for p in packs if p.parent == world), packs[0])
            filename = pack.name
            try:
                filename = filename.encode("cp437").decode("cp932")
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
            with pack.open("rb") as stream:
                item["resource_pack"] = {**item["resource_pack"], "filename": filename, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        from utils.custom_maps.catalog import requirements_hash
        item["requirements_hash"] = requirements_hash(item)
        with archive.open("rb") as stream:
            archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {"map_id": map_id, "revision": revision, "mc_version": item["mc_version"], "archive_sha256": archive_hash, "world_metadata": metadata, "catalog": item, "smoke_verified": False}
        (final / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(final, target)
    return manifest


def ensure_vanilla(root: Path, version: str) -> dict:
    target = root / "artifacts" / version
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(root / "artifacts", 0o755)
    os.chmod(target, 0o755)
    metadata_file = target / "verified.json"
    if metadata_file.exists():
        metadata = json.loads(metadata_file.read_text())
        with (target / "server.jar").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != metadata["sha256"]:
                raise ValueError("保存済みサーバーjarが変更されている")
        os.chmod(target / "server.jar", 0o644)
        return metadata
    index = requests.get("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json", timeout=30)
    index.raise_for_status()
    source = next((v for v in index.json()["versions"] if v["id"] == version), None)
    if not source:
        raise ValueError("Minecraft公式の版が見つからない")
    response = requests.get(public_url(source["url"]), timeout=30)
    response.raise_for_status()
    if hashlib.sha1(response.content).hexdigest() != source["sha1"]:
        raise ValueError("公式metadataのSHA-1が一致しない")
    data = response.json()
    server = data["downloads"]["server"]
    digest = download(server["url"], target / "server.jar", sha1=server["sha1"])
    os.chmod(target / "server.jar", 0o644)
    metadata = {"version": version, "sha256": digest, "sha1": server["sha1"], "java": data.get("javaVersion", {}).get("majorVersion", 8)}
    metadata_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
