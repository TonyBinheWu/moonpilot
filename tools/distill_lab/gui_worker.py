#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys

os.environ["ORT_DISABLE_TELEMETRY"] = "1"
from drive_lab.jobs import JobStore, supervise
from drive_lab.tasks import execute

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    store = JobStore(args.work)
    job = store.get(args.job)
    if args.execute:
        execute(store.root, job["kind"], job["payload"])
    else:
        supervise(store, args.job, [sys.executable, str(Path(__file__).resolve()), "--work", str(store.root),
                                   "--job", args.job, "--execute"])
