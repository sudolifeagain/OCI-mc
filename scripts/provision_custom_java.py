#!/usr/bin/python3
"""旧配布マップ用のARM Java 8を公式配布物とSHA-256で配置する。"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def main() -> None:
    root = Path("/opt/minecraft/custom-maps")
    target = root / "java8"
    if (target / "bin/java").is_file():
        print("unchanged")
        return
    source = "https://corretto.aws/downloads/latest/amazon-corretto-8-aarch64-linux-jdk.tar.gz"
    checksum = "https://corretto.aws/downloads/latest_sha256/amazon-corretto-8-aarch64-linux-jdk.tar.gz"
    with tempfile.TemporaryDirectory(dir=root, prefix=".java8-") as name:
        stage = Path(name)
        archive = stage / "java.tar.gz"
        with urllib.request.urlopen(source, timeout=60) as response, archive.open("wb") as out:
            resolved = response.url
            shutil.copyfileobj(response, out)
        with urllib.request.urlopen(checksum, timeout=60) as response:
            expected = response.read().decode().split()[0].lower()
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected != actual:
            raise ValueError("Java 8のSHA-256が一致しない")
        unpacked = stage / "jdk"
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        candidates = [p for p in unpacked.iterdir() if (p / "bin/java").is_file()]
        if len(candidates) != 1:
            raise ValueError("Java 8の構成が不正である")
        (candidates[0] / "verified.json").write_text(json.dumps({"url": resolved, "sha256": actual}))
        os.replace(candidates[0], target)
    print("changed")


if __name__ == "__main__":
    main()
