"""Strict Decision Lab inputs and read-only, explainable preview outputs."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StrictBool, StrictInt, StrictStr, model_validator

from app.contracts import (
    AccountSource, FEATURE_IDS, ModelSettings, MoneyCents, PaymentTerm, ScoreNumber,
    ServiceSource, ServiceStatus, SourceId, SourceSnapshot, ThresholdsPatch, Timestamp,
)

Number = Annotated[Decimal, PlainSerializer(
    lambda value: int(value) if value == value.to_integral_value() else float(value),
    return_type=int | float, when_used="json",
)]


class LabDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, from_attributes=True)


class RevisionTuple(LabDTO):
    analysis_revision: StrictStr = Field(pattern=r"^a-[1-9][0-9]*$")
    data_revision: StrictInt = Field(ge=1)
    settings_revision: StrictInt = Field(ge=1)


class SourcePreviewRequest(LabDTO):
    expected_revision: RevisionTuple
    candidate_snapshot: SourceSnapshot


class SourceConfirmRequest(LabDTO):
    expected_revision: RevisionTuple
    candidate_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class SourceRecordFieldChange(LabDTO):
    name: StrictStr
    before: Any
    after: Any
    before_present: StrictBool
    after_present: StrictBool


class SourceRecordChange(LabDTO):
    entity: Literal["account", "service", "transaction"]
    record_id: SourceId
    kind: Literal["added", "removed", "changed"]
    fields: tuple[SourceRecordFieldChange, ...]


class SourceEntityCounts(LabDTO):
    entity: Literal["account", "service", "transaction"]
    before: StrictInt
    after: StrictInt
    added: StrictInt
    removed: StrictInt
    changed: StrictInt


class SourceEvaluationSummary(LabDTO):
    customer_count: StrictInt
    scored_customer_count: StrictInt
    unknown_customer_count: StrictInt
    monetary_risk_cents: MoneyCents
    monetary_coverage_evaluated: StrictInt
    monetary_coverage_unknown: StrictInt
    due_services_price_cents: MoneyCents
    funding_gap_cents: MoneyCents
    risk_counts: tuple[tuple[StrictStr, StrictInt], ...]


class SourceCustomerResultDiff(LabDTO):
    customer_id: SourceId
    source_change: Literal["added", "removed", "present"]
    before_score: Number | None
    after_score: Number | None
    score_delta: Number | None
    before_level: StrictStr | None
    after_level: StrictStr | None
    before_money_cents: MoneyCents | None
    after_money_cents: MoneyCents | None
    money_delta_cents: StrictInt | None
    before_rank: StrictInt | None
    after_rank: StrictInt | None
    before_due_price_cents: MoneyCents | None
    after_due_price_cents: MoneyCents | None
    before_funding_gap_cents: MoneyCents | None
    after_funding_gap_cents: MoneyCents | None


class SourceSnapshotDiff(LabDTO):
    as_of: Timestamp
    entity_counts: tuple[SourceEntityCounts, ...]
    record_changes: tuple[SourceRecordChange, ...]
    before: SourceEvaluationSummary
    after: SourceEvaluationSummary
    customer_results: tuple[SourceCustomerResultDiff, ...]
    warnings: tuple[StrictStr, ...]


class SourcePreviewResponse(RevisionTuple):
    preview_id: StrictStr
    candidate_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    source_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: Timestamp
    as_of: Timestamp
    horizon_days: StrictInt
    model_version: Literal["rules-v1"]
    diff: SourceSnapshotDiff


class SourceCounts(LabDTO):
    accounts: StrictInt
    services: StrictInt
    transactions: StrictInt


class SourceWarningCount(LabDTO):
    code: StrictStr
    count: StrictInt


class SourceConfirmResponse(RevisionTuple):
    changed: StrictBool
    received_at: Timestamp | None
    checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    counts: SourceCounts
    warnings: tuple[SourceWarningCount, ...]
    preview_id: StrictStr
    preview_as_of: Timestamp
    as_of: Timestamp


class ModelOverrides(LabDTO):
    weights: dict[str, ScoreNumber] | None = None
    thresholds: ThresholdsPatch | None = None

    @model_validator(mode="after")
    def valid_overrides(self) -> Self:
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("null_setting_not_allowed")
        if self.weights is not None and not set(self.weights) <= set(FEATURE_IDS):
            raise ValueError("unknown_feature")
        return self


class ActionInput(LabDTO):
    top_up_cents: MoneyCents = 0
    enable_auto_prolong_ids: tuple[SourceId, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len(set(self.enable_auto_prolong_ids)) != len(self.enable_auto_prolong_ids):
            raise ValueError("service_not_allowed")
        return self


class PreviewRequest(LabDTO):
    expected_revision: RevisionTuple
    customer_id: SourceId
    mode: Literal["model", "action"]
    settings_overrides: ModelOverrides | None = None
    action: ActionInput | None = None

    @model_validator(mode="after")
    def separate_branches(self) -> Self:
        if self.mode == "model":
            if "action" in self.model_fields_set or self.settings_overrides is None:
                raise ValueError("model_overrides_required")
        elif "settings_overrides" in self.model_fields_set or self.action is None:
            raise ValueError("action_required")
        return self


class FeaturePreview(LabDTO):
    id: str
    version: str
    title: str
    value: Number | None
    weight: Number
    contribution: Number | None
    available: bool
    explanation: str
    evidence: dict[str, Any]


class ServicePreview(LabDTO):
    service_id: int
    input: ServiceSource
    eligible: bool
    in_horizon: bool
    overdue: bool
    excluded_reason: str | None
    covered_cents: int | None
    funding_gap_cents: int | None
    raw_score: Number | None
    risk_score: Number | None
    risk_level: str
    score_clamped: bool
    monetary_risk_cents: int | None
    features: tuple[FeaturePreview, ...]
    top_reasons: tuple[FeaturePreview, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]


class CustomerPreview(LabDTO):
    customer_id: int
    input: AccountSource
    risk_score: Number | None
    risk_level: str
    monetary_risk_cents: int | None
    spendable_cents: int
    due_services_price_cents: int
    covered_cents: int
    funding_gap_cents: int
    services: tuple[ServicePreview, ...]
    top_service_id: int | None
    top_reasons: tuple[FeaturePreview, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]
    coverage_evaluated: int
    coverage_unknown: int


class RenewalPeriodAssumption(LabDTO):
    service_id: SourceId
    payment_term: PaymentTerm


class RenewalCalendarRequest(LabDTO):
    expected_revision: RevisionTuple
    customer_id: SourceId
    horizon_days: StrictInt | None = Field(default=None, ge=1, le=90)
    max_events: StrictInt = Field(default=1000, ge=1, le=1000)
    period_assumptions: tuple[RenewalPeriodAssumption, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def unique_period_assumptions(self) -> Self:
        ids = [item.service_id for item in self.period_assumptions]
        if len(set(ids)) != len(ids):
            raise ValueError("service_not_allowed")
        if "horizon_days" in self.model_fields_set and self.horizon_days is None:
            raise ValueError("null_horizon_not_allowed")
        return self


class RenewalCalendarEvent(LabDTO):
    service_id: SourceId
    occurrence_index: StrictInt = Field(ge=0)
    due_at: Timestamp
    price_cents: MoneyCents
    covered_cents: MoneyCents
    funding_gap_cents: MoneyCents
    remaining_cents: MoneyCents


class RenewalCalendarExclusion(LabDTO):
    service_id: SourceId
    reason: StrictStr


class RenewalCalendarScenario(LabDTO):
    calendar_version: Literal["renewal-calendar-v1"]
    status: Literal["ok", "insufficient_data"]
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    horizon_end: Timestamp
    spendable_cents: MoneyCents
    events: tuple[RenewalCalendarEvent, ...] = Field(max_length=1000)
    exclusions: tuple[RenewalCalendarExclusion, ...]
    period_assumptions: tuple[RenewalPeriodAssumption, ...]
    assumptions: tuple[StrictStr, ...]
    max_events: StrictInt = Field(ge=1, le=1000)
    truncated: StrictBool
    total_price_cents: MoneyCents
    total_covered_cents: MoneyCents
    funding_gap_cents: MoneyCents
    remaining_cents: MoneyCents
    first_shortfall_at: Timestamp | None


class RenewalCalendarResponse(RevisionTuple):
    source_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    model_version: StrictStr
    hypothetical: Literal[True] = True
    baseline: CustomerPreview
    scenario: RenewalCalendarScenario


class FeatureDelta(LabDTO):
    service_id: int
    feature_id: str
    baseline_value: Number | None
    scenario_value: Number | None
    baseline_weight: Number
    scenario_weight: Number
    baseline_contribution: Number | None
    scenario_contribution: Number | None
    explanation: str


class PreviewDelta(LabDTO):
    label: str = "изменение модельного индекса"
    risk_score: Number | None
    monetary_risk_cents: int | None
    funding_gap_cents: int
    category_changed: bool
    features: tuple[FeatureDelta, ...]
    remaining_reasons: tuple[FeaturePreview, ...]


class PreviewResult(LabDTO):
    mode: Literal["model", "action"]
    hypothetical: bool
    as_of: Timestamp
    horizon_days: int
    model_version: str
    baseline: CustomerPreview
    model_scenario: CustomerPreview | None = None
    action_scenario: CustomerPreview | None = None
    effective_settings: ModelSettings
    effective_overrides: ModelOverrides | None = None
    effective_action: ActionInput | None = None
    delta: PreviewDelta


class WorklistRequest(LabDTO):
    expected_revision: RevisionTuple
    limit: StrictInt = Field(default=10, ge=1, le=50)


WorklistReason = Literal["funding_gap", "manual_renewal"]
NonnegativeCount = Annotated[StrictInt, Field(ge=0)]


class WorklistServiceResult(LabDTO):
    service_id: SourceId
    expires_at: Timestamp
    overdue: StrictBool
    status: ServiceStatus
    price_cents: MoneyCents
    funding_gap_cents: MoneyCents | None
    reason_codes: tuple[WorklistReason, ...] = Field(min_length=1)
    suggested_actions: tuple[Literal["review_funding_gap", "review_manual_renewal"], ...]
    warnings: tuple[StrictStr, ...]


class WorklistRowResult(LabDTO):
    customer_id: SourceId
    risk_score: ScoreNumber
    risk_level: Literal["medium", "high"]
    monetary_risk_cents: MoneyCents | None
    due_services_price_cents: MoneyCents
    funding_gap_cents: MoneyCents
    affected_services: tuple[WorklistServiceResult, ...] = Field(min_length=1)
    reason_codes: tuple[WorklistReason, ...] = Field(min_length=1)
    warnings: tuple[StrictStr, ...]


class WorklistCoverageResult(LabDTO):
    total_customers: NonnegativeCount
    scored_customers: NonnegativeCount
    unknown_customers: NonnegativeCount
    monetary_known_customers: NonnegativeCount
    monetary_unknown_customers: NonnegativeCount


class WorklistResponse(RevisionTuple):
    source_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    model_version: Literal["rules-v1"]
    rows: tuple[WorklistRowResult, ...] = Field(max_length=50)
    matched_count: NonnegativeCount
    returned_count: NonnegativeCount
    coverage: WorklistCoverageResult
    excluded_reason_counts: dict[Literal["no_eligible_service", "no_in_horizon_service", "unknown_score",
                                         "low_risk", "no_actionable_reason"], NonnegativeCount]


class CoverageGoal(LabDTO):
    type: Literal["cover_selected"]
    service_ids: tuple[SourceId, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len(set(self.service_ids)) != len(self.service_ids):
            raise ValueError("service_not_allowed")
        return self


class ScoreGoal(LabDTO):
    type: Literal["score_below"]
    threshold: ScoreNumber = Field(gt=0, le=100)


SolverGoal = Annotated[CoverageGoal | ScoreGoal, Field(discriminator="type")]


class SolverConstraints(LabDTO):
    max_top_up_cents: StrictInt = Field(ge=0, le=10_000_000)
    allowed_enable_auto_prolong_ids: tuple[SourceId, ...] = Field(default=(), max_length=8)
    max_switches: StrictInt = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def valid_switches(self) -> Self:
        if len(set(self.allowed_enable_auto_prolong_ids)) != len(self.allowed_enable_auto_prolong_ids):
            raise ValueError("service_not_allowed")
        if self.max_switches > len(self.allowed_enable_auto_prolong_ids):
            raise ValueError("max_switches_out_of_range")
        return self


class SolveRequest(SolverConstraints):
    expected_revision: RevisionTuple
    customer_id: SourceId
    goal: SolverGoal


class SolverServiceScore(LabDTO):
    service_id: SourceId
    eligible: StrictBool
    in_horizon: StrictBool
    overdue: StrictBool
    excluded_reason: str | None
    covered_cents: MoneyCents | None
    funding_gap_cents: MoneyCents | None
    raw_score: Number | None
    risk_score: ScoreNumber | None
    risk_level: Literal["low", "medium", "high", "unknown"]
    score_clamped: StrictBool
    monetary_risk_cents: MoneyCents | None
    features: tuple[FeaturePreview, ...]
    top_reasons: tuple[FeaturePreview, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]


class SolverCustomerScore(LabDTO):
    customer_id: SourceId
    risk_score: ScoreNumber | None
    risk_level: Literal["low", "medium", "high", "unknown"]
    monetary_risk_cents: MoneyCents | None
    spendable_cents: MoneyCents
    due_services_price_cents: MoneyCents
    funding_gap_cents: MoneyCents
    services: tuple[SolverServiceScore, ...]
    top_service_id: SourceId | None
    top_reasons: tuple[FeaturePreview, ...]
    unscored_reason: str | None
    data_quality_warnings: tuple[str, ...]


class SolverSubsetMinimum(LabDTO):
    enable_auto_prolong_ids: tuple[SourceId, ...] = Field(max_length=8)
    top_up_cents: StrictInt | None = Field(ge=0, le=10_000_000)


class SolverCertificate(LabDTO):
    complete: StrictBool
    subset_minima: tuple[SolverSubsetMinimum, ...] = Field(max_length=256)
    one_cent_less_satisfies: StrictBool | None
    one_cent_less_result: SolverCustomerScore | None
    ordering: tuple[Literal["top_up_cents"], Literal["switch_count"], Literal["service_ids"]]


class SolveResponse(RevisionTuple):
    source_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    model_version: Literal["rules-v1"]
    hypothetical: Literal[True]
    effective_goal: SolverGoal
    effective_constraints: SolverConstraints
    effective_action: ActionInput | None
    delta: PreviewDelta | None
    status: Literal["found", "already_satisfied", "unreachable", "search_limit"]
    top_up_cents: StrictInt | None = Field(ge=0, le=10_000_000)
    enable_auto_prolong_ids: tuple[SourceId, ...] = Field(max_length=8)
    baseline: CustomerPreview
    result: CustomerPreview | None
    evaluations: StrictInt = Field(ge=0, le=6144)
    certificate: SolverCertificate


class CaseModelOperation(LabDTO):
    type: Literal["model"]
    settings_overrides: ModelOverrides


class CaseActionOperation(LabDTO):
    type: Literal["action"]
    action: ActionInput


class CaseSolveOperation(SolverConstraints):
    type: Literal["solve"]
    goal: SolverGoal


CaseOperation = Annotated[CaseModelOperation | CaseActionOperation | CaseSolveOperation,
                          Field(discriminator="type")]


class CaseExportRequest(LabDTO):
    expected_revision: RevisionTuple
    customer_id: SourceId
    operation: CaseOperation


class CaseDocument(LabDTO):
    """Portable synthetic inputs and derived summary; checksum excludes itself."""
    case_version: Literal[1]
    engine_version: Literal["rules-v1"]
    feature_versions: dict[StrictStr, StrictStr]
    settings: ModelSettings
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    snapshot: SourceSnapshot
    operation: CaseOperation
    expected: dict[str, Any]
    checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class CaseReplayRequest(LabDTO):
    expected_revision: RevisionTuple
    case_document: StrictStr = Field(max_length=65536, description=(
        "Untouched UTF-8 JSON file text, not a decoded object. Duplicate keys, checksum, "
        "schema, versions and the 64 KiB UTF-8 document cap are validated before calculation. "
        "The entire JSON wrapper also has a separate 64 KiB byte cap."))


class CaseReplayResponse(RevisionTuple):
    source_checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: Timestamp
    horizon_days: StrictInt = Field(ge=1, le=90)
    model_version: Literal["rules-v1"]
    sandbox: Literal[True]
    matched: Literal[True]
    checksum: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    case_as_of: Timestamp
    case_horizon_days: StrictInt = Field(ge=1, le=90)
    case_engine_version: Literal["rules-v1"]
    calculated: dict[str, Any]
