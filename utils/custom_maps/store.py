"""SQLiteによるセッション・参加者・ジョブの永続化。"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Iterator


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, guild TEXT NOT NULL,
                owner TEXT NOT NULL, channel TEXT NOT NULL, updated REAL NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(kind,id)
            );
            CREATE INDEX IF NOT EXISTS records_owner ON records(kind,guild,owner,updated);
            CREATE UNIQUE INDEX IF NOT EXISTS approved_uuid ON records(guild,json_extract(data,'$.uuid'))
                WHERE kind='identity' AND json_extract(data,'$.approved')=1;
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL,
                actor TEXT NOT NULL, at REAL NOT NULL, data TEXT NOT NULL
            );
            """)

    @contextlib.contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, kind: str, ident: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, ident)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind: str, guild: int | str | None = None) -> list[dict]:
        with self.connect() as db:
            if guild is None:
                rows = db.execute("SELECT data FROM records WHERE kind=? ORDER BY updated DESC", (kind,)).fetchall()
            else:
                rows = db.execute("SELECT data FROM records WHERE kind=? AND guild=? ORDER BY updated DESC", (kind, str(guild))).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind: str, record: dict, *, expected: int | None = None) -> dict:
        record = dict(record)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT data FROM records WHERE kind=? AND id=?", (kind, record["id"])).fetchone()
            old = json.loads(row[0]) if row else None
            if expected is not None and (old or {}).get("generation", 0) != expected:
                raise ValueError("内容が更新されました。画面を開き直して操作してください。")
            record["generation"] = (old or {}).get("generation", 0) + 1
            record["updated"] = time.time()
            db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)", (kind, record["id"], str(record.get("guild", "")), str(record.get("owner", "")), str(record.get("channel", "")), record["updated"], json.dumps(record, ensure_ascii=False)))
        return record

    def create(self, kind: str, **fields) -> dict:
        return self.put(kind, {"id": uuid.uuid4().hex[:16], "created": time.time(), **fields})

    def event(self, kind: str, target: str, actor: int | str, **data) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO events(kind,target,actor,at,data) VALUES(?,?,?,?,?)", (kind, target, str(actor), time.time(), json.dumps(data, ensure_ascii=False)))

    def played(self, guild: int | str, channel: int | str) -> dict:
        result = {}
        for session in self.list("session", guild):
            if session.get("channel") != str(channel) or not session.get("actual_players"):
                continue
            ident = session["map_id"]
            old = result.setdefault(ident, {"last_played": 0, "cleared": False})
            old["last_played"] = max(old["last_played"], max(session["actual_players"].values()))
            old["cleared"] |= session.get("result") == "clear_reported"
        return result

    def snapshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, contextlib.closing(sqlite3.connect(destination)) as target:
            with target:
                source.backup(target)

    def relocate_channel(self, guild: int | str, source: int | str, destination: int | str, messages: dict[str, str]) -> dict[str, str]:
        """投稿済みカードの対応表と全関連レコードを一度に移す。"""
        if str(source) == str(destination):
            raise ValueError("移動元と移動先のチャンネルが同じである")
        moved = {}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT kind,id,data FROM records WHERE guild=? AND channel=?", (str(guild), str(source))).fetchall()
            bindings = {row["id"] for row in rows if row["kind"] == "message"}
            if bindings != set(messages):
                raise ValueError("移動対象の共有カードと投稿結果が一致しない")
            for row in rows:
                record = json.loads(row["data"])
                record.update(channel=str(destination), generation=record.get("generation", 0) + 1, updated=time.time())
                if row["kind"] == "message":
                    record["message_id"] = str(messages[row["id"]])
                    if record["type"] == "panel":
                        record.update(id=f"panel-{destination}", target=str(destination))
                        if db.execute("SELECT 1 FROM records WHERE kind='message' AND id=?", (record["id"],)).fetchone():
                            raise ValueError("移動先の常設パネルがすでに存在する")
                    moved[row["id"]] = record["id"]
                db.execute("DELETE FROM records WHERE kind=? AND id=?", (row["kind"], row["id"]))
                db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?)", (row["kind"], record["id"], str(guild), str(record.get("owner", "")), str(destination), record["updated"], json.dumps(record, ensure_ascii=False)))
        return moved
