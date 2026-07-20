from __future__ import annotations

from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from hindsight.db import Database
from hindsight.index import SearchResult
from hindsight.schema import Env
from hindsight.search import search_error as _search_error
from hindsight.submit import submit_record as _submit_record


def _result_dict(r: SearchResult) -> dict:
    return {
        "id": r.record.id,
        "problem_title": r.record.problem_title,
        "solution_body": r.record.solution_body,
        "score": r.score,
        "why": r.why,
        "source": r.record.source.model_dump(),
    }


def build_tools(db: Database) -> SimpleNamespace:
    def search_error(trace: str, message: str = "", attempt_summary: str = "",
                     env: dict | None = None, k: int = 10) -> list[dict]:
        env_obj = Env.model_validate(env) if env else None
        results = _search_error(db, trace, message, attempt_summary, env_obj, k)
        return [_result_dict(r) for r in results]

    def get_solution(id: str) -> dict | None:
        rec = db.get_solution(id)
        return rec.model_dump(mode="json") if rec else None

    def report_attempt(id: str, worked: bool, notes: str | None = None,
                       agent_id: str | None = None) -> dict:
        attempt = db.add_attempt(id, worked=worked, notes=notes, agent_id=agent_id)
        return {"attempt_id": attempt.attempt_id, "record_id": attempt.record_id,
                "worked": attempt.worked}

    def submit_record(problem_body: str, raw_trace: str, env: dict, solution_body: str,
                      repro_script: dict, problem_title: str | None = None,
                      solution_code: list[str] | None = None, language: str = "unknown",
                      framework: str | None = None, error_class: str = "unknown",
                      author: str | None = None) -> dict:
        before = db.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        rid = _submit_record(db, problem_body=problem_body, raw_trace=raw_trace, env=env,
                             solution_body=solution_body, repro_script=repro_script,
                             problem_title=problem_title, solution_code=solution_code,
                             language=language, framework=framework, error_class=error_class,
                             author=author)
        after = db.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        return {"id": rid, "duplicate": after == before}

    return SimpleNamespace(search_error=search_error, get_solution=get_solution,
                           report_attempt=report_attempt, submit_record=submit_record)


def build_server(db: Database) -> FastMCP:
    tools = build_tools(db)
    server = FastMCP("hindsight")
    server.tool()(tools.search_error)
    server.tool()(tools.get_solution)
    server.tool()(tools.report_attempt)
    server.tool()(tools.submit_record)
    return server


def main() -> None:  # pragma: no cover
    build_server(Database("hindsight.db")).run()


if __name__ == "__main__":  # pragma: no cover
    main()
