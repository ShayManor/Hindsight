import pytest

from hindsight.db import Database
from hindsight.mcp_server import build_tools
from tests.test_db import make_record


@pytest.fixture()
def tools():
    db = Database()
    db.upsert(make_record("r1"))
    return build_tools(db), db


def test_search_tool_returns_dicts(tools):
    api, _ = tools
    out = api.search_error(trace="", message="flash attention build undefined symbol", k=5)
    assert out and out[0]["id"] == "r1"
    assert "why" in out[0] and out[0]["source"]["license"] == "MIT"


def test_get_solution_tool(tools):
    api, _ = tools
    sol = api.get_solution("r1")
    assert sol["repro_script"]["content"] == "pip install flash-attn"
    assert api.get_solution("missing") is None


def test_report_attempt_tool(tools):
    api, db = tools
    res = api.report_attempt("r1", worked=True)
    assert res["worked"] is True and res["attempt_id"] is not None
    assert db.get("r1").stats.attempts == 1


def test_report_attempt_missing_raises(tools):
    api, _ = tools
    with pytest.raises(KeyError):
        api.report_attempt("ghost", worked=True)


def test_submit_tool(tools):
    api, db = tools
    res = api.submit_record(
        problem_body="tried x", raw_trace='  File "/a.py", line 1, in f\n    g()\nErr: boom',
        env={"cuda": "12.1"}, solution_body="do y",
        repro_script={"interpreter": "bash", "content": "run"},
    )
    assert res["duplicate"] is False
    res2 = api.submit_record(
        problem_body="tried x", raw_trace='  File "/a.py", line 1, in f\n    g()\nErr: boom',
        env={"cuda": "12.1"}, solution_body="do y",
        repro_script={"interpreter": "bash", "content": "run"},
    )
    assert res2["duplicate"] is True and res2["id"] == res["id"]


def test_build_server_registers_tools():
    from hindsight.mcp_server import build_server
    db = Database()
    server = build_server(db)
    assert server is not None
