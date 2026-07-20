import pytest

from hindsight.api import create_app
from hindsight.db import Database
from tests.test_db import make_record


@pytest.fixture()
def client():
    db = Database()
    db.upsert(make_record("r1"))
    return create_app(db).test_client()


def test_health(client):
    assert client.get("/health").get_json() == {"status": "ok"}


def test_search_route(client):
    resp = client.post("/search", json={"message": "flash attention undefined symbol", "k": 5})
    body = resp.get_json()
    assert body["results"][0]["id"] == "r1"


def test_solution_route_and_404(client):
    assert client.get("/solution/r1").get_json()["id"] == "r1"
    assert client.get("/solution/missing").status_code == 404


def test_attempt_route_and_404(client):
    ok = client.post("/attempt", json={"id": "r1", "worked": True})
    assert ok.get_json()["worked"] is True
    assert client.post("/attempt", json={"id": "ghost", "worked": True}).status_code == 404


def test_attempt_malformed_body_is_400(client):
    assert client.post("/attempt", json={"worked": True}).status_code == 400


def test_submit_route_and_validation(client):
    good = client.post("/submit", json={
        "problem_body": "tried x",
        "raw_trace": '  File "/a.py", line 1, in f\n    g()\nErr: boom',
        "env": {"cuda": "12.1"}, "solution_body": "do y",
        "repro_script": {"interpreter": "bash", "content": "run"},
    })
    assert good.get_json()["duplicate"] is False
    bad = client.post("/submit", json={"problem_body": "", "raw_trace": "x",
                                        "env": {}, "solution_body": "y",
                                        "repro_script": {"interpreter": "b", "content": "c"}})
    assert bad.status_code == 400
