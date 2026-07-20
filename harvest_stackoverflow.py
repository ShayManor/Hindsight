#!/usr/bin/env python3
"""
Hindsight raw Stack Overflow harvester.

Pulls QUESTIONS (plus their full answers, comments, and timeline) for a list of
tags from the Stack Exchange API and writes the RAW payloads to local disk, one
record per question.

Guiding principle (same as the GitHub harvester): pull raw and complete,
transform later. No schema, extraction, normalization, or field selection.

Each stored record is a thin container:

    {"_meta": {...provenance...},
     "question":         <verbatim question object, with body>,
     "answers":          [<verbatim answer objects, with bodies>, ...],
     "question_comments":[<verbatim comment objects, with bodies>, ...],
     "answer_comments":  [<verbatim comment objects, with bodies>, ...],
     "timeline":         [<verbatim question timeline events>, ...]}

The "verified fix" signal (a pre-verified symptom->fix pair) is NOT extracted
here. It's preserved raw: the question's `accepted_answer_id` and each answer's
`is_accepted` flag. Later analysis reads those from the stored raw payloads.

Stack Exchange API notes baked in here:
  * Bodies are only returned with filter=withbody (the default omits them).
  * Responses are gzip-encoded; decompressed explicitly.
  * An app key (STACK_APP_KEY env) raises the quota from 300/day to 10,000/day.
  * The `backoff` field is a mandatory wait and is always honored.
  * The window is sliced to stay under the 25,000-results deep-paging cap.

Usage:
    export STACK_APP_KEY=...        # optional but strongly recommended
    python3 harvest_stackoverflow.py
    python3 harvest_stackoverflow.py --config foo.json
    python3 harvest_stackoverflow.py --tag python --tag docker   # override tags
    python3 harvest_stackoverflow.py --dry-run

Requires: Python 3.7+ standard library only. No pip install.
"""

import argparse
import gzip
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
API_ROOT = "https://api.stackexchange.com/2.3"
USER_AGENT = "hindsight-harvester/%s" % HARVESTER_VERSION


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unix(dt):
    return int(dt.timestamp())


def log(msg):
    print("%s  %s" % (iso(now_utc()), msg), flush=True)


def slugify_tag(tag):
    keep = []
    for ch in tag:
        keep.append(ch if (ch.isalnum() or ch in "-_.") else "_")
    return "".join(keep)


def atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #

DEFAULTS = {
    "output_dir": "data_stackoverflow",
    "site": "stackoverflow",
    "window_days": 365,
    "date_field": "activity",
    "window_slice_days": 30,
    "min_answers": 1,
    "accepted_only": False,
    "pagesize": 100,
    "request_delay_seconds": 0.0,
    "min_quota_remaining": 20,
    "max_retries": 6,
    "tags": [],
}


def load_config(path):
    if not os.path.exists(path):
        sys.exit(
            "Config file not found: %s\n"
            "Copy config_stackoverflow.example.json to config_stackoverflow.json and edit it." % path
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
# Stack Exchange API client: gzip, quota, backoff, retries
# --------------------------------------------------------------------------- #

class QuotaExhausted(Exception):
    pass


class StackExchangeClient:
    def __init__(self, site, app_key=None, access_token=None, pagesize=100,
                 request_delay=0.0, min_quota_remaining=20, max_retries=6):
        self.site = site
        self.app_key = app_key
        self.access_token = access_token
        self.pagesize = pagesize
        self.request_delay = request_delay
        self.min_quota_remaining = min_quota_remaining
        self.max_retries = max_retries
        self.request_count = 0
        self.quota_remaining = None
        self._next_allowed_at = 0.0  # enforced by the API `backoff` field

    def _common_params(self):
        params = {"site": self.site}
        if self.app_key:
            params["key"] = self.app_key
        if self.access_token:
            params["access_token"] = self.access_token
        return params

    def _respect_backoff(self):
        wait = self._next_allowed_at - time.time()
        if wait > 0:
            log("honoring API backoff: sleeping %.1fs" % wait)
            time.sleep(wait)

    def _read_body(self, resp_or_err):
        raw = resp_or_err.read()
        enc = ""
        try:
            enc = (resp_or_err.headers.get("Content-Encoding") or "").lower()
        except Exception:
            pass
        # SE always gzips; decompress by header or by magic bytes.
        if enc == "gzip" or (len(raw) > 2 and raw[0] == 0x1F and raw[1] == 0x8B):
            raw = gzip.decompress(raw)
        return raw.decode("utf-8")

    def get(self, path, params=None):
        """GET a Stack Exchange API method. Returns the parsed wrapper dict
        ({items, has_more, quota_remaining, backoff, ...}). Honors backoff and
        quota; retries transient failures with exponential backoff."""
        url = "%s/%s" % (API_ROOT, path.lstrip("/"))
        query = self._common_params()
        query.update(params or {})
        full_url = url + "?" + urllib.parse.urlencode(query)

        attempt = 0
        while True:
            attempt += 1
            self._respect_backoff()
            if self.request_delay:
                time.sleep(self.request_delay)

            req = urllib.request.Request(
                full_url,
                headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.request_count += 1
                    wrapper = json.loads(self._read_body(resp))
                    self._absorb_wrapper(wrapper)
                    return wrapper

            except urllib.error.HTTPError as err:
                try:
                    body = self._read_body(err)
                    wrapper = json.loads(body)
                except Exception:
                    wrapper = {}
                error_id = wrapper.get("error_id")
                error_name = wrapper.get("error_name", "")
                # Absorb any backoff/quota the error carried.
                self._absorb_wrapper(wrapper)

                # Throttle violations: 502 (throttle) / 429 (too many requests).
                if err.code == 429 or error_id in (429, 502):
                    wait = min(30 * attempt, 300)
                    log("SE throttled (%s / HTTP %d): sleeping %.0fs"
                        % (error_name or "throttle", err.code, wait))
                    time.sleep(wait)
                    continue

                if 500 <= err.code < 600 and attempt <= self.max_retries:
                    backoff = min(2 ** attempt + random.uniform(0, 1), 120)
                    log("SE HTTP %d: retry %d/%d in %.1fs"
                        % (err.code, attempt, self.max_retries, backoff))
                    time.sleep(backoff)
                    continue

                raise RuntimeError("SE HTTP %d (%s): %s"
                                   % (err.code, error_name, str(wrapper)[:300]))

            except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
                if attempt <= self.max_retries:
                    backoff = min(2 ** attempt + random.uniform(0, 1), 120)
                    log("network error (%s): retry %d/%d in %.1fs"
                        % (err, attempt, self.max_retries, backoff))
                    time.sleep(backoff)
                    continue
                raise

    def _absorb_wrapper(self, wrapper):
        if not isinstance(wrapper, dict):
            return
        if "backoff" in wrapper and wrapper["backoff"]:
            # Mandatory wait before the next request of this method.
            self._next_allowed_at = time.time() + float(wrapper["backoff"]) + 0.5
        if "quota_remaining" in wrapper:
            self.quota_remaining = wrapper["quota_remaining"]

    def paginate(self, path, params):
        """Yield items across pages for one query, respecting has_more, quota,
        and the 25k deep-paging cap. Items are returned verbatim."""
        page = 1
        params = dict(params)
        params["pagesize"] = self.pagesize
        while True:
            if (self.quota_remaining is not None
                    and self.quota_remaining <= self.min_quota_remaining):
                raise QuotaExhausted("quota_remaining=%s" % self.quota_remaining)

            params["page"] = page
            wrapper = self.get(path, params)
            for item in wrapper.get("items", []):
                yield item

            if not wrapper.get("has_more"):
                return
            if page * self.pagesize >= 25000:
                log("WARNING: hit the 25,000-result deep-paging cap on '%s' "
                    "(page=%d). Narrow window_slice_days or the tag to avoid "
                    "dropping data." % (path, page))
                return
            page += 1


# --------------------------------------------------------------------------- #
# checkpoint
# --------------------------------------------------------------------------- #

def checkpoint_path(output_dir, tag):
    return os.path.join(output_dir, "_checkpoints", "tag__%s.json" % slugify_tag(tag))


def load_checkpoint(output_dir, tag):
    path = checkpoint_path(output_dir, tag)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (ValueError, OSError):
            pass
    return None


def save_checkpoint(output_dir, tag, cp):
    cp["updated_at"] = iso(now_utc())
    path = checkpoint_path(output_dir, tag)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, cp)


# --------------------------------------------------------------------------- #
# date slicing (keeps each query under the deep-paging cap)
# --------------------------------------------------------------------------- #

def date_slices(window_start, window_end, slice_days):
    slices = []
    cur = window_start
    step = timedelta(days=slice_days)
    while cur < window_end:
        nxt = min(cur + step, window_end)
        slices.append((unix(cur), unix(nxt)))
        cur = nxt
    return slices


# --------------------------------------------------------------------------- #
# harvest
# --------------------------------------------------------------------------- #

def question_record_path(questions_dir, qid):
    return os.path.join(questions_dir, "%010d.json" % qid)


def harvest_tag(client, tag, cfg, dry_run=False):
    output_dir = cfg["output_dir"]
    questions_dir = os.path.join(output_dir, "questions")
    if not dry_run:
        os.makedirs(questions_dir, exist_ok=True)

    window_end = now_utc()
    window_start = window_end - timedelta(days=cfg["window_days"])
    date_field = cfg["date_field"]
    sort = "activity" if date_field == "activity" else "creation"

    window = {
        "window_days": cfg["window_days"],
        "date_field": date_field,
        "window_start": iso(window_start),
        "window_end": iso(window_end),
        "min_answers": cfg["min_answers"],
        "accepted_only": cfg["accepted_only"],
    }

    cp = load_checkpoint(output_dir, tag) or {
        "tag": tag,
        "site": cfg["site"],
        "started_at": iso(now_utc()),
        "status": "in_progress",
    }
    if cp.get("window") != window:
        cp["window"] = window
        cp["status"] = "in_progress"
        cp["slices_done"] = []
    cp.setdefault("slices_done", [])
    cp.setdefault("questions_seen", 0)
    cp.setdefault("questions_stored", 0)
    cp.setdefault("questions_already_on_disk", 0)

    slices = date_slices(window_start, window_end, cfg["window_slice_days"])
    log("=== tag:%s === (%d slices, %s window %s..%s)"
        % (tag, len(slices), date_field, window["window_start"], window["window_end"]))

    search_params = {
        "tagged": tag,
        "sort": sort,
        "order": "desc",
        "filter": "withbody",
        "answers": cfg["min_answers"],
    }
    if cfg["accepted_only"]:
        search_params["accepted"] = "True"

    for (slice_min, slice_max) in slices:
        slice_key = "%d-%d" % (slice_min, slice_max)
        if slice_key in cp["slices_done"]:
            continue

        params = dict(search_params)
        params["min"] = slice_min
        params["max"] = slice_max

        try:
            for question in client.paginate("search/advanced", params):
                cp["questions_seen"] += 1
                qid = question.get("question_id")
                if qid is None:
                    continue

                record_path = question_record_path(questions_dir, qid)
                if os.path.exists(record_path):
                    cp["questions_already_on_disk"] += 1
                    continue

                if dry_run:
                    cp["questions_stored"] += 1
                    continue

                _fetch_and_store_question(client, question, record_path, tag, cfg)
                cp["questions_stored"] += 1

                if cp["questions_stored"] % 25 == 0:
                    save_checkpoint(output_dir, tag, cp)
                    log("  progress tag:%s stored=%d seen=%d (on-disk=%d) quota=%s"
                        % (tag, cp["questions_stored"], cp["questions_seen"],
                           cp["questions_already_on_disk"], client.quota_remaining))

        except QuotaExhausted as exc:
            log("quota exhausted (%s) mid-tag:%s — checkpoint saved, re-run "
                "after quota resets to resume." % (exc, tag))
            if not dry_run:
                save_checkpoint(output_dir, tag, cp)
            raise

        cp["slices_done"].append(slice_key)
        if not dry_run:
            save_checkpoint(output_dir, tag, cp)

    cp["status"] = "done"
    if not dry_run:
        save_checkpoint(output_dir, tag, cp)
    log("done tag:%s stored=%d seen=%d (already-on-disk=%d) quota=%s"
        % (tag, cp["questions_stored"], cp["questions_seen"],
           cp["questions_already_on_disk"], client.quota_remaining))


def _fetch_and_store_question(client, question, record_path, surfaced_by_tag, cfg):
    """Fetch full answers, comments, and timeline for one question and write the
    raw record. The question object from search/advanced already carries the
    body (filter=withbody) and is stored verbatim."""
    qid = question["question_id"]

    answers = list(client.paginate(
        "questions/%d/answers" % qid,
        {"sort": "activity", "order": "desc", "filter": "withbody"},
    ))
    question_comments = list(client.paginate(
        "questions/%d/comments" % qid,
        {"sort": "creation", "order": "asc", "filter": "withbody"},
    ))

    # Answer comments, fetched by vectorized answer ids (up to 100 per call).
    answer_comments = []
    answer_ids = [a["answer_id"] for a in answers if a.get("answer_id") is not None]
    for i in range(0, len(answer_ids), 100):
        batch = answer_ids[i:i + 100]
        ids = ";".join(str(x) for x in batch)
        answer_comments.extend(client.paginate(
            "answers/%s/comments" % ids,
            {"sort": "creation", "order": "asc", "filter": "withbody"},
        ))

    # SE question timeline — the parallel to the GitHub issue timeline.
    timeline = list(client.paginate(
        "questions/%d/timeline" % qid,
        {"filter": "withbody"},
    ))

    record = {
        "_meta": {
            "harvester_version": HARVESTER_VERSION,
            "source": "stackexchange",
            "site": cfg["site"],
            "question_id": qid,
            "surfaced_by_tag": surfaced_by_tag,
            "fetched_at": iso(now_utc()),
            "source_urls": {
                "question_link": question.get("link"),
                "answers_api": "%s/questions/%d/answers" % (API_ROOT, qid),
                "comments_api": "%s/questions/%d/comments" % (API_ROOT, qid),
                "timeline_api": "%s/questions/%d/timeline" % (API_ROOT, qid),
            },
            "counts": {
                "answers": len(answers),
                "question_comments": len(question_comments),
                "answer_comments": len(answer_comments),
                "timeline_events": len(timeline),
            },
        },
        "question": question,
        "answers": answers,
        "question_comments": question_comments,
        "answer_comments": answer_comments,
        "timeline": timeline,
    }
    atomic_write_json(record_path, record)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv=None):
    parser = argparse.ArgumentParser(description="Raw Stack Overflow harvester (Hindsight)")
    parser.add_argument("--config", default="config_stackoverflow.json", help="path to config JSON")
    parser.add_argument("--tag", action="append", default=None,
                        help="override tag list (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="count questions in-window without fetching per-question data")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    if args.tag:
        cfg["tags"] = args.tag

    tags = cfg.get("tags") or []
    if not tags:
        sys.exit("No tags configured. Add some to 'tags' in %s (or pass --tag)." % args.config)

    app_key = os.environ.get("STACK_APP_KEY")
    access_token = os.environ.get("STACK_ACCESS_TOKEN")
    if not app_key:
        log("WARNING: no STACK_APP_KEY set — quota is only 300 requests/day. "
            "Register a free app key for 10,000/day.")

    client = StackExchangeClient(
        site=cfg["site"],
        app_key=app_key,
        access_token=access_token,
        pagesize=cfg["pagesize"],
        request_delay=cfg["request_delay_seconds"],
        min_quota_remaining=cfg["min_quota_remaining"],
        max_retries=cfg["max_retries"],
    )

    log("harvesting %d tag(s) from %s -> %s%s"
        % (len(tags), cfg["site"], cfg["output_dir"], " [DRY RUN]" if args.dry_run else ""))

    started = time.time()
    for tag in tags:
        try:
            harvest_tag(client, tag, cfg, dry_run=args.dry_run)
        except KeyboardInterrupt:
            log("interrupted by user; checkpoint is saved. Re-run to resume.")
            raise
        except QuotaExhausted:
            log("stopping: daily quota exhausted. Re-run after reset to resume "
                "(remaining tags and slices are checkpointed).")
            break
        except Exception as err:
            log("ERROR on tag:%s: %s" % (tag, err))

    log("all tags processed in %.0fs; %d API requests made; quota_remaining=%s."
        % (time.time() - started, client.request_count, client.quota_remaining))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
