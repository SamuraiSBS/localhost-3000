"""Check product revision and access boundaries through its real HTTP proxy."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import time
import urllib.error
import urllib.request
from configure_local import read_env


def request(opener, url, data=None, headers=None, method=None):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with opener.open(req, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as response:
        return response.code, response.read(), dict(response.headers)


def verify(url, revision=None, env_file=None, password_file=None):
    base = url.rstrip("/")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    status, body, _ = request(opener, base + "/healthz")
    health = json.loads(body)
    assert status == 200 and health["status"] == "ok" and health["stage"] == "product"
    assert health["revision"] and (revision is None or health["revision"] == revision), "Unexpected release revision"
    assert request(opener, base)[0] == 200
    for path in ("/api/v1/customers", "/api/v1/openapi.json", "/api/v1/docs", "/metrics"):
        assert request(opener, base + path)[0] == 401, "Protected route exposed: " + path
    if env_file:
        config = read_env(env_file)
        if password_file is None:
            raise ValueError("Authenticated smoke requires --password-file (never a command-line password)")
        credentials = {"login": config["APP_LOGIN"], "password": Path(password_file).read_text(encoding="utf-8")}
        status, body, _ = request(opener, base + "/api/v1/auth/login", credentials, {"Origin": base})
        assert status == 200, "Login failed"
        for path in ("/api/v1/customers", "/api/v1/analytics/summary", "/api/v1/model/settings", "/api/v1/openapi.json", "/api/v1/docs"):
            assert request(opener, base + path)[0] == 200, "Authenticated route failed: " + path
        assert request(opener, base + "/metrics")[0] == 401, "Session must not grant collector access"
        assert request(opener, base + "/metrics", headers={"Authorization": "Bearer " + config["METRICS_TOKEN"]})[0] == 200
    return health


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--revision")
    parser.add_argument("--env-file")
    parser.add_argument("--password-file")
    parser.add_argument("--attempts", type=int, default=15)
    args = parser.parse_args()
    for attempt in range(args.attempts):
        try:
            health = verify(args.url, args.revision, args.env_file, args.password_file)
            print("Product smoke passed; revision=" + health["revision"] + "; authenticated=" + str(bool(args.env_file)))
            return
        except Exception as error:
            if attempt == args.attempts - 1:
                raise SystemExit("Product smoke failed: " + type(error).__name__) from None
            time.sleep(2)


if __name__ == "__main__":
    main()
