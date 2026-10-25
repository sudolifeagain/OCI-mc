#!/usr/bin/env python3
"""本番のカスタムマップ領域と秘密情報を冪等に準備する。"""

import os
from pathlib import Path
import pwd
import secrets


def main() -> None:
    root = Path("/opt/minecraft/custom-maps")
    user = pwd.getpwnam("ubuntu")
    changed = False
    for name in ("", "artifacts", "downloads", "templates", "sessions", "backups", "trash", "slots"):
        path = root / name
        if not path.exists():
            path.mkdir(parents=True)
            changed = True
        os.chown(path, user.pw_uid, user.pw_gid)
        os.chmod(path, 0o755 if name in ("", "artifacts", "slots") else 0o700)
    env = Path("/opt/minecraft/bot/.env")
    text = env.read_text() if env.exists() else ""
    for n in (1, 2):
        key = f"CUSTOM_MAP_{n}_RCON_PASSWORD"
        if not any(line.startswith(key + "=") for line in text.splitlines()):
            text += f"\n{key}={secrets.token_urlsafe(32)}\n"
            changed = True
    if not env.exists() or env.read_text() != text:
        env.write_text(text)
    os.chown(env, user.pw_uid, user.pw_gid)
    os.chmod(env, 0o600)
    print("changed" if changed else "unchanged")


if __name__ == "__main__":
    main()
