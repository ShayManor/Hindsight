import math

import pytest

from hindsight.db import Database
from hindsight.index import (
    LexicalIndex, VectorIndex, env_match, fuse, rrf, wilson_lower_bound,
)
from hindsight.schema import Frame
from tests.test_db import make_record


@pytest.fixture()
def db():
    d = Database()
    d.upsert(make_record("r1", problem_title="flash attention build fails",
                          problem_body="undefined symbol cuda",
                          normalized_trace=make_record().normalized_trace))
    d.upsert(make_record("r2", problem_title="pandas merge memory error",
                          problem_body="dataframe join OOM", error_class="MemoryError",
                          normalized_trace=[Frame(symbol="merge", module="pandas/core/reshape/merge.py",
                                                  position=0, weight=1.0)]))
    return d


def test_wilson_zero_attempts_is_zero():
    assert wilson_lower_bound(0, 0) == 0.0


def test_wilson_prefers_more_evidence():
    assert wilson_lower_bound(40, 50) > wilson_lower_bound(1, 1)


def test_rrf_combines_rankings():
    scores = rrf([["a", "b"], ["b", "a"]])
    assert set(scores) == {"a", "b"}
    assert math.isclose(scores["a"], scores["b"])


def test_lexical_finds_by_frame_tokens(db):
    hits = LexicalIndex(db.conn).search("compile_ext@build.py", "flash build", k=5)
    assert hits and hits[0][0] == "r1"


def test_vector_finds_semantic_match(db):
    hits = VectorIndex(db.conn, db.embedder).search("flash attention undefined symbol", k=5)
    assert any(h[0] == "r1" for h in hits)


def test_env_match_full_when_no_query_env(db):
    assert env_match(None, db.get("r1")) == 1.0


def test_env_match_partial(db):
    from hindsight.schema import Env
    score = env_match(Env(cuda="12.1", os="Ubuntu 22.04", arch="x86_64"), db.get("r1"))
    assert score == 1.0
    score2 = env_match(Env(cuda="11.8"), db.get("r1"))
    assert score2 == 0.0


def test_env_match_no_checks_is_full(db):
    from hindsight.schema import Env
    assert env_match(Env(), db.get("r1")) == 1.0


def test_env_match_package_field(db):
    from hindsight.schema import Env
    assert env_match(Env(packages={"torch": "2.1.0"}), db.get("r1")) == 1.0


def test_lexical_empty_query_returns_empty(db):
    assert LexicalIndex(db.conn).search("", "", k=5) == []


def test_fuse_ranks_relevant_first(db):
    results = fuse(db, "compile_ext@build.py", "flash attention build undefined symbol",
                   "flash attention build undefined symbol", env=None, k=5)
    assert results[0].record.id == "r1"
    assert "attempts" in results[0].why


def test_fuse_success_boost_changes_order(db):
    # Make r2 a strong match too, then boost r2 via successes and confirm it can move up.
    for _ in range(20):
        db.add_attempt("r2", worked=True)
    base = fuse(db, "", "memory error dataframe join OOM", "memory error dataframe join OOM",
                env=None, k=5)
    assert base[0].record.id == "r2"


def test_fuse_env_filter_drops_mismatch(db):
    from hindsight.schema import Env
    results = fuse(db, "compile_ext@build.py", "flash build", "flash build",
                   env=Env(cuda="11.8"), k=5, env_mode="filter")
    assert all(r.record.id != "r1" for r in results)


def test_fuse_exact_match_beats_weak_high_success(db):
    # r1 is the exact match with 0 attempts; r2 is a weaker match with strong success history.
    for _ in range(100):
        db.add_attempt("r2", worked=True)
    results = fuse(db, "compile_ext@build.py",
                   "flash attention build undefined symbol",
                   "flash attention build undefined symbol", env=None, k=5)
    assert results[0].record.id == "r1"


def test_fuse_ties_break_by_id(db):
    db.upsert(make_record("z9", problem_title="identical text token",
                          problem_body="identical text token"))
    db.upsert(make_record("a1", problem_title="identical text token",
                          problem_body="identical text token"))
    results = fuse(db, "", "identical text token", "identical text token", env=None, k=10)
    ids = [r.record.id for r in results if r.record.id in {"z9", "a1"}]
    assert ids == ["a1", "z9"]
