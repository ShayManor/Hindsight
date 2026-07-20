#!/usr/bin/env python3
"""
Hindsight raw GitHub Issues harvester.

Pulls CLOSED issues (plus their full comments and full timeline) from a list of
repos and writes the RAW GitHub API payloads to local disk, one record per issue.

Guiding principle: pull raw and complete, transform later. This script does NOT
transform, reshape, extract, normalize, or dedup anything. Each stored record is
a thin container:

    {"_meta": {...provenance...},
     "issue":    <verbatim /issues/{n} object>,
     "comments": [<verbatim comment objects>, ...],
     "timeline": [<verbatim timeline events>, ...]}

The `issue`, `comments`, and `timeline` values are stored exactly as GitHub
returned them. Paginated list endpoints are simply concatenated across pages;
no fields are selected, renamed, or dropped.

The closed-by-PR signal (a pre-verified symptom->fix pair) is NOT extracted here.
It lives in the raw timeline (`cross-referenced` / `closed` / `connected` events
referencing a PR), which is stored in full so later analysis can identify it.

Usage:
    export GITHUB_TOKEN=ghp_...
    python3 harvest.py                 # uses ./config.json
    python3 harvest.py --config foo.json
    python3 harvest.py --repo owner/name   # override repo list (repeatable)
    python3 harvest.py --dry-run           # list what would be pulled, fetch nothing

Requires: Python 3.7+ standard library only. No pip install.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

HARVESTER_VERSION = "1.0"
API_ROOT = "https://api.github.com"
USER_AGENT = "hindsight-harvester/%s" % HARVESTER_VERSION


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    """ISO-8601 UTC with trailing Z, matching GitHub's format."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_gh_ts(value):
    """Parse a GitHub ISO-8601 timestamp (e.g. 2023-01-02T03:04:05Z) -> aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def log(msg):
    print("%s  %s" % (iso(now_utc()), msg), flush=True)


def slugify_repo(full_name):
    return full_name.replace("/", "__")


def atomic_write_json(path, obj):
    """Write JSON to a temp file then rename, so an interrupted write never
    leaves a half-written record on disk."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

DEFAULTS = {
    "output_dir": "data",
    "updated_since_days": 365,
    "closed_within_days": 365,
    "include_pull_requests": False,
    "per_page": 100,
    "request_delay_seconds": 0.0,
    "rate_limit_buffer": 75,
    "max_retries": 6,
    "repos": [],
}


def load_config(path):
    if not os.path.exists(path):
        sys.exit(
            "Config file not found: %s\n"
            "Copy config.example.json to config.json and edit it." % path
        )
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    cfg = dict(DEFAULTS)
    for key, value in raw.items():
        if key.startswith("_comment"):
            continue
        cfg[key] = value
    return cfg


# --------------------------------------------------------------------------- #
# HTTP client with rate-limit safety + retries
# --------------------------------------------------------------------------- #

class GitHubClient:
    def __init__(self, token, per_page=100, request_delay=0.0,
                 rate_limit_buffer=75, max_retries=6):
        self.token = token
        self.per_page = per_page
        self.request_delay = request_delay
        self.rate_limit_buffer = rate_limit_buffer
        self.max_retries = max_retries
        self.request_count = 0

    def _headers(self):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        # Omit Authorization entirely when unauthenticated (empty token) so the
        # request isn't rejected as bad credentials. Note: unauthenticated is
        # only 60 req/hr — fine for a smoke test, not for a real harvest.
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        return headers

    def _sleep_until(self, reset_epoch, reason):
        """Sleep until a unix reset timestamp (plus a small buffer)."""
        try:
            reset = float(reset_epoch)
        except (TypeError, ValueError):
            return
        wait = max(0.0, reset - time.time()) + 2.0
        if wait > 0:
            log("%s: sleeping %.0fs until rate-limit reset" % (reason, wait))
            time.sleep(wait)

    def _handle_rate_limit_headers(self, headers):
        """Proactively pause when the primary rate-limit budget is nearly spent."""
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")
        if remaining is None:
            return
        try:
            remaining = int(remaining)
        except ValueError:
            return
        if remaining <= self.rate_limit_buffer:
            self._sleep_until(reset, "primary rate limit low (remaining=%d)" % remaining)

    def get(self, url, params=None):
        """GET a single URL. Returns (parsed_json, response_headers).

        Handles primary + secondary rate limits and retries transient failures
        with exponential backoff. Raises on non-retryable 4xx (except rate limit)
        and after exhausting retries.
        """
        if params:
            url = url + "?" + urllib.parse.urlencode(params)

        attempt = 0
        while True:
            attempt += 1
            if self.request_delay:
                time.sleep(self.request_delay)

            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.request_count += 1
                    body = resp.read().decode("utf-8")
                    headers = {k: v for k, v in resp.headers.items()}
                    self._handle_rate_limit_headers(headers)
                    return json.loads(body) if body else None, headers

            except urllib.error.HTTPError as err:
                headers = {k: v for k, v in (err.headers.items() if err.headers else [])}
                status = err.code
                try:
                    err_body = err.read().decode("utf-8")
                except Exception:
                    err_body = ""

                # 403/429 -> rate limiting (primary or secondary).
                if status in (403, 429):
                    retry_after = headers.get("Retry-After")
                    remaining = headers.get("X-RateLimit-Remaining")
                    if retry_after is not None:
                        wait = float(retry_after) + 1.0
                        log("secondary rate limit: sleeping %.0fs (Retry-After)" % wait)
                        time.sleep(wait)
                        continue
                    if remaining is not None and remaining.isdigit() and int(remaining) == 0:
                        self._sleep_until(headers.get("X-RateLimit-Reset"),
                                          "primary rate limit exhausted")
                        continue
                    # Some secondary limits give neither header -> back off.
                    if "secondary rate limit" in err_body.lower():
                        wait = min(60 * attempt, 300)
                        log("secondary rate limit (no header): sleeping %.0fs" % wait)
                        time.sleep(wait)
                        continue
                    # A genuine 403 (e.g. forbidden repo) -> not retryable.
                    raise RuntimeError("403 Forbidden for %s: %s" % (url, err_body[:300]))

                if status == 404:
                    raise FileNotFoundError("404 Not Found: %s" % url)

                # 5xx -> retry with backoff.
                if 500 <= status < 600 and attempt <= self.max_retries:
                    backoff = min(2 ** attempt + random.uniform(0, 1), 120)
                    log("HTTP %d for %s: retry %d/%d in %.1fs"
                        % (status, url, attempt, self.max_retries, backoff))
                    time.sleep(backoff)
                    continue

                raise RuntimeError("HTTP %d for %s: %s" % (status, url, err_body[:300]))

            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt <= self.max_retries:
                    backoff = min(2 ** attempt + random.uniform(0, 1), 120)
                    log("network error for %s (%s): retry %d/%d in %.1fs"
                        % (url, err, attempt, self.max_retries, backoff))
                    time.sleep(backoff)
                    continue
                raise

    def get_paginated(self, url, params=None):
        """Follow Link rel=next and return the concatenated list of items.

        The items are returned verbatim (no field selection). This is the ONLY
        assembly the harvester does: gluing pages of the same list together.
        """
        params = dict(params or {})
        params.setdefault("per_page", self.per_page)
        items = []
        next_url = url + "?" + urllib.parse.urlencode(params)
        while next_url:
            page, headers = self.get(next_url)
            if page is None:
                break
            if not isinstance(page, list):
                raise RuntimeError("Expected a list from %s, got %s" % (next_url, type(page)))
            items.extend(page)
            next_url = _parse_next_link(headers.get("Link"))
        return items


def _parse_next_link(link_header):
    """Extract the rel="next" URL from a GitHub Link header, or None."""
    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url_part = segments[0].strip()
        if not (url_part.startswith("<") and url_part.endswith(">")):
            continue
        rels = [s.strip() for s in segments[1:]]
        if 'rel="next"' in rels:
            return url_part[1:-1]
    return None


# --------------------------------------------------------------------------- #
# checkpoint
# --------------------------------------------------------------------------- #

def checkpoint_path(repo_dir):
    return os.path.join(repo_dir, "_checkpoint.json")


def load_checkpoint(repo_dir):
    path = checkpoint_path(repo_dir)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            pass
    return None


def save_checkpoint(repo_dir, cp):
    cp["updated_at"] = iso(now_utc())
    atomic_write_json(checkpoint_path(repo_dir), cp)


# --------------------------------------------------------------------------- #
# harvest
# --------------------------------------------------------------------------- #

def issue_record_path(issues_dir, number):
    # Zero-pad for readable sorting; number is the authoritative id.
    return os.path.join(issues_dir, "%06d.json" % number)


def harvest_repo(client, repo, cfg, dry_run=False):
    owner_repo = repo.strip()
    if "/" not in owner_repo:
        log("SKIP invalid repo (need owner/name): %r" % repo)
        return

    repo_dir = os.path.join(cfg["output_dir"], slugify_repo(owner_repo))
    issues_dir = os.path.join(repo_dir, "issues")
    if not dry_run:
        os.makedirs(issues_dir, exist_ok=True)

    # Compute the recency window.
    updated_since = None
    if cfg.get("updated_since_days") is not None:
        updated_since = now_utc() - timedelta(days=cfg["updated_since_days"])
    closed_after = None
    if cfg.get("closed_within_days") is not None:
        closed_after = now_utc() - timedelta(days=cfg["closed_within_days"])

    window = {
        "updated_since": iso(updated_since) if updated_since else None,
        "closed_after": iso(closed_after) if closed_after else None,
        "updated_since_days": cfg.get("updated_since_days"),
        "closed_within_days": cfg.get("closed_within_days"),
        "include_pull_requests": cfg.get("include_pull_requests", False),
    }

    cp = load_checkpoint(repo_dir) or {
        "repo": owner_repo,
        "started_at": iso(now_utc()),
        "status": "in_progress",
    }
    # If the window changed since a previous run, reset the "done" status so we
    # re-walk the list (existing records are still skipped by file presence).
    if cp.get("window") != window:
        cp["window"] = window
        cp["status"] = "in_progress"
    cp.setdefault("issues_seen", 0)
    cp.setdefault("issues_stored", 0)
    cp.setdefault("issues_skipped_pr", 0)
    cp.setdefault("issues_skipped_out_of_window", 0)
    cp.setdefault("issues_already_on_disk", 0)

    log("=== %s ===" % owner_repo)
    log("window: updated_since=%s  closed_after=%s  include_prs=%s"
        % (window["updated_since"], window["closed_after"], window["include_pull_requests"]))

    list_params = {
        "state": "closed",
        "sort": "updated",
        "direction": "asc",
        "per_page": cfg["per_page"],
    }
    if updated_since:
        list_params["since"] = iso(updated_since)

    list_url = "%s/repos/%s/issues" % (API_ROOT, owner_repo)

    # Walk the issues list page by page so we can process + checkpoint as we go
    # rather than buffering every issue in memory first.
    next_url = list_url + "?" + urllib.parse.urlencode(list_params)
    seen_this_run = 0

    while next_url:
        try:
            page, headers = client.get(next_url)
        except FileNotFoundError:
            log("repo not found or no access: %s" % owner_repo)
            cp["status"] = "error_not_found"
            if not dry_run:
                save_checkpoint(repo_dir, cp)
            return

        if not page:
            break

        for issue in page:
            cp["issues_seen"] += 1
            seen_this_run += 1
            number = issue.get("number")

            # The /issues endpoint returns PRs too; skip unless asked to keep them.
            is_pr = "pull_request" in issue
            if is_pr and not cfg.get("include_pull_requests", False):
                cp["issues_skipped_pr"] += 1
                continue

            # Client-side closed_at window (the API `since` is updated_at based).
            closed_dt = parse_gh_ts(issue.get("closed_at"))
            if closed_after and (closed_dt is None or closed_dt < closed_after):
                cp["issues_skipped_out_of_window"] += 1
                continue

            record_path = issue_record_path(issues_dir, number)
            if os.path.exists(record_path):
                cp["issues_already_on_disk"] += 1
                continue

            if dry_run:
                log("[dry-run] would fetch #%d (%s)"
                    % (number, "PR" if is_pr else "issue"))
                cp["issues_stored"] += 1
                continue

            _fetch_and_store_issue(client, owner_repo, issue, record_path)
            cp["issues_stored"] += 1
            cp["last_issue_number"] = number
            cp["last_updated_at_processed"] = issue.get("updated_at")

            if cp["issues_stored"] % 25 == 0:
                save_checkpoint(repo_dir, cp)
                log("  progress: stored=%d seen=%d (skipped pr=%d, out-of-window=%d, on-disk=%d)"
                    % (cp["issues_stored"], cp["issues_seen"], cp["issues_skipped_pr"],
                       cp["issues_skipped_out_of_window"], cp["issues_already_on_disk"]))

        next_url = _parse_next_link(headers.get("Link"))
        if not dry_run:
            save_checkpoint(repo_dir, cp)

    cp["status"] = "done"
    if not dry_run:
        save_checkpoint(repo_dir, cp)
    log("done %s: stored=%d seen=%d (skipped pr=%d, out-of-window=%d, already-on-disk=%d)"
        % (owner_repo, cp["issues_stored"], cp["issues_seen"], cp["issues_skipped_pr"],
           cp["issues_skipped_out_of_window"], cp["issues_already_on_disk"]))


def _fetch_and_store_issue(client, owner_repo, issue, record_path):
    """Fetch the full comments + full timeline for one issue and write the raw
    record. The issue object from the list endpoint is already the full issue
    payload, stored verbatim."""
    number = issue["number"]
    base = "%s/repos/%s/issues/%d" % (API_ROOT, owner_repo, number)

    comments = client.get_paginated(base + "/comments")

    # Timeline carries the closed-by-PR linkage (cross-referenced / closed /
    # connected events). Stored raw and complete; nothing extracted here.
    timeline = client.get_paginated(base + "/timeline")

    record = {
        "_meta": {
            "harvester_version": HARVESTER_VERSION,
            "repo": owner_repo,
            "issue_number": number,
            "is_pull_request": "pull_request" in issue,
            "fetched_at": iso(now_utc()),
            "source_urls": {
                "issue_api": issue.get("url"),
                "issue_html": issue.get("html_url"),
                "comments_api": base + "/comments",
                "timeline_api": base + "/timeline",
            },
            "counts": {
                "comments": len(comments),
                "timeline_events": len(timeline),
            },
        },
        "issue": issue,
        "comments": comments,
        "timeline": timeline,
    }
    atomic_write_json(record_path, record)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw GitHub Issues harvester (Hindsight)")
    parser.add_argument("--config", default="config.json", help="path to config JSON")
    parser.add_argument("--repo", action="append", default=None,
                        help="override repo list (repeatable): owner/name")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be fetched without fetching per-issue data")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.repo:
        cfg["repos"] = args.repo

    repos = cfg.get("repos") or []
    if not repos:
        sys.exit("No repos configured. Add some to 'repos' in %s (or pass --repo)." % args.config)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token and not args.dry_run:
        sys.exit("Set GITHUB_TOKEN (or GH_TOKEN) in the environment.")

    client = GitHubClient(
        token=token or "",
        per_page=cfg["per_page"],
        request_delay=cfg["request_delay_seconds"],
        rate_limit_buffer=cfg["rate_limit_buffer"],
        max_retries=cfg["max_retries"],
    )

    log("harvesting %d repo(s) -> %s%s"
        % (len(repos), cfg["output_dir"], " [DRY RUN]" if args.dry_run else ""))

    started = time.time()
    for repo in repos:
        try:
            harvest_repo(client, repo, cfg, dry_run=args.dry_run)
        except KeyboardInterrupt:
            log("interrupted by user; checkpoint is saved. Re-run to resume.")
            raise
        except Exception as err:  # keep going to the next repo
            log("ERROR on %s: %s" % (repo, err))

    log("all repos processed in %.0fs; %d API requests made."
        % (time.time() - started, client.request_count))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
