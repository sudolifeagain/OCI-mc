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

    def identity(self, guild: int | str, user: int | str) -> dict | None:
        return self.get("identity", f"{guild}-{user}")

    def approve_identity(self, guild: int | str, user: int | str, name: str, player_uuid: str, actor: int | str) -> dict:
        result = {"id": f"{guild}-{user}", "guild": str(guild), "owner": str(user), "name": name, "uuid": player_uuid, "approved": True, "approved_by": str(actor), "updated": time.time()}
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                rows = db.execute("SELECT data FROM records WHERE kind='identity' AND guild=?", (str(guild),)).fetchall()
                for row in rows:
                    item = json.loads(row[0])
                    if item.get("uuid") == player_uuid and item["owner"] != str(user) and item.get("approved"):
                        raise ValueError("このMinecraft名は別のDiscordアカウントに登録済みです。")
                db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)", ("identity", result["id"], str(guild), str(user), "", result["updated"], json.dumps(result)))
        except sqlite3.IntegrityError:
            raise ValueError("このMinecraft名は別のDiscordアカウントに登録済みです。") from None
        self.event("identity_approved", result["id"], actor)
        return result

    def played(self, guild: int | str, user: int | str) -> dict:
        result = {}
        for session in self.list("session", guild):
            if str(user) not in session.get("actual_players", {}):
                continue
            ident = session["map_id"]
            old = result.setdefault(ident, {"last_played": 0, "cleared": False})
            old["last_played"] = max(old["last_played"], session["actual_players"][str(user)])
            old["cleared"] |= session.get("result") == "clear_reported"
        return result

    def snapshot(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source, contextlib.closing(sqlite3.connect(destination)) as target:
            with target:
                source.backup(target)
