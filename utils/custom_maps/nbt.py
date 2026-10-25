"""level.datの版・難易度を標準ライブラリだけで読む。"""

import gzip
import io
from pathlib import Path
import struct


def world_metadata(path: Path) -> dict:
    with gzip.open(path, "rb") as source:
        raw = source.read(32 * 1024 ** 2 + 1)
    if len(raw) > 32 * 1024 ** 2:
        raise ValueError("level.datが読み取り上限を超える")
    stream = io.BytesIO(raw)

    def number(fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, stream.read(size))[0]

    def string() -> str:
        return stream.read(number(">H")).decode("utf-8", errors="replace")

    def value(kind: int, depth: int = 0):
        if depth > 64:
            raise ValueError("NBTの階層が深すぎる")
        formats = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}
        if kind in formats:
            return number(formats[kind])
        if kind == 8:
            return string()
        if kind == 10:
            result = {}
            while True:
                child = number(">B")
                if not child:
                    return result
                name = string()
                result[name] = value(child, depth + 1)
        if kind in (7, 11, 12):
            length = number(">i")
            size = {7: 1, 11: 4, 12: 8}[kind]
            if length < 0 or length * size > len(raw) - stream.tell():
                raise ValueError("NBTの配列長が不正である")
            stream.seek(length * size, 1)
            return None
        if kind == 9:
            subtype, length = number(">B"), number(">i")
            if length < 0 or length > 1000000:
                raise ValueError("NBTのリスト長が不正である")
            return [value(subtype, depth + 1) for _ in range(length)]
        raise ValueError("未対応のNBTタグである")

    if number(">B") != 10:
        raise ValueError("level.datのルートが不正である")
    string()
    data = value(10).get("Data", {})
    return {"version": data.get("Version", {}).get("Name"), "difficulty": data.get("Difficulty"), "gametype": data.get("GameType")}
