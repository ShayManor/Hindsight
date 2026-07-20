from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import sqlite_vec

from hindsight.embedder import DeterministicEmbedder, Embedder, build_embed_input
from hindsight.normalize import frame_tokens
from hindsight.schema import Attempt, Env, Record


def serialize_vec(vec: list[float]) -> bytes:
    return sqlite_vec.serialize_float32(vec)


class Database:
    def __init__(self, path: str = ":memory:", embedder: Embedder | None = None) -> None:
        self.embedder = embedder or DeterministicEmbedder()
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS records (
                rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                language TEXT, framework TEXT, error_class TEXT,
                cuda TEXT, os TEXT, arch TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                successes INTEGER NOT NULL DEFAULT 0,
                doc TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL,
                worked INTEGER NOT NULL,
                notes TEXT, agent_id TEXT, env TEXT,
                created_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS fts_records
                USING fts5(id UNINDEXED, frame_tokens, message);
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_records
                USING vec0(embedding float[{self.embedder.dim}]);
            """
        )
        self.conn.commit()

    def _write(self, fn):
        cur = self.conn.cursor()
        try:
            result = fn(cur)
            self.conn.commit()
            return result
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def rowid_of(self, record_id: str) -> int | None:
        row = self.conn.execute("SELECT rowid FROM records WHERE id=?", (record_id,)).fetchone()
        return row["rowid"] if row else None

    def upsert(self, record: Record) -> str:
        embedding = self.embedder.embed(build_embed_input(record))
        message = f"{record.problem_title} {record.problem_body} {record.error_class}"
        tokens = frame_tokens(record.normalized_trace)

        def _op(cur: sqlite3.Cursor) -> str:
            existing = self.rowid_of(record.id)
            if existing is not None:
                cur_row = cur.execute(
                    "SELECT attempts, successes FROM records WHERE id=?", (record.id,)
                ).fetchone()
                attempts, successes = cur_row["attempts"], cur_row["successes"]
            else:
                attempts, successes = record.stats.attempts, record.stats.successes
            doc_final = record.model_copy(
                update={"stats": record.stats.model_copy(update={"attempts": attempts,
                                                                 "successes": successes})}
            ).model_dump_json()
            cur.execute(
                """INSERT INTO records
                   (id, language, framework, error_class, cuda, os, arch, attempts, successes, doc)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     language=excluded.language, framework=excluded.framework,
                     error_class=excluded.error_class, cuda=excluded.cuda,
                     os=excluded.os, arch=excluded.arch,
                     attempts=excluded.attempts, successes=excluded.successes,
                     doc=excluded.doc""",
                (record.id, record.language, record.framework, record.error_class,
                 record.env.cuda, record.env.os, record.env.arch,
                 attempts, successes, doc_final),
            )
            rid = existing if existing is not None else self.rowid_of(record.id)
            cur.execute("DELETE FROM fts_records WHERE id=?", (record.id,))
            cur.execute("INSERT INTO fts_records (id, frame_tokens, message) VALUES (?,?,?)",
                        (record.id, tokens, message))
            cur.execute("DELETE FROM vec_records WHERE rowid=?", (rid,))
            cur.execute("INSERT INTO vec_records (rowid, embedding) VALUES (?,?)",
                        (rid, serialize_vec(embedding)))
            return record.id

        return self._write(_op)

    def get(self, record_id: str) -> Record | None:
        row = self.conn.execute("SELECT doc FROM records WHERE id=?", (record_id,)).fetchone()
        return Record.model_validate_json(row["doc"]) if row else None

    def get_solution(self, record_id: str) -> Record | None:
        return self.get(record_id)

    def add_attempt(self, record_id: str, worked: bool, notes: str | None = None,
                    agent_id: str | None = None, env: Env | None = None,
                    created_at: datetime | None = None) -> Attempt:
        if self.rowid_of(record_id) is None:
            raise KeyError(record_id)
        stamp = created_at or datetime.now(timezone.utc)

        def _op(cur: sqlite3.Cursor) -> Attempt:
            cur.execute(
                """INSERT INTO attempts (record_id, worked, notes, agent_id, env, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (record_id, int(worked), notes, agent_id,
                 env.model_dump_json() if env else None, stamp.isoformat()),
            )
            attempt_id = cur.lastrowid
            cur.execute(
                "UPDATE records SET attempts=attempts+1, successes=successes+? WHERE id=?",
                (int(worked), record_id),
            )
            row = cur.execute("SELECT doc FROM records WHERE id=?", (record_id,)).fetchone()
            rec = Record.model_validate_json(row["doc"])
            rec.stats.attempts += 1
            rec.stats.successes += int(worked)
            cur.execute("UPDATE records SET doc=? WHERE id=?", (rec.model_dump_json(), record_id))
            return Attempt(attempt_id=attempt_id, record_id=record_id, worked=worked,
                           notes=notes, agent_id=agent_id, env=env, created_at=stamp)

        return self._write(_op)
