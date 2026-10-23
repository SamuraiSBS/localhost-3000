"""Request-local Prometheus exposition over one immutable Evaluation."""

from decimal import Decimal, ROUND_HALF_UP
import hmac

from fastapi import HTTPException, Request
from prometheus_client import CollectorRegistry, Gauge, generate_latest

from app.evaluation import Evaluation


def require_metrics_token(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    expected = request.app.state.config.metrics_token or ""
    if scheme.lower() != "bearer" or not token or not expected or not hmac.compare_digest(
        token.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(401, detail={"code": "metrics_authentication_required",
                                        "message": "Collector bearer token required."},
                            headers={"WWW-Authenticate": "Bearer"})


def render_metrics(evaluation: Evaluation) -> bytes:
    # A scrape owns its registry: concurrent updates cannot mix revisions, and
    # replacing a snapshot cannot leave a previous risk level's value behind.
    registry = CollectorRegistry()
    customers = Gauge("churn_customers", "Customers by risk level", ["risk_level"], registry=registry)
    for level in ("low", "medium", "high", "unknown"):
        customers.labels(risk_level=level).set(evaluation.risk_counts[level])
    scores = [row.risk_score for row in evaluation.customers if row.risk_score is not None]
    mean = ((sum(scores, Decimal(0)) / len(scores)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if scores else Decimal(0))
    values = {
        "monetary_risk_cents": (evaluation.monetary_risk_cents, "Known monetary risk in euro cents"),
        "risk_score_mean": (mean, "Mean score of evaluated customers; zero with no evaluated customers"),
        "evaluated_customers": (evaluation.customers_evaluated, "Customers with a known score"),
        "evaluation_timestamp_seconds": (evaluation.evaluated_at.timestamp(), "Evaluation Unix timestamp"),
        "data_revision": (evaluation.data_revision, "Source snapshot revision"),
        "settings_revision": (evaluation.settings_revision, "Model settings revision"),
        "analysis_revision": (int(evaluation.analysis_revision.removeprefix("a-")), "Analysis revision"),
        "due_services_price_cents": (evaluation.due_services_price_cents, "Due services in euro cents"),
        "funding_gap_cents": (evaluation.funding_gap_cents, "Funding gap in euro cents"),
    }
    for name, (value, help_text) in values.items():
        Gauge("churn_" + name, help_text, registry=registry).set(value)
    return generate_latest(registry)
