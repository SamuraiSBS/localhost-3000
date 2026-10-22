"""Check the externally observable page and, optionally, the exact release."""
import argparse
import json
import time
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--revision")
parser.add_argument("--attempts", type=int, default=15)
args = parser.parse_args()
for attempt in range(args.attempts):
    try:
        with urllib.request.urlopen(args.url.rstrip("/") + "/healthz", timeout=5) as response:
            health = json.load(response)
        assert health["status"] == "ok", "Unhealthy application"
        if args.revision:
            assert health["revision"] == args.revision, "Unexpected release revision"
        with urllib.request.urlopen(args.url, timeout=5) as response:
            assert "Lazyum Autumn Hack — Infrastructure Ready" in response.read().decode(), "Wrong page"
        print(f"Smoke passed: {args.url}; revision={health['revision']}")
        break
    except Exception as error:
        if attempt == args.attempts - 1:
            raise SystemExit(f"Smoke failed: {error}") from error
        time.sleep(2)
