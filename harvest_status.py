#!/usr/bin/env python3
"""
Hindsight harvester progress viewer.

Reads the on-disk checkpoints and record counts written by harvest.py and
harvest_stackoverflow.py and prints a live progress summary. Read-only — safe to
run at any time while a harvest is in flight.

    python3 harvest_status.py
    python3 harvest_status.py --data data --so-data data_stackoverflow
"""

import argparse
import glob
import json
import os
import time


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _age(path):
    try:
        secs = time.time() - os.path.getmtime(path)
    except OSError:
        return "-"
    if secs < 90:
        return "%ds ago" % int(secs)
    if secs < 5400:
        return "%dm ago" % int(secs / 60)
    return "%dh ago" % int(secs / 3600)


def github_status(data_dir):
    repos = sorted(d for d in glob.glob(os.path.join(data_dir, "*"))
                   if os.path.isdir(d) and os.path.exists(os.path.join(d, "_checkpoint.json")))
    if not repos:
        return
    print("== GitHub (%s) ==" % data_dir)
    print("  %-32s %-12s %8s %8s %10s" % ("repo", "status", "stored", "on_disk", "updated"))
    tot_stored = tot_disk = 0
    for rd in repos:
        cp = _load(os.path.join(rd, "_checkpoint.json"))
        on_disk = len(glob.glob(os.path.join(rd, "issues", "*.json")))
        stored = cp.get("issues_stored", 0)
        tot_stored += stored
        tot_disk += on_disk
        print("  %-32s %-12s %8s %8d %10s"
              % (cp.get("repo", os.path.basename(rd)), cp.get("status", "?"),
                 stored, on_disk, _age(os.path.join(rd, "_checkpoint.json"))))
    print("  %-32s %-12s %8d %8d" % ("TOTAL", "", tot_stored, tot_disk))
    print()


def stackoverflow_status(so_dir):
    cps = sorted(glob.glob(os.path.join(so_dir, "_checkpoints", "*.json")))
    disk = len(glob.glob(os.path.join(so_dir, "questions", "*.json")))
    if not cps and not disk:
        return
    print("== Stack Overflow (%s) ==" % so_dir)
    print("  %-16s %-12s %8s %8s %8s %10s" % ("tag", "status", "stored", "slices", "seen", "updated"))
    tot = 0
    for cf in cps:
        cp = _load(cf)
        slices = "%d/%s" % (len(cp.get("slices_done", [])),
                            "?" if "window" not in cp else "")
        stored = cp.get("questions_stored", 0)
        tot += stored
        print("  %-16s %-12s %8s %8s %8s %10s"
              % (cp.get("tag", os.path.basename(cf)[:-5]), cp.get("status", "?"),
                 stored, len(cp.get("slices_done", [])), cp.get("questions_seen", 0),
                 _age(cf)))
    print("  %-16s %-12s %8s (questions on disk, deduped by id: %d)" % ("TOTAL", "", tot, disk))
    print()


def main():
    ap = argparse.ArgumentParser(description="Hindsight harvester progress viewer")
    ap.add_argument("--data", default="data")
    ap.add_argument("--so-data", default="data_stackoverflow")
    args = ap.parse_args()
    printed = False
    if os.path.isdir(args.data):
        github_status(args.data)
        printed = True
    if os.path.isdir(args.so_data):
        stackoverflow_status(args.so_data)
        printed = True
    if not printed:
        print("No harvest output yet (no %s/ or %s/ directories)." % (args.data, args.so_data))


if __name__ == "__main__":
    main()
