from __future__ import annotations

from hindsight.db import Database
from hindsight.embedder import Embedder, build_query_input
from hindsight.index import SearchResult, fuse
from hindsight.normalize import frame_tokens, normalize_trace
from hindsight.schema import Env


def index_version(embedder: Embedder) -> str:
    return f"idx-{embedder.id}"


def search_error(db: Database, trace: str, message: str = "", attempt_summary: str = "",
                 env: Env | None = None, k: int = 10,
                 env_mode: str = "boost") -> list[SearchResult]:
    frames = normalize_trace(trace)
    tok = frame_tokens(frames)
    query_text = build_query_input(message, attempt_summary, tok, env)
    lexical_message = f"{message} {attempt_summary}".strip()
    return fuse(db, tok, lexical_message, query_text, env, k, env_mode=env_mode)
