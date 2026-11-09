#!/usr/bin/env python3
"""カスタムマップの配布元取得、原本登録、検証記録を管理する。"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.custom_maps.artifacts import download, import_archive, ensure_vanilla, public_url  # noqa: E402
from utils.custom_maps.catalog import Catalog  # noqa: E402


def resolve_archive(item: dict) -> str:
    """配布ページの公開ダウンロード導線だけを解決する。"""
    url = item["source_url"]
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; OCI-mc map installer)"
    if item.get("download_page"):
        response = session.get(public_url(item["download_page"]), timeout=90)
    elif item["source"] == "colony":
        post_id = urlsplit(url).path.strip("/")
        page = session.get(public_url(url), timeout=30)
        page.raise_for_status()
        types = re.findall(r"download_bt_func\(\s*" + re.escape(post_id) + r"\s*,\s*(\d+)\s*\)", page.text)
        if not types:
            raise ValueError("配布ページのダウンロードボタンが見つからない")
        response = session.get(f"https://minecraft-mcworld.com/dl/?postid={post_id}&type={types[0]}", timeout=60, allow_redirects=False)
        if response.is_redirect:
            target = urljoin(response.url, response.headers["Location"])
            parsed = urlsplit(target)
            if parsed.hostname in ("www.dropbox.com", "dropbox.com"):
                target = urlunsplit(("https", "dl.dropboxusercontent.com", parsed.path, parsed.query, ""))
            response = session.get(public_url(target), timeout=90)
    else:
        page = session.get(public_url(url), timeout=30)
        page.raise_for_status()
        match = re.search(r'href=[\"\x27](https://[u]+x?\.getuploader\.com/[^\"\x27]+)', page.text)
        if not match:
            match = re.search(r'href=[\"\x27](https://[^\"\x27]*getuploader\.com/[^\"\x27]+)', page.text)
        if not match:
            raise ValueError("配布元へのダウンロードリンクが見つからない")
        response = session.get(html.unescape(match.group(1)), timeout=30)
    for _ in range(6):
        response.raise_for_status()
        if "zip" in response.headers.get("Content-Type", "") or response.content[:4] == b"PK\x03\x04":
            return response.url
        current = response.url
        if "mediafire.com" in current:
            direct = re.search(r'href=[\"\x27](https://download[^\"\x27]+)[\"\x27]', response.text)
            if direct:
                return html.unescape(direct.group(1))
        if "getuploader.com" in current:
            token = re.search(r'name=[\"\x27]token[\"\x27]\s+value=[\"\x27]([^\"\x27]+)', response.text)
            if token:
                response = session.post(current, data={"token": token.group(1)}, timeout=60)
                continue
            direct = re.search(r'(?:href|src)=[\"\x27](https://[^\"\x27]+(?:download\.php|dl[0-9]*\.getuploader)[^\"\x27]*)', response.text)
            if not direct:
                direct = re.search(r'https://[a-z0-9.-]*getuploader\.com/[^\s\"\x27<>]+\.zip[^\s\"\x27<>]*', response.text)
            if direct:
                next_url = html.unescape(direct.group(1) if direct.lastindex else direct.group(0))
                response = session.get(public_url(next_url), timeout=90)
                continue
        if "dropbox.com" in current:
            parsed = urlsplit(current)
            query = parse_qs(parsed.query)
            query["dl"] = ["1"]
            next_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))
            response = session.get(public_url(next_url), timeout=90)
            continue
        drive = re.search(r'(?:/file/d/|[?&]id=)([\w-]{15,})', current)
        if drive and "google" in current:
            response = session.get("https://drive.usercontent.google.com/download", params={"id": drive.group(1), "export": "download", "confirm": "t"}, timeout=90)
            continue
        match = re.search(r'(?:url=|href=[\"\x27])([^\"\x27<>]+\.zip[^\"\x27<>]*)', response.text, re.I)
        if match:
            response = session.get(public_url(urljoin(current, html.unescape(match.group(1)))), timeout=90)
            continue
        raise ValueError("取得先に手動確認が必要である。原本ZIPを管理者が登録できる")
    raise ValueError("配布先の解決が完了しなかった")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fetch", "import", "inventory", "verify"])
    parser.add_argument("--root", type=Path, default=Path("/opt/minecraft/custom-maps"))
    parser.add_argument("--catalog", type=Path, default=Path("custom-maps.json"))
    parser.add_argument("--map")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--world-path")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    catalog = Catalog(args.catalog)
    items = [catalog.get(args.map)] if args.map else list(catalog.items.values())
    for item in items:
        manifest_file = args.root / "templates" / item["id"] / item["revision"] / "manifest.json"
        try:
            if args.action == "inventory":
                manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
                print(json.dumps({"id": item["id"], "imported": bool(manifest), "smoke_verified": manifest.get("smoke_verified", False)}, ensure_ascii=False))
                continue
            if args.action == "verify":
                if not args.evidence:
                    raise ValueError("起動・保存復元の検証証跡JSONが必要である")
                evidence = json.loads(args.evidence.read_text())
                if evidence.get("map_id") != item["id"] or not all(evidence.get(key) for key in ("rcon_ready", "save_resume", "multiple_players")):
                    raise ValueError("検証証跡が不足している")
                resumed = evidence.get("resumed_content", {})
                if evidence.get("datapack_errors") != [] or not evidence.get("content_permissions_verified") or resumed.get("datapack_errors") != [] or not resumed.get("content_permissions_verified") or not all(evidence.get(key, {}).get("permissions_verified") for key in ("first_clients", "resumed_clients")):
                    raise ValueError("データパックとOP権限の検証証跡が不足している")
                manifest = json.loads(manifest_file.read_text())
                manifest.update(smoke_verified=True, verification=evidence)
                manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                print(item["id"] + ": verified")
                continue
            if manifest_file.exists():
                print(item["id"] + ": already imported")
                continue
            if args.action == "import":
                if not args.archive:
                    raise ValueError("--archiveが必要である")
                archive = args.archive
            else:
                archive = args.root / "downloads" / (item["id"] + ".zip")
                if not archive.exists():
                    url = resolve_archive(item)
                    download(url, archive)
            manifest = import_archive(args.root, item, archive, world_path=args.world_path)
            ensure_vanilla(args.root, item["mc_version"])
            print(json.dumps({"id": item["id"], "imported": True, "sha256": manifest["archive_sha256"]}, ensure_ascii=False))
        except Exception as error:
            print(json.dumps({"id": item["id"], "error": str(error)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
