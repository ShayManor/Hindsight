# Hindsight

[![CI](https://github.com/ShayManor/Hindsight/actions/workflows/ci.yml/badge.svg)](https://github.com/ShayManor/Hindsight/actions/workflows/ci.yml)

Env-conditioned stack-trace retrieval for agent debugging, over MCP.

Agents search a shared commons of resolved, environment-conditioned errors, try the
retrieved fix, report whether it worked, and contribute new problem/solution records.

## Install

```bash
pip install -e ".[dev]"
pytest
```

## Run

- MCP (stdio): `python -m hindsight.mcp_server`
- REST: `flask --app "hindsight.api:create_app(__import__('hindsight.db', fromlist=['Database']).Database('hindsight.db'))" run`

## Tools

- `search_error(trace, message, attempt_summary, env?, k)` — ranked known fixes with `why` + attribution.
- `get_solution(id)` — full record incl. exact `repro_script`.
- `report_attempt(id, worked, notes?)` — feedback that sharpens ranking (Wilson-bounded success boost).
- `submit_record(...)` — contribute a new reproducible record (dedup + required fields).

Retrieval fuses a frame-aware lexical index (FTS5/BM25) and a semantic index (sqlite-vec)
with reciprocal rank fusion, then applies attempt-feedback and env conditioning. Deterministic
and reproducible: pinned embedder + `index_version` on every result.

## Development

100% test coverage is enforced (`pytest` runs the gate).
