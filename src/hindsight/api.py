from __future__ import annotations

from flask import Flask, jsonify, request

from hindsight.db import Database
from hindsight.mcp_server import build_tools
from hindsight.submit import SubmissionError


def create_app(db: Database) -> Flask:
    app = Flask(__name__)
    tools = build_tools(db)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.post("/search")
    def search():
        p = request.get_json(force=True)
        results = tools.search_error(
            trace=p.get("trace", ""), message=p.get("message", ""),
            attempt_summary=p.get("attempt_summary", ""), env=p.get("env"),
            k=p.get("k", 10),
        )
        return jsonify({"results": results})

    @app.get("/solution/<id>")
    def solution(id: str):
        sol = tools.get_solution(id)
        if sol is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(sol)

    @app.post("/attempt")
    def attempt():
        p = request.get_json(force=True)
        if "id" not in p or "worked" not in p:
            return jsonify({"error": "id and worked are required"}), 400
        try:
            return jsonify(tools.report_attempt(
                id=p["id"], worked=p["worked"], notes=p.get("notes"),
                agent_id=p.get("agent_id")))
        except KeyError:
            return jsonify({"error": "record not found"}), 404

    @app.post("/submit")
    def submit():
        p = request.get_json(force=True)
        try:
            return jsonify(tools.submit_record(
                problem_body=p.get("problem_body", ""), raw_trace=p.get("raw_trace", ""),
                env=p.get("env", {}), solution_body=p.get("solution_body", ""),
                repro_script=p.get("repro_script", {}), problem_title=p.get("problem_title"),
                solution_code=p.get("solution_code"), language=p.get("language", "unknown"),
                framework=p.get("framework"), error_class=p.get("error_class", "unknown"),
                author=p.get("author")))
        except SubmissionError as exc:
            return jsonify({"error": str(exc)}), 400

    return app
