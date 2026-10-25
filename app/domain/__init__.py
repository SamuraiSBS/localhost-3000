"""Pure, deterministic churn-analysis rules.

The domain package has no database, web, clock-global, or billing side effects.
Callers pass one evaluation time and immutable source DTOs into each calculation.
"""

from app.domain.context import AccountContext, ServiceContext, build_account_context
from app.domain.features import DEFAULT_FEATURE_REGISTRY, FeatureDefinition, FeatureRegistry
from app.domain.scoring import CustomerScore, ServiceScore, score_customer

__all__ = [
    "AccountContext",
    "CustomerScore",
    "DEFAULT_FEATURE_REGISTRY",
    "FeatureDefinition",
    "FeatureRegistry",
    "ServiceContext",
    "ServiceScore",
    "build_account_context",
    "score_customer",
]
