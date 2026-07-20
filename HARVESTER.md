# Hindsight — Raw GitHub Issues Harvester

Pulls **closed** GitHub issues — plus their **full comments** and **full timeline** —
from a configurable list of repos and writes the **raw** GitHub API payloads to
local disk, one record per issue.

**Design principle: pull raw and complete, transform later.** This script does no
schema-fitting, extraction, normalization, dedup, or field selection. It stores
what GitHub returned so we never have to re-crawl because a field was dropped.

## Quick start

```bash
# 1. A GitHub personal access token (classic or fine-grained, public repo read is enough)
export GITHUB_TOKEN=ghp_xxx

# 2. Configure
cp config.example.json config.json
#    edit config.json: repo list + window if you want

# 3. Run
python3 harvest.py

# See what would be pulled without doing the expensive per-issue fetches
python3 harvest.py --dry-run

# Override the repo list ad hoc (repeatable)
python3 harvest.py --repo cli/cli --repo sharkdp/bat
```

Standard library only — no `pip install`. Python 3.7+.

## Output layout

```
data/
  dusty-nv__jetson-containers/
    _checkpoint.json          # resume state + progress counters for this repo
    issues/
      001234.json             # one raw record per issue (zero-padded issue number)
```

Each `issues/NNNNNN.json`:

```json
{
  "_meta": {                  // provenance ONLY — added by the harvester
    "repo": "owner/name",
    "issue_number": 1234,
    "fetched_at": "2026-07-19T...Z",
    "source_urls": { "issue_api": "...", "comments_api": "...", "timeline_api": "..." },
    "counts": { "comments": 4, "timeline_events": 11 }
  },
  "issue":    { ...verbatim /issues/{n} object... },
  "comments": [ ...verbatim comment objects... ],
  "timeline": [ ...verbatim timeline events... ]
}
```

`issue`, `comments`, and `timeline` are stored **exactly as GitHub returned them**.
Paginated lists are simply concatenated across pages — the only assembly done.
Nothing is renamed, dropped, or reshaped.

## Closed-by-PR linkage

The "issue was closed by a merged PR" signal (a pre-verified symptom→fix pair) is
**not extracted here** — that's transformation, and it's deferred. It's preserved
raw inside `timeline`, which is why the full timeline is pulled per issue:

- `cross-referenced` events referencing a pull request
- `closed` events (may carry a `commit_id`)
- `connected` / `disconnected` events

Later analysis reads these from the stored raw timeline.

## Config (`config.json`)

| Key | Meaning |
|-----|---------|
| `repos` | List of `owner/name` strings to harvest. |
| `output_dir` | Root directory for output (default `data`). |
| `updated_since_days` | Feeds the API `since` param (filters on **`updated_at`**). `null` = no bound. |
| `closed_within_days` | Client-side filter on **`closed_at`** (the API `since` can't do this). `null` = no bound. |
| `include_pull_requests` | The `/issues` endpoint also returns PRs; `false` skips them (target is issues). |
| `per_page` | Page size (max 100). |
| `request_delay_seconds` | Optional polite delay between requests (0 is fine authenticated). |
| `rate_limit_buffer` | Pause when remaining requests drop to/below this. |
| `max_retries` | Retry budget for 5xx / network errors. |

**Widening the window later never requires re-crawling logic changes** — all raw
timestamps are stored, so re-run with a larger window and only the newly-in-window
issues get fetched (existing records are skipped by file presence).

## Rate limits & resume

- Token read from `GITHUB_TOKEN` (or `GH_TOKEN`).
- Authenticated REST is 5,000 req/hr. Per-issue timeline + comments dominate volume.
- Proactively sleeps to `X-RateLimit-Reset` when the remaining budget nears
  `rate_limit_buffer`; honors `Retry-After` and secondary-rate-limit responses;
  exponential backoff w/ jitter on 5xx and network errors.
- **Resumable:** a record on disk = done, so an interrupted multi-hour run resumes
  by re-walking the cheap list endpoint and skipping issues already stored.
  Kill it any time (Ctrl-C); re-run the same command to continue.

## API choice note (REST vs GraphQL)

Uses the **REST** API deliberately: each endpoint returns a self-contained JSON
object we can store verbatim, matching "capture everything, decide fields later."
GraphQL could fetch issue + timeline + closing-PR in fewer round-trips (and has a
purpose-built `closedByPullRequestsReferences`), but it forces field selection up
front and has a harder-to-reason-about point-based rate limit. Revisit if request
volume becomes the bottleneck.
