"""Real isolated Compose restart and authenticated collector acceptance (mutates test state)."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
from configure_local import read_env
from smoke import request, verify


def run(args):
    config = read_env(args.env_file)
    assert config["COMPOSE_PROJECT_NAME"] == args.project
    dc = ["docker", "compose", "--project-name", args.project, "--env-file", args.env_file, "-f", "compose.yaml", "--profile", "monitoring"]
    password = Path(args.password_file).read_text(encoding="utf-8")
    base = args.url.rstrip("/")
    def login():
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, body, _ = request(opener, base + "/api/v1/auth/login", {"login": config["APP_LOGIN"], "password": password}, {"Origin": base})
        assert status == 200
        return opener, {"Origin": base, "X-CSRF-Token": json.loads(body)["csrf_token"]}
    opener, headers = login()
    status, raw, response_headers = request(opener, base + "/api/v1/model/settings")
    before = json.loads(raw)
    assert status == 200
    changed_horizon = 29 if before["horizon_days"] != 29 else 28
    assert request(opener, base + "/api/v1/model/settings", {"horizon_days": changed_horizon}, {"Origin": base, "If-Match": next(value for key, value in response_headers.items() if key.lower() == "etag")}, "PATCH")[0] == 403
    status, raw, _ = request(opener, base + "/api/v1/model/settings", {"horizon_days": changed_horizon}, {**headers, "If-Match": next(value for key, value in response_headers.items() if key.lower() == "etag")}, "PATCH")
    assert status == 200
    settings_revision = json.loads(raw)["settings_revision"]
    seed = json.loads(Path("data/seed.json").read_text(encoding="utf-8"))
    seed["accounts"][0]["email"] = "delivery-restart@example.test"
    status, raw, _ = request(opener, base + "/api/v1/source/snapshot", seed, headers, "PUT")
    assert status == 200
    snapshot = json.loads(raw)
    subprocess.run(dc + ["restart", "app"], check=True, stdout=subprocess.DEVNULL)
    for attempt in range(30):
        try:
            verify(base, args.revision, args.env_file, args.password_file)
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)
    opener, headers = login()
    status, raw, _ = request(opener, base + "/api/v1/model/settings")
    current = json.loads(raw)
    assert status == 200 and current["horizon_days"] == changed_horizon and current["settings_revision"] == settings_revision
    status, raw, _ = request(opener, base + "/api/v1/customers?limit=100")
    customers = json.loads(raw)
    assert customers["data_revision"] == snapshot["data_revision"]
    assert any(row["email"] == "delivery-restart@example.test" for row in customers["items"])
    status, raw, _ = request(opener, base + "/api/v1/analytics/summary")
    summary = json.loads(raw)
    expected_metrics = {
        "churn_monetary_risk_cents": summary["monetary_risk_cents"],
        "churn_settings_revision": settings_revision,
        "churn_data_revision": summary["data_revision"],
        "churn_evaluated_customers": summary["customers_evaluated"],
        "churn_due_services_price_cents": summary["due_services_price_cents"],
        "churn_funding_gap_cents": summary["funding_gap_cents"],
        "churn_risk_score_mean": summary["mean_risk_score"] or 0,
    }
    def query(expression):
        url = "http://prometheus:9090/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        code = "import urllib.request; print(urllib.request.urlopen(" + repr(url) + ").read().decode())"
        result = subprocess.check_output(dc + ["exec", "-T", "app", "python", "-c", code], text=True)
        rows = json.loads(result)["data"]["result"]
        return {tuple(sorted(row["metric"].items())): float(row["value"][1]) for row in rows}
    for attempt in range(20):
        try:
            up = query('up{job="churn-product"}')
            assert list(up.values()) == [1.0]
            for metric, expected in expected_metrics.items():
                assert list(query(metric).values()) == [float(expected)]
            for level, expected in summary["risk_counts"].items():
                assert list(query('churn_customers{risk_level="' + level + '"}').values()) == [float(expected)]
            break
        except (AssertionError, subprocess.CalledProcessError, KeyError):
            if attempt == 19:
                raise
            time.sleep(2)
    image = subprocess.check_output(dc + ["images", "-q", "app"], text=True).strip()
    report = {"environment": "local Docker Compose via Caddy", "project": args.project, "url": base,
              "revision": args.revision, "image_id": image, "restart": "passed", "settings_persisted": True,
              "snapshot_persisted": True, "settings_revision": settings_revision,
              "data_revision": snapshot["data_revision"], "csrf_rejection": 403,
              "prometheus_up": 1, "collector_authenticated": True, "collector_matches_api": True,
              "monetary_risk_cents": summary["monetary_risk_cents"], "compared_metrics": expected_metrics,
              "risk_counts": summary["risk_counts"], "browser_e2e": "not_run in B09; independent B10", "ci_vps_https": "not_run"}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Compose restart, persisted settings/snapshot, CSRF and actual authenticated collector: passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("env-file", "password-file", "project", "url", "revision", "output"):
        parser.add_argument("--" + field, required=True)
    run(parser.parse_args())
