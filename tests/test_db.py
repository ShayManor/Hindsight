from datetime import datetime, timezone

import pytest

from hindsight.db import Database
from hindsight.schema import Env, Frame, Record, ReproScript, Source


def make_record(rid="r1", **over):
    base = dict(
        id=rid, problem_title="flash build fails", problem_body="tried building flash-attn",
        raw_trace="Traceback", normalized_trace=[Frame(symbol="compile_ext", module="build.py",
                                                        position=0, weight=1.0)],
        language="python", framework="flash-attn", error_class="ImportError",
        env=Env(packages={"torch": "2.1.0"}, cuda="12.1", os="Ubuntu 22.04", arch="x86_64"),
        repro_script=ReproScript(interpreter="bash", content="pip install flash-attn"),
        solution_body="pin torch to 2.1.0", solution_code=["pip install torch==2.1.0"],
        source=Source(platform="github", url=f"https://x/{rid}", author="dev", license="MIT"),
        posted=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ingested=datetime(2024, 1, 2, tzinfo=timezone.utc), index_version="v1",
    )
    base.update(over)
    return Record(**base)


@pytest.fixture()
def db():
    return Database()


def test_upsert_and_get_round_trip(db):
    rid = db.upsert(make_record())
    assert rid == "r1"
    got = db.get("r1")
    assert got.problem_title == "flash build fails"
    assert got.env.cuda == "12.1"
    assert got.repro_script.content == "pip install flash-attn"


def test_get_missing_returns_none(db):
    assert db.get("nope") is None
    assert db.get_solution("nope") is None


def test_upsert_is_idempotent(db):
    db.upsert(make_record())
    db.upsert(make_record(problem_title="updated title"))
    rows = db.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    assert rows == 1
    assert db.get("r1").problem_title == "updated title"


def test_rowid_stable_across_upsert(db):
    db.upsert(make_record())
    first = db.rowid_of("r1")
    db.upsert(make_record(problem_body="changed"))
    assert db.rowid_of("r1") == first


def test_add_attempt_updates_stats(db):
    db.upsert(make_record())
    a1 = db.add_attempt("r1", worked=True)
    assert a1.attempt_id is not None and a1.worked is True
    db.add_attempt("r1", worked=False, notes="still broke")
    rec = db.get("r1")
    assert rec.stats.attempts == 2
    assert rec.stats.successes == 1


def test_add_attempt_missing_record_raises(db):
    with pytest.raises(KeyError):
        db.add_attempt("ghost", worked=True)


def test_vec_and_fts_rows_written(db):
    db.upsert(make_record())
    vec_count = db.conn.execute("SELECT COUNT(*) FROM vec_records").fetchone()[0]
    fts_count = db.conn.execute("SELECT COUNT(*) FROM fts_records").fetchone()[0]
    assert vec_count == 1 and fts_count == 1


def test_write_rolls_back_on_error(db):
    def boom(cur):
        raise ValueError("boom")

    with pytest.raises(ValueError):
        db._write(boom)


def test_upsert_preserves_attempt_stats(db):
    db.upsert(make_record())
    db.add_attempt("r1", worked=True)
    db.add_attempt("r1", worked=True)
    db.upsert(make_record(problem_body="edited body"))
    rec = db.get("r1")
    assert rec.stats.attempts == 2 and rec.stats.successes == 2
    cols = db.conn.execute(
        "SELECT attempts, successes FROM records WHERE id='r1'").fetchone()
    assert cols["attempts"] == 2 and cols["successes"] == 2
    db.add_attempt("r1", worked=False)
    rec2 = db.get("r1")
    assert rec2.stats.attempts == 3 and rec2.stats.successes == 2


def test_close(db):
    db.close()
