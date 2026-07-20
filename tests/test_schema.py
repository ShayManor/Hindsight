from datetime import datetime, timezone

from hindsight.schema import (
    Attempt, Env, Frame, Quality, Record, ReproScript, Source, Stats, content_checksum,
)


def _record(**over):
    base = dict(
        id="abc", problem_title="t", problem_body="tried x", raw_trace="Traceback...",
        normalized_trace=[Frame(symbol="f", module="m", position=0, weight=1.0)],
        language="python", framework="flash-attn", error_class="ImportError",
        env=Env(packages={"torch": "2.1.0"}, cuda="12.1"),
        repro_script=ReproScript(interpreter="bash", content="pip install flash-attn"),
        solution_body="pin torch", solution_code=["pip install torch==2.1.0"],
        source=Source(platform="github", url="https://x/1", author="a", license="MIT"),
        posted=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ingested=datetime(2024, 1, 2, tzinfo=timezone.utc),
        quality=Quality(votes=3), stats=Stats(attempts=2, successes=1),
        embedding_ref="1", index_version="v1",
    )
    base.update(over)
    return Record(**base)


def test_checksum_is_deterministic_and_stable():
    assert content_checksum("hello") == content_checksum("hello")
    assert content_checksum("hello") != content_checksum("world")


def test_repro_script_autofills_checksum():
    rs = ReproScript(interpreter="bash", content="echo hi")
    assert rs.checksum == content_checksum("echo hi")


def test_repro_script_keeps_explicit_checksum():
    rs = ReproScript(interpreter="bash", content="echo hi", checksum="pinned")
    assert rs.checksum == "pinned"


def test_record_round_trips_through_json():
    rec = _record()
    dumped = rec.model_dump_json()
    loaded = Record.model_validate_json(dumped)
    assert loaded == rec


def test_defaults():
    assert Stats().attempts == 0 and Stats().successes == 0
    assert Quality().votes == 0
    assert Env().packages == {}


def test_attempt_optional_fields():
    a = Attempt(record_id="abc", worked=True, created_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert a.attempt_id is None and a.notes is None and a.agent_id is None and a.env is None
