from __future__ import annotations

from datetime import datetime, timezone

from hindsight.db import Database
from hindsight.normalize import frame_tokens, normalize_trace
from hindsight.schema import Env, Record, ReproScript, Source, Stats, content_checksum
from hindsight.search import index_version


class SubmissionError(ValueError):
    pass


def _as_env(env) -> Env | None:
    if env is None:
        return None
    return env if isinstance(env, Env) else Env.model_validate(env)


def _as_repro(repro) -> ReproScript | None:
    if repro is None:
        return None
    return repro if isinstance(repro, ReproScript) else ReproScript.model_validate(repro)


def submit_record(db: Database, *, problem_body: str, raw_trace: str, env,
                  solution_body: str, repro_script, problem_title: str | None = None,
                  solution_code: list[str] | None = None, language: str = "unknown",
                  framework: str | None = None, error_class: str = "unknown",
                  author: str | None = None, url: str | None = None,
                  posted: datetime | None = None, ingested: datetime | None = None) -> str:
    env_obj = _as_env(env)
    repro_obj = _as_repro(repro_script)
    if not problem_body.strip():
        raise SubmissionError("problem_body is required")
    if not raw_trace.strip():
        raise SubmissionError("raw_trace is required")
    if not solution_body.strip():
        raise SubmissionError("solution_body is required")
    if env_obj is None or not (
        env_obj.packages or env_obj.cuda or env_obj.os or env_obj.arch or env_obj.hardware
    ):
        raise SubmissionError("env with at least one field is required")
    if repro_obj is None or not repro_obj.content.strip():
        raise SubmissionError("repro_script with content is required")

    frames = normalize_trace(raw_trace)
    dedup_key = "|".join([frame_tokens(frames), solution_body.strip(), repro_obj.checksum])
    record_id = content_checksum(dedup_key)

    existing = db.get(record_id)
    if existing is not None:
        return existing.id

    now = datetime.now(timezone.utc)
    record = Record(
        id=record_id,
        problem_title=problem_title or problem_body.strip()[:80],
        problem_body=problem_body,
        raw_trace=raw_trace,
        normalized_trace=frames,
        language=language,
        framework=framework,
        error_class=error_class,
        env=env_obj,
        repro_script=repro_obj,
        solution_body=solution_body,
        solution_code=solution_code or [],
        source=Source(platform="user_submitted", url=url or f"hindsight://{record_id}",
                      author=author, license="MIT"),
        posted=posted or now,
        ingested=ingested or now,
        stats=Stats(),
        embedding_ref=None,
        index_version=index_version(db.embedder),
    )
    return db.upsert(record)
