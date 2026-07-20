import pytest

from hindsight.db import Database
from hindsight.search import index_version, search_error
from tests.test_db import make_record

TRACE = '''Traceback (most recent call last):
  File "/x/app.py", line 42, in main
    run_build()
  File "/x/flash_attn/build.py", line 99, in run_build
    compile_ext()
ImportError: undefined symbol
'''


@pytest.fixture()
def db():
    d = Database()
    d.upsert(make_record("r1"))
    d.upsert(make_record("r2", problem_title="pandas oom", problem_body="merge memory",
                          error_class="MemoryError"))
    return d


def test_index_version(db):
    assert index_version(db.embedder) == "idx-det-hash-256"


def test_search_error_finds_relevant(db):
    results = search_error(db, TRACE, message="ImportError undefined symbol",
                           attempt_summary="tried to build flash attention", k=5)
    assert results[0].record.id == "r1"
    assert results[0].record.source.license == "MIT"


def test_search_error_deterministic(db):
    a = search_error(db, TRACE, message="ImportError", k=5)
    b = search_error(db, TRACE, message="ImportError", k=5)
    assert [r.record.id for r in a] == [r.record.id for r in b]


def test_search_error_empty_trace(db):
    results = search_error(db, "", message="pandas merge memory oom", k=5)
    assert any(r.record.id == "r2" for r in results)
