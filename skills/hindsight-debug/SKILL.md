---
name: hindsight-debug
description: Use when stuck on a long, cryptic, or environment-sensitive error (build failures, CUDA/toolchain mismatches, distributed-training crashes) that you cannot solve from memory. Searches the Hindsight commons for a known fix and reports whether it worked.
---

# Debugging with Hindsight

When an error resists your parametric knowledge, query the shared corpus before guessing.

1. **Search.** Call `search_error` with:
   - `trace`: the full stack trace / multi-line error, verbatim.
   - `message`: the headline error (e.g. `ImportError: undefined symbol`).
   - `attempt_summary`: one sentence on what you were doing ("Tried to build flash-attention 2.3.6 on Ubuntu 22.04 with torch 2.1 + CUDA 12.1").
   - `env`: `{cuda, os, arch, packages}` when known — this conditions and ranks results.
2. **Read the top results.** Each carries `why` (matched frames, env match, and attempts/successes/success_rate) plus `source` attribution. Prefer solutions with a higher success_rate.
3. **Get the full record.** Call `get_solution(id)` for the chosen result — it includes `solution_body`, `solution_code`, and the exact `repro_script`.
4. **Try the fix.**
5. **Always report back.** Call `report_attempt(id, worked)` with `worked=true` or `worked=false`. **Failures are the most valuable signal** — they tell the commons which popular fixes do not actually work. Add `notes` explaining what differed.

Attribution: results may be licensed CC-BY-SA (Stack Overflow) or MIT. Preserve `source.author`/`source.url` if you quote a solution.
