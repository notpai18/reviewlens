"""Smoke test for production/staging ReviewLens service.

Hits /health, /ready, and one /ask endpoint to verify end-to-end functionality.
Usage:
    python scripts/smoke_prod.py <url>
Example:
    python scripts/smoke_prod.py http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def smoke_test(base_url: str) -> int:
    base_url = base_url.rstrip("/")
    print(f"=== Smoke Testing ReviewLens at {base_url} ===")

    # 1. Test /health
    health_url = f"{base_url}/health"
    print(f"1. Checking {health_url}...")
    try:
        req = urllib.request.Request(health_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if status != 200 or data.get("status") != "ok":
                print(f"  FAIL: Expected status 200 and {{'status': 'ok'}}, got {status}: {body}", file=sys.stderr)
                return 1
            print(f"  PASS: /health returned {status} -> {body.strip()}")
    except Exception as exc:
        print(f"  FAIL: Could not reach /health: {exc}", file=sys.stderr)
        return 1

    # 2. Test /ready
    ready_url = f"{base_url}/ready"
    print(f"2. Checking {ready_url}...")
    try:
        req = urllib.request.Request(ready_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if status != 200 or data.get("status") != "ready":
                print(f"  FAIL: Expected status 200 and {{'status': 'ready'}}, got {status}: {body}", file=sys.stderr)
                return 1
            print(f"  PASS: /ready returned {status} -> {body.strip()}")
    except Exception as exc:
        print(f"  FAIL: Could not reach /ready: {exc}", file=sys.stderr)
        return 1

    # 3. Test /ask
    ask_url = f"{base_url}/ask"
    question = "How many reviews are there for each game?"
    print(f"3. Checking {ask_url} with question: '{question}'...")
    try:
        payload = json.dumps({"question": question}).encode("utf-8")
        req = urllib.request.Request(
            ask_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            if status != 200 or "answer" not in data:
                print(f"  FAIL: Expected status 200 with 'answer' field, got {status}: {body[:200]}", file=sys.stderr)
                return 1
            ans = data.get("answer_markdown") or str(data.get("answer", ""))
            print(f"  PASS: /ask returned {status}")
            print(f"  Answer preview: {ans[:150]}...")
    except Exception as exc:
        print(f"  FAIL: /ask failed: {exc}", file=sys.stderr)
        return 1

    print("\nALL SMOKE CHECKS PASSED SUCCESSFULLY! Service is healthy and functional.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Production smoke test")
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8000", help="Base URL of service")
    args = parser.parse_args()

    sys.exit(smoke_test(args.url))


if __name__ == "__main__":
    main()
