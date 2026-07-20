# Hindsight — Raw Harvesters

Two source harvesters, same design principle (**pull raw and complete, transform
later**) and same conventions (thin `_meta` container over verbatim payloads,
per-source checkpoints, on-disk dedup, rate-limit safety, resumable):

- **GitHub Issues** — `harvest.py` (see below)
- **Stack Overflow** — `harvest_stackoverflow.py` (see [Stack Overflow](#stack-overflow-harvester) section)

---

# Raw GitHub Issues Harvester

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
python3 harvest.py --repo cli/cli --repo BurntSushi/ripgrep
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

---

# Stack Overflow harvester

`harvest_stackoverflow.py` pulls **questions** — plus their full **answers**,
**comments**, and **timeline** — for a list of tags from the Stack Exchange API
and writes the **raw** payloads to disk, one record per question.

The Stack Overflow analog of "issue closed by a merged PR" is a **question with an
accepted answer** — a pre-verified symptom→fix pair. As on the GitHub side, that
signal is **not extracted**; it's preserved raw in the question's
`accepted_answer_id` and each answer's `is_accepted` flag.

## Quick start

```bash
export STACK_APP_KEY=...          # optional but strongly recommended (quota: 300/day -> 10,000/day)
cp config_stackoverflow.example.json config_stackoverflow.json
python3 harvest_stackoverflow.py
python3 harvest_stackoverflow.py --dry-run
python3 harvest_stackoverflow.py --tag python --tag docker   # override tags
```

## Output layout

```
data_stackoverflow/
  _checkpoints/
    tag__cuda.json              # per-tag resume state (window + completed slices)
  questions/
    0075107329.json            # one raw record per question (keyed by global question_id)
```

Each `questions/NNNNNNNNNN.json`:

```json
{
  "_meta": { "site": "stackoverflow", "question_id": 75107329, "surfaced_by_tag": "fastapi",
             "counts": { "answers": 2, "answer_comments": 1, "timeline_events": 24 }, ... },
  "question":          { ...verbatim question object, with body... },
  "answers":           [ ...verbatim answer objects, with bodies... ],
  "question_comments": [ ...verbatim comments, with bodies... ],
  "answer_comments":   [ ...verbatim comments, with bodies... ],
  "timeline":          [ ...verbatim question timeline events... ]
}
```

Questions are keyed by **global `question_id`**, so a question surfaced under two
tags is fetched once (on-disk presence dedups).

## Config (`config_stackoverflow.json`)

| Key | Meaning |
|-----|---------|
| `tags` | Tags to crawl (the repo-list analog). Each crawled separately. |
| `output_dir` | Root output directory (default `data_stackoverflow`). |
| `site` | Stack Exchange site (default `stackoverflow`). |
| `window_days` | Recency window size. |
| `date_field` | `activity` (touched recently) or `creation` (asked recently). |
| `window_slice_days` | Sub-window size; keeps each query under the 25k deep-paging cap. |
| `min_answers` | Minimum answer count (search/advanced `answers`; `0` includes unanswered). |
| `accepted_only` | `true` restricts to questions with an accepted answer. Default `false` (capture all answered, identify verified later). |
| `pagesize` | Page size (max 100). |
| `min_quota_remaining` | Stop a crawl when daily quota drops to/below this. |
| `max_retries` | Retry budget for throttles / 5xx / network errors. |

## API notes

- **Bodies require `filter=withbody`** — the default API omits question/answer/comment
  text. Used on every call.
- **Responses are gzip-encoded** and decompressed explicitly.
- **App key** (`STACK_APP_KEY` env) raises quota from 300/day to 10,000/day. Optional
  `STACK_ACCESS_TOKEN` for authenticated calls (not needed for public read).
- The API **`backoff`** field is a mandatory wait and is always honored; `quota_remaining`
  is tracked and a crawl stops cleanly (checkpointed) when it runs low or the daily quota
  resets are needed.
- **Deep-paging cap (25,000 results/query):** the window is sliced into sub-windows so a
  busy tag never silently truncates; a warning is logged if a slice ever hits the cap.

## Future option (not built)

Per-question requests could be collapsed by building a **custom filter** (via
`/filters/create`) that inlines answers + comments into the question response,
cutting calls per question. `withbody` + explicit sub-resource fetches were chosen
for faithful, self-contained raw payloads (mirrors the REST-over-GraphQL choice on
the GitHub side). Revisit if quota becomes the bottleneck.
