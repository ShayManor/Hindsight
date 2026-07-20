from datetime import datetime, timezone

import pytest

from hindsight.db import Database
from hindsight.schema import Env, ReproScript
from hindsight.submit import SubmissionError, submit_record


def _submit(db, **over):
    args = dict(
        problem_body="tried building flash-attn from source",
        raw_trace='  File "/x/build.py", line 9, in run\n    ext()\nImportError: undefined symbol',
        env=Env(cuda="12.1"),
        solution_body="pin torch==2.1.0",
        repro_script=ReproScript(interpreter="bash", content="pip install flash-attn"),
        posted=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ingested=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    args.update(over)
    return submit_record(db, **args)


def test_submit_creates_user_record():
    db = Database()
    rid = _submit(db)
    rec = db.get(rid)
    assert rec.source.platform == "user_submitted"
    assert rec.source.license == "MIT"
    assert rec.repro_script.content == "pip install flash-attn"


def test_submit_is_idempotent_dedup():
    db = Database()
    rid1 = _submit(db)
    rid2 = _submit(db)
    assert rid1 == rid2
    assert db.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 1


@pytest.mark.parametrize("missing", ["problem_body", "raw_trace", "solution_body"])
def test_missing_required_text_rejected(missing):
    db = Database()
    with pytest.raises(SubmissionError):
        _submit(db, **{missing: ""})


def test_missing_repro_script_rejected():
    db = Database()
    with pytest.raises(SubmissionError):
        _submit(db, repro_script=None)


def test_missing_env_rejected():
    db = Database()
    with pytest.raises(SubmissionError):
        _submit(db, env=None)


def test_accepts_dict_env_and_repro():
    db = Database()
    rid = _submit(db, env={"cuda": "12.1"}, repro_script={"interpreter": "bash", "content": "x"})
    assert db.get(rid).env.cuda == "12.1"


def test_empty_env_object_rejected():
    db = Database()
    with pytest.raises(SubmissionError):
        _submit(db, env=Env())


def test_empty_env_dict_rejected():
    db = Database()
    with pytest.raises(SubmissionError):
        _submit(db, env={})
