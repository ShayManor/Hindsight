from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, Field, model_validator


def content_checksum(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


class Frame(BaseModel):
    symbol: str
    module: str
    position: int
    weight: float


class Env(BaseModel):
    packages: dict[str, str] = Field(default_factory=dict)
    cuda: str | None = None
    os: str | None = None
    arch: str | None = None
    hardware: str | None = None


class ReproScript(BaseModel):
    interpreter: str
    content: str
    expected_error_signature: str | None = None
    checksum: str = ""

    @model_validator(mode="after")
    def _fill_checksum(self) -> "ReproScript":
        if not self.checksum:
            object.__setattr__(self, "checksum", content_checksum(self.content))
        return self


class Source(BaseModel):
    platform: str
    url: str
    author: str | None = None
    license: str


class Quality(BaseModel):
    votes: int = 0


class Stats(BaseModel):
    attempts: int = 0
    successes: int = 0


class Record(BaseModel):
    id: str
    problem_title: str
    problem_body: str
    raw_trace: str
    normalized_trace: list[Frame]
    language: str
    framework: str | None = None
    error_class: str
    env: Env
    repro_script: ReproScript | None = None
    solution_body: str
    solution_code: list[str] = Field(default_factory=list)
    source: Source
    posted: datetime
    ingested: datetime
    quality: Quality = Field(default_factory=Quality)
    stats: Stats = Field(default_factory=Stats)
    embedding_ref: str | None = None
    index_version: str


class Attempt(BaseModel):
    attempt_id: int | None = None
    record_id: str
    worked: bool
    notes: str | None = None
    agent_id: str | None = None
    env: Env | None = None
    created_at: datetime
