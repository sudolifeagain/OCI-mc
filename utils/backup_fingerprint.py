"""バックアップ済みワールドのフィンガープリントを管理する。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def load_fingerprints(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_fingerprints(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp_path.chmod(0o600)
    temp_path.replace(path)


def compute_fingerprint(base_dir: str, target_dirs: list[str]) -> str:
    entries: list[str] = []
    for target in sorted(target_dirs):
        full_path = os.path.join(base_dir, target)
        if not os.path.exists(full_path):
            continue
        if os.path.isdir(full_path):
            for root, dirs, files in os.walk(full_path):
                dirs.sort()
                for filename in sorted(files):
                    file_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(file_path, base_dir)
                    stat = os.stat(file_path)
                    entries.append(f"{relative_path}|{stat.st_size}|{stat.st_mtime_ns}")
        else:
            stat = os.stat(full_path)
            entries.append(f"{target}|{stat.st_size}|{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def fingerprint_value(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        value = entry.get("fingerprint")
        return value if isinstance(value, str) else None
    return None


def fingerprint_updated_at(entry: object) -> int | None:
    if not isinstance(entry, dict):
        return None
    value = entry.get("updated_at")
    return value if isinstance(value, int) else None


def update_fingerprint(
    path: Path,
    server_id: str,
    base_dir: str,
    target_dirs: list[str],
    *,
    now: int | None = None,
) -> None:
    saved = load_fingerprints(path)
    saved[server_id] = {
        "fingerprint": compute_fingerprint(base_dir, target_dirs),
        "updated_at": int(time.time()) if now is None else now,
    }
    save_fingerprints(path, saved)


def backup_is_acceptable(
    path: Path,
    server_id: str,
    base_dir: str,
    target_dirs: list[str],
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> bool:
    """直近のバックアップ、または現在と完全一致するバックアップのみ許可する。"""

    entry = load_fingerprints(path).get(server_id)
    saved_fingerprint = fingerprint_value(entry)
    if not saved_fingerprint:
        return False

    current_time = int(time.time()) if now is None else now
    updated_at = fingerprint_updated_at(entry)
    if updated_at is not None and 0 <= current_time - updated_at <= max_age_seconds:
        return True

    return compute_fingerprint(base_dir, target_dirs) == saved_fingerprint
