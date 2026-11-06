"""公式 Java プロフィールの照会とホワイトリストへの安全な追加。"""
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
from urllib.parse import quote

import requests
import yaml


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


def resolve_bedrock_profile(name: str, server_dir: str) -> dict[str, str]:
    name = name.strip()
    if not 1 <= len(name) <= 16 or not name.isprintable() or any(c in name for c in '\r\n'):
        raise ValueError('統合版のXboxゲーマータグを確認してください。')
    config_path = Path(server_dir) / 'plugins' / 'floodgate' / 'config.yml'
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    prefix = config.get('username-prefix', '.')
    replace_spaces = config.get('replace-spaces', True)
    if not isinstance(prefix, str) or not prefix or len(prefix) > 4 or not isinstance(replace_spaces, bool):
        raise ValueError('Floodgateの名前設定を確認してください。')
    requested = prefix + name
    url = 'https://api.geysermc.org/v2/utils/uuid/bedrock_or_java/' + quote(requested, safe='')
    response = requests.get(url, params={'prefix': prefix}, timeout=15, allow_redirects=False)
    if response.status_code == 503:
        raise ValueError('Geyserの公式APIでゲーマータグを照合できません。別のGeyser対応サーバーへの参加履歴が必要な場合があります。')
    if response.status_code in (204, 400, 404):
        raise ValueError('統合版のゲーマータグが見つかりません。')
    if response.status_code != 200:
        raise ValueError('Geyserの公式APIで統合版プロフィールを確認できません。')
    data = response.json()
    canonical = data['name']
    expected = (requested.replace(' ', '_') if replace_spaces else requested).lower()
    if not isinstance(canonical, str) or (canonical.replace(' ', '_') if replace_spaces else canonical).lower() != expected:
        raise ValueError('Geyserから返された統合版プロフィール名が一致しません。')
    player_uuid = uuid.UUID(data['id'])
    if not 0 < player_uuid.int < 2**64:
        raise ValueError('統合版プロフィールのUUIDを確認できません。')
    return {'name': canonical.replace(' ', '_') if replace_spaces else canonical, 'uuid': str(player_uuid)}


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
