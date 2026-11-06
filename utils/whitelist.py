"""公式 Java プロフィールの照会とホワイトリストへの安全な追加。"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

import requests


JAVA_NAME = re.compile(r'^[A-Za-z0-9_]{3,16}$')


def resolve_java_profile(name: str) -> dict[str, str]:
    if not JAVA_NAME.fullmatch(name):
        raise ValueError('Java版のプレイヤー名ではありません。')
    response = requests.get(f'https://api.mojang.com/users/profiles/minecraft/{name}', timeout=15)
    if response.status_code in (204, 404):
        raise ValueError('そのJava版プロフィールは見つかりません。名前を確認してください。')
    response.raise_for_status()
    profile = response.json()
    canonical = profile['name']
    if not isinstance(canonical, str) or not JAVA_NAME.fullmatch(canonical) or canonical.lower() != name.lower():
        raise ValueError('公式プロフィールの名前を確認できません。')
    return {'name': canonical, 'uuid': str(uuid.UUID(profile['id']))}


def add_verified_profile(server_dir: str, profile: dict[str, str]) -> None:
    path = Path(server_dir) / 'whitelist.json'
    # 書き込み中の変更を検出し、既存リストを上書きしない。
    original = path.read_bytes()
    info = path.stat()
    entries = json.loads(original)
    if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
        raise ValueError('既存のホワイトリストを読み込めません。')
    name, player_uuid = profile['name'], profile['uuid']
    for entry in entries:
        if str(entry.get('name', '')).lower() == name.lower() and str(entry.get('uuid', '')).lower() != player_uuid:
            raise ValueError('同じ名前に別のUUIDが登録されています。')
    if any(str(e.get('uuid', '')).lower() == player_uuid for e in entries):
        return
    entries.append({'uuid': player_uuid, 'name': name})
    fd, temporary = tempfile.mkstemp(prefix='.whitelist-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(entries, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, stat.S_IMODE(info.st_mode))
        if path.read_bytes() != original:
            raise ValueError('ホワイトリストが同時に変更されたため、再実行してください。')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
