#!/usr/bin/env python3
"""
replay_vllm_crash.py — reproduce a vLLM crash by replaying recorded payloads.

Usage
-----
1. Set LLM_RECORD_DIR before starting the service:

       export LLM_RECORD_DIR=/tmp/vllm_records

   Run the service under load until the crash occurs.  Every request payload
   sent to /v1/chat/completions will be written to that directory as
   ``request_00000.json``, ``request_00001.json``, …

2. Run this script against the same (or a fresh) vLLM instance:

       python scripts/replay_vllm_crash.py \\
           --record-dir /tmp/vllm_records \\
           --endpoint http://<vllm-host>:<port> \\
           --concurrency 32 \\
           [--api-key YOUR_KEY]

   The script saturates the server with exactly ``--concurrency`` concurrent
   requests (matching the vLLM batch size), cycling through all recorded
   payloads if there are fewer than 32.
"""

import argparse
import glob
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_session(concurrency: int) -> requests.Session:
    """Create a session with the same pool settings as the production service."""
    adapter = HTTPAdapter(
        pool_connections=3,
        pool_maxsize=concurrency,
        pool_block=True,
    )
    session = requests.Session()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def load_records(record_dir: str) -> list[dict]:
    """Load all ``request_*.json`` files from *record_dir*, sorted by name."""
    pattern = os.path.join(record_dir, "request_*.json")
    paths = sorted(glob.glob(pattern))
    if not paths:
        print(f"[ERROR] No request_*.json files found in {record_dir!r}", file=sys.stderr)
        sys.exit(1)
    records = []
    for p in paths:
        with open(p, encoding="utf-8") as fh:
            records.append(json.load(fh))
    print(f"[INFO]  Loaded {len(records)} recorded payloads from {record_dir!r}")
    return records


def send_request(session: requests.Session, endpoint: str, payload: dict, api_key: str | None, idx: int) -> dict:
    """Send a single chat-completions request and return a result summary dict."""
    url = f"{endpoint}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    start = time.time()
    try:
        response = session.post(url, json=payload, headers=headers)
        elapsed = time.time() - start
        status = response.status_code
        try:
            body = response.json()
        except Exception:
            body = response.text[:500]
        return {"idx": idx, "status": status, "elapsed": elapsed, "body": body, "error": None}
    except Exception as exc:
        elapsed = time.time() - start
        return {"idx": idx, "status": None, "elapsed": elapsed, "body": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay recorded vLLM payloads to reproduce a concurrency crash."
    )
    parser.add_argument(
        "--record-dir", required=True,
        help="Directory containing request_*.json files written by the service.",
    )
    parser.add_argument(
        "--endpoint",
        help=(
            "Override the vLLM base URL (e.g. http://localhost:8000).  "
            "If omitted, the endpoint stored inside each record file is used."
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, default=32,
        help="Number of concurrent requests to fire (default: 32).",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="Bearer token for the vLLM API (optional).",
    )
    args = parser.parse_args()

    records = load_records(args.record_dir)
    session = build_session(args.concurrency)

    # Build the exact batch: cycle through records until we have ``concurrency`` items
    batch: list[tuple[int, str, dict]] = []
    for i in range(args.concurrency):
        rec = records[i % len(records)]
        endpoint = args.endpoint or rec["endpoint"]
        batch.append((i, endpoint, rec["payload"]))

    print(f"[INFO]  Firing {len(batch)} concurrent requests (concurrency={args.concurrency}) …")
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(send_request, session, ep, payload, args.api_key, idx): idx
            for idx, ep, payload in batch
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Print summary
    results.sort(key=lambda r: r["idx"])
    errors = [r for r in results if r["error"] or (r["status"] and r["status"] >= 400)]
    print(f"\n[SUMMARY] {len(results)} requests sent — {len(errors)} failures\n")
    for r in errors:
        print(f"  req {r['idx']:>3}: status={r['status']}  error={r['error']}  elapsed={r['elapsed']:.2f}s")
        if r["body"]:
            body_str = json.dumps(r["body"]) if isinstance(r["body"], dict) else str(r["body"])
            print(f"           body={body_str[:300]}")
    if not errors:
        print("  All requests succeeded — crash not reproduced with this batch.")


if __name__ == "__main__":
    main()
