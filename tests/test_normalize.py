from hindsight.normalize import frame_tokens, normalize_trace, parse_env

PY_TRACE = '''Traceback (most recent call last):
  File "/home/user/proj/app.py", line 42, in main
    run_build()
  File "/opt/venv/lib/python3.11/site-packages/flash_attn/build.py", line 99, in run_build
    compile_ext()
ImportError: undefined symbol: _ZN3c104cuda9SetDeviceEi
'''


def test_normalize_extracts_ordered_frames():
    frames = normalize_trace(PY_TRACE)
    assert [f.symbol for f in frames] == ["compile_ext", "run_build"]
    assert [f.module for f in frames] == ["build.py", "app.py"]
    assert frames[0].position == 0
    assert frames[0].weight > frames[1].weight


def test_normalize_strips_paths_and_line_numbers():
    frames = normalize_trace(PY_TRACE)
    for f in frames:
        assert "/" not in f.module
        assert "line" not in f.symbol


def test_normalize_empty_trace_returns_empty():
    assert normalize_trace("no frames here") == []


def test_normalize_respects_top_k():
    frames = normalize_trace(PY_TRACE, top_k=1)
    assert len(frames) == 1


def test_frame_tokens_format():
    frames = normalize_trace(PY_TRACE)
    assert frame_tokens(frames) == "compile_ext@build.py run_build@app.py"


def test_parse_env_from_text():
    env = parse_env("Using torch==2.1.0 and flash-attn 2.3.6 on Ubuntu 22.04, CUDA 12.1, x86_64")
    assert env.packages["torch"] == "2.1.0"
    assert env.packages["flash-attn"] == "2.3.6"
    assert env.cuda == "12.1"
    assert env.os == "Ubuntu 22.04"
    assert env.arch == "x86_64"


def test_parse_env_from_labels():
    env = parse_env("", labels=["cuda-11.8", "aarch64"])
    assert env.cuda == "11.8"
    assert env.arch == "aarch64"


def test_parse_env_missing_is_none():
    env = parse_env("nothing useful")
    assert env.cuda is None and env.os is None and env.arch is None and env.packages == {}
