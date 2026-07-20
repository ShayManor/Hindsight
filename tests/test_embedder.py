import math

from hindsight.embedder import (
    DeterministicEmbedder, build_embed_input, build_query_input, env_text,
)
from hindsight.schema import Env, Frame, Record, ReproScript, Source
from datetime import datetime, timezone


def test_embed_is_deterministic():
    e = DeterministicEmbedder(dim=64)
    assert e.embed("flash attention build error") == e.embed("flash attention build error")


def test_embed_dimension_and_norm():
    e = DeterministicEmbedder(dim=128)
    v = e.embed("cuda undefined symbol")
    assert len(v) == 128
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_embed_empty_is_zero_vector():
    e = DeterministicEmbedder(dim=32)
    v = e.embed("")
    assert v == [0.0] * 32


def test_distinct_text_distinct_vector():
    e = DeterministicEmbedder(dim=256)
    assert e.embed("torch import error") != e.embed("segfault in cudnn")


def test_embedder_id():
    assert DeterministicEmbedder(dim=256).id == "det-hash-256"


def test_env_text():
    assert "cuda:12.1" in env_text(Env(cuda="12.1", packages={"torch": "2.1.0"}))
    assert env_text(None) == ""


def test_build_inputs_include_signal():
    rec = Record(
        id="a", problem_title="build fails", problem_body="tried flash-attn",
        raw_trace="x", normalized_trace=[Frame(symbol="f", module="m.py", position=0, weight=1.0)],
        language="python", error_class="ImportError", env=Env(cuda="12.1"),
        repro_script=ReproScript(interpreter="bash", content="pip install"),
        solution_body="pin", source=Source(platform="github", url="u", license="MIT"),
        posted=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ingested=datetime(2024, 1, 1, tzinfo=timezone.utc), index_version="v1",
    )
    text = build_embed_input(rec)
    assert "build fails" in text and "f@m.py" in text and "cuda:12.1" in text
    q = build_query_input("ImportError", "tried flash-attn", "f@m.py", Env(cuda="12.1"))
    assert "ImportError" in q and "f@m.py" in q and "cuda:12.1" in q
