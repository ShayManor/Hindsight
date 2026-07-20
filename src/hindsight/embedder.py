from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from hindsight.normalize import frame_tokens
from hindsight.schema import Env, Record


@runtime_checkable
class Embedder(Protocol):
    dim: int
    id: str

    def embed(self, text: str) -> list[float]: ...


class DeterministicEmbedder:
    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.id = f"det-hash-{dim}"

    def _grams(self, text: str) -> list[str]:
        tokens = text.lower().split()
        char_grams = [text[i : i + 3] for i in range(len(text) - 2)]
        return tokens + char_grams

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for gram in self._grams(text):
            h = int(hashlib.blake2b(gram.encode("utf-8"), digest_size=8).hexdigest(), 16)
            idx = h % self.dim
            sign = 1.0 if (h >> 8) & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def env_text(env: Env | None) -> str:
    if env is None:
        return ""
    parts: list[str] = []
    for name, ver in sorted(env.packages.items()):
        parts.append(f"{name}:{ver}")
    for label, value in (("cuda", env.cuda), ("os", env.os), ("arch", env.arch),
                         ("hw", env.hardware)):
        if value:
            parts.append(f"{label}:{value}")
    return " ".join(parts)


def build_embed_input(record: Record) -> str:
    return " ".join([
        record.problem_title,
        record.problem_body,
        frame_tokens(record.normalized_trace),
        env_text(record.env),
    ]).strip()


def build_query_input(message: str, attempt_summary: str, frame_tok: str,
                      env: Env | None) -> str:
    return " ".join([message, attempt_summary, frame_tok, env_text(env)]).strip()
