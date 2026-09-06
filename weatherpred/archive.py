"""Append-only, hash-chained local archive. No external database connections.

The hash chain detects accidental modification; it is not an externally notarized
timestamp. Backups/external anchoring are required for stronger tamper evidence.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

from weatherpred.timeutil import iso


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Archive:
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "archive.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                key TEXT NOT NULL,
                available_at TEXT NOT NULL,
                metadata TEXT NOT NULL,
                body_sha256 TEXT NOT NULL,
                previous_sha256 TEXT NOT NULL,
                record_sha256 TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS records_lookup ON records(kind, key, available_at);
            CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
                BEGIN SELECT RAISE(ABORT, 'archive records are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
                BEGIN SELECT RAISE(ABORT, 'archive records are append-only'); END;
        """)

    def close(self):
        self.db.close()

    def append(self, kind, key, available_at, metadata, body: bytes) -> int:
        timestamp = iso(available_at)
        digest = hashlib.sha256(body).hexdigest()
        path = self.root / "blobs" / digest
        try:
            with path.open("xb") as f:
                f.write(body)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Existing archive blob has been altered")
        payload = canonical(metadata)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            previous = self.db.execute(
                "SELECT record_sha256 FROM records ORDER BY id DESC LIMIT 1"
            ).fetchone()
            previous = previous[0] if previous else "0" * 64
            fields = [kind, key, timestamp, payload, digest, previous]
            record_hash = hashlib.sha256(canonical(fields).encode()).hexdigest()
            cursor = self.db.execute(
                "INSERT INTO records(kind,key,available_at,metadata,body_sha256,previous_sha256,record_sha256) "
                "VALUES(?,?,?,?,?,?,?)",
                (*fields, record_hash),
            )
            self.db.commit()
            return cursor.lastrowid
        except Exception:
            self.db.rollback()
            raise

    def body(self, record) -> bytes:
        return (self.root / "blobs" / record["body_sha256"]).read_bytes()

    def json(self, record):
        return json.loads(self.body(record))

    def latest(self, kind, key, as_of=None):
        query = "SELECT * FROM records WHERE kind=? AND key=?"
        args = [kind, key]
        if as_of is not None:
            query += " AND available_at<=?"
            args.append(iso(as_of))
        return self.db.execute(query + " ORDER BY available_at DESC,id DESC LIMIT 1", args).fetchone()

    def verify(self):
        previous = "0" * 64
        count = 0
        for row in self.db.execute("SELECT * FROM records ORDER BY id"):
            digest = hashlib.sha256(self.body(row)).hexdigest()
            fields = [
                row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
            ]
            if digest != row["body_sha256"] or row["previous_sha256"] != previous:
                raise ValueError(f"Archive integrity failure at record {row['id']}")
            if hashlib.sha256(canonical(fields).encode()).hexdigest() != row["record_sha256"]:
                raise ValueError(f"Record integrity failure at {row['id']}")
            previous = row["record_sha256"]
            count += 1
        return {"records_verified": count, "chain_head": previous}
