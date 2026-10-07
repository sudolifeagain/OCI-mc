#!/usr/bin/env python3
"""デプロイ前に旧公開設定の運用情報をホスト内の非公開設定へ退避する。"""

import argparse
import json
import os
import tempfile
from pathlib import Path


def migrate(config_path: Path) -> bool:
    private_path = config_path.parent / "reaction_roles.local.json"
    if private_path.exists():
        return False
    config = json.loads(config_path.read_text(encoding="utf-8"))
    data = config.get("reaction_roles", {})
    if not data.get("channel_id"):
        return False
    with tempfile.NamedTemporaryFile(dir=config_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(0o600)
        os.replace(temporary, private_path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print("Private runtime configuration migrated" if migrate(args.config) else "No migration needed")
