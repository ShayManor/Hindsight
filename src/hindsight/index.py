from __future__ import annotations

import math
import re
import sqlite3

from pydantic import BaseModel

from hindsight.db import Database, serialize_vec
from hindsight.embedder import Embedder
from hindsight.schema import Env, Record

_TOKEN = re.compile(r"[A-Za-z0-9_@.]+")


class SearchResult(BaseModel):
    record: Record
    score: float
    why: dict


def _fts_query(*texts: str) -> str:
    seen: list[str] = []
    for text in texts:
        for tok in _TOKEN.findall(text):
            q = tok.replace('"', '""')
            seen.append(f'"{q}"')
    return " OR ".join(seen)


class LexicalIndex:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def search(self, frame_tok: str, message: str, k: int) -> list[tuple[str, float]]:
        query = _fts_query(frame_tok, message)
        if not query:
            return []
        rows = self.conn.execute(
            "SELECT id, bm25(fts_records) AS score FROM fts_records "
            "WHERE fts_records MATCH ? ORDER BY score, id LIMIT ?",
            (query, k),
        ).fetchall()
        return [(r["id"], -float(r["score"])) for r in rows]


class VectorIndex:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self.conn = conn
        self.embedder = embedder

    def search(self, query_text: str, k: int) -> list[tuple[str, float]]:
        vec = serialize_vec(self.embedder.embed(query_text))
        rows = self.conn.execute(
            "SELECT r.id AS id, v.distance AS distance FROM vec_records v "
            "JOIN records r ON r.rowid = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance, r.id",
            (vec, k),
        ).fetchall()
        return [(r["id"], -float(r["distance"])) for r in rows]


def rrf(rankings: list[list[str]], k_const: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k_const + rank + 1)
    return scores


def wilson_lower_bound(successes: int, attempts: int, z: float = 1.96) -> float:
    if attempts == 0:
        return 0.0
    phat = successes / attempts
    denom = 1 + z * z / attempts
    centre = phat + z * z / (2 * attempts)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * attempts)) / attempts)
    return (centre - margin) / denom


def env_match(query_env: Env | None, record: Record) -> float:
    if query_env is None:
        return 1.0
    checks: list[bool] = []
    for field in ("cuda", "os", "arch"):
        q = getattr(query_env, field)
        if q is not None:
            checks.append(getattr(record.env, field) == q)
    for name, ver in query_env.packages.items():
        checks.append(record.env.packages.get(name) == ver)
    if not checks:
        return 1.0
    return sum(checks) / len(checks)


def fuse(db: Database, frame_tok: str, message: str, query_text: str, env: Env | None,
         k: int, env_mode: str = "boost", success_weight: float = 0.5,
         env_weight: float = 0.3) -> list[SearchResult]:
    fanout = max(k * 5, 20)
    lex = LexicalIndex(db.conn).search(frame_tok, message, fanout)
    vec = VectorIndex(db.conn, db.embedder).search(query_text, fanout)
    lex_ids = [i for i, _ in lex]
    vec_ids = [i for i, _ in vec]
    fused = rrf([lex_ids, vec_ids])

    results: list[SearchResult] = []
    for doc_id, base in fused.items():
        record = db.get(doc_id)
        em = env_match(env, record)
        if env_mode == "filter" and em == 0.0:
            continue
        success = wilson_lower_bound(record.stats.successes, record.stats.attempts)
        boost = 1.0 + success_weight * success + env_weight * em
        score = base * boost
        why = {
            "frames": [f"{f.symbol}@{f.module}" for f in record.normalized_trace],
            "env_match": em,
            "attempts": record.stats.attempts,
            "successes": record.stats.successes,
            "success_rate": (record.stats.successes / record.stats.attempts)
            if record.stats.attempts else None,
            "indexes": [n for n, ids in (("lexical", lex_ids),
                                         ("semantic", vec_ids)) if doc_id in ids],
        }
        results.append(SearchResult(record=record, score=score, why=why))
    results.sort(key=lambda r: (-r.score, r.record.id))
    return results[:k]
