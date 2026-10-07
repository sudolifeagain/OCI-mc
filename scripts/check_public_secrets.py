#!/usr/bin/env python3
"""公開対象の秘密情報・運用設定を検査し、値を出力せずに失敗させる。"""

import json
import re
import subprocess
from pathlib import Path


PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED)? ?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "notion-token": re.compile(r"\b(?:secret_[A-Za-z0-9]{30,}|ntn_[A-Za-z0-9]{30,})\b"),
    "discord-token": re.compile(r"\b(?:[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}|mfa\.[A-Za-z0-9_-]{80,})\b"),
    "discord-webhook": re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    "literal-credential": re.compile(
        r'''(?im)["']?(?:[A-Z_]*(?:TOKEN|PASSWORD|PRIVATE_KEY|WEBHOOK_URL)|rcon\.password)["']?\s*[:=]\s*["']([^\r\n"']+)["']'''
    ),
}
PLACEHOLDER = re.compile(
    r"(?i)^(?:your[_ -]|example|dummy|test|password$|token$|secret$|fake|\$|<|\{|os\.|[A-Z_]+_PASSWORD$|\s*$)"
)
PRIVATE_FILES = {
    "id_rsa", "id_ed25519", "user_permissions.json", "reaction_role_state.json",
    "reaction_roles.local.json", ".backup_fingerprints.json",
}


def findings(path: str, content: bytes) -> list[tuple[str, int]]:
    name = Path(path).name
    issues = []
    if (name in PRIVATE_FILES or (name.startswith(".env") and not name.endswith(".example"))
            or Path(name).suffix in {".pem", ".key", ".p12", ".pfx", ".tfstate", ".tfvars"}):
        issues.append(("private-file", 1))
    text = content.decode("utf-8", errors="replace")
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            if kind == "literal-credential" and PLACEHOLDER.search(match[1]):
                continue
            issues.append((kind, text[:match.start()].count("\n") + 1))
    if path in {"config.json", "server-artifacts.json"}:
        def check(data: object) -> None:
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in {"guild_id", "channel_id", "user_id", "guild_ids", "channel_ids", "user_ids"} and value:
                        issues.append(("private-operational-metadata", 1))
                    if key.lower() in {"password", "rcon_password", "token", "discord_token", "notion_token", "private_key"} and value:
                        issues.append(("credential-in-public-config", 1))
                    check(value)
            elif isinstance(data, list):
                for value in data:
                    check(value)
        check(json.loads(text))
    return issues


def main() -> None:
    paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    failures = 0
    for path in paths:
        if not path or not Path(path).is_file():
            continue
        for kind, line in findings(path, Path(path).read_bytes()):
            print(f"{path}:{line}: {kind}")
            failures += 1
    if failures:
        raise SystemExit("Public repository policy check failed; values are omitted")
    print("Public repository policy check passed")


if __name__ == "__main__":
    main()
