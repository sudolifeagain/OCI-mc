"""公開設定から運用固有のリアクション設定を分離する。"""

import json
from pathlib import Path


def load_reaction_roles(config: dict, path: Path) -> dict:
    if not path.exists():
        return config.get("reaction_roles", {})
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("mappings", {}), dict):
            raise ValueError
        if type(data.get("channel_id")) is not int or data["channel_id"] <= 0:
            raise ValueError
        for emoji, mapping in data.get("mappings", {}).items():
            if (not isinstance(emoji, str) or not isinstance(mapping, dict)
                    or type(mapping.get("channel_id")) is not int or mapping["channel_id"] <= 0
                    or not isinstance(mapping.get("label"), str)):
                raise ValueError
    except (OSError, ValueError, TypeError):
        raise ValueError("reaction_roles.local.json の形式が不正である") from None
    return data
