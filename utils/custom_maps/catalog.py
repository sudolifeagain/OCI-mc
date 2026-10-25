"""出典と不明値を保持したカスタムマップ検索。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("識別子の形式が不正である")
    return value


def requirements_hash(item: dict) -> str:
    fields = {key: item.get(key) for key in ("mc_version", "resource_pack", "client_settings")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Catalog:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.revision = str(data.get("revision", "1"))
        self.items: dict[str, dict] = {}
        for raw in data["maps"]:
            item = dict(raw)
            valid_id(item["id"])
            if item["id"] in self.items:
                raise ValueError("カタログIDが重複している")
            if not re.fullmatch(r"(?:1\.\d+(?:\.\d+)?|\d{2}\.\d+(?:\.\d+)?)", item["mc_version"]):
                raise ValueError("Minecraft版を正確に指定する必要がある")
            for key in ("source_url",):
                if not item[key].startswith("https://"):
                    raise ValueError("配布ページにはHTTPSが必要である")
            item.setdefault("difficulty", None)
            item.setdefault("duration_min", None)
            item.setdefault("duration_max", None)
            item.setdefault("min_players", 2)
            item.setdefault("max_players", 4)
            item.setdefault("revision", "1")
            item.setdefault("published", False)
            item.setdefault("resource_pack", None)
            item.setdefault("client_settings", [])
            item["requirements_hash"] = requirements_hash(item)
            self.items[item["id"]] = item

    def get(self, map_id: str) -> dict:
        try:
            return self.items[map_id]
        except KeyError:
            raise ValueError("カスタムマップが見つからない") from None

    def search(self, filters: dict, *, available: set[str] | None = None, played: dict | None = None) -> list[dict]:
        played = played or {}
        result = []
        for item in self.items.values():
            if not item["published"] or (available is not None and item["id"] not in available):
                continue
            keyword = normalized(str(filters.get("keyword", "")))
            if keyword and keyword not in normalized(item["title"] + " " + item.get("author", "") + " " + " ".join(item.get("aliases", []))):
                continue
            players = filters.get("players")
            if players is not None and not item["min_players"] <= int(players) <= item["max_players"]:
                continue
            if filters.get("source") and filters["source"] != item["source"]:
                continue
            if filters.get("version") and filters["version"] != item["mc_version"]:
                continue
            difficulty = filters.get("difficulty_max")
            if difficulty is not None:
                if item["difficulty"] is None and not filters.get("unknown_difficulty"):
                    continue
                if item["difficulty"] is not None and item["difficulty"] > int(difficulty):
                    continue
            limit = filters.get("max_minutes")
            if limit is not None:
                if item["duration_min"] is not None and item["duration_min"] > int(limit):
                    continue
                if item["duration_max"] is None and not filters.get("unknown_duration"):
                    continue
                if item["duration_max"] is not None and item["duration_max"] > int(limit):
                    continue
            if filters.get("pack") == "none" and item["resource_pack"]:
                continue
            if filters.get("pack") == "required" and not item["resource_pack"]:
                continue
            record = played.get(item["id"], {})
            history = filters.get("history")
            if history == "unplayed" and record:
                continue
            if history == "played" and not record:
                continue
            if history == "cleared" and not record.get("cleared"):
                continue
            if any(filters.get("no_" + tag) and item.get(tag) is not False for tag in ("horror", "parkour", "combat")):
                continue
            result.append(item)
        sort = filters.get("sort", "popular")
        field = {"short": "duration_max", "long": "duration_max", "easy": "difficulty", "hard": "difficulty", "many": "max_players", "few": "max_players"}.get(sort)
        reverse = sort in {"long", "hard", "many"}

        def key(item: dict) -> tuple:
            tie = (item["source"], item.get("rank", 9999), item["id"])
            if sort == "recent":
                value = played.get(item["id"], {}).get("last_played")
                return (value is None, -(value or 0), *tie)
            if field:
                value = item[field]
                return (value is None, -(value or 0) if reverse else (value or 0), *tie)
            return tie

        return sorted(result, key=key)


def summary(item: dict) -> str:
    difficulty = ["", "入門", "やさしい", "標準", "難しい", "とても難しい"]
    level = item.get("difficulty")
    diff = difficulty[level] if level else "難易度未記載"
    low, high = item.get("duration_min"), item.get("duration_max")
    duration = f"{low}～{high}分" if low and high and low != high else f"{high}分" if high else f"{low}分～" if low else item.get("duration_text", "時間未記載")
    pack = "指定パックあり" if item.get("resource_pack") else "パックなし"
    return f"{item['min_players']}～{item['max_players']}人 / {diff} / 目安{duration} / {pack}"
