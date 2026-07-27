"""Explicit reconciliation between source Ergo blocks and official GHEs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    CompatibilityException,
    ErgoBlock,
    GHE,
    ReconciliationCandidate,
    ReconciliationItem,
    ReconciliationPlan,
    ReconciliationStatus,
)

from .normalization import (
    canonical_ghe_code,
    ghe_name_similarity,
    normalize_ghe_name,
)


class ReconciliationDecision(BaseModel):
    """A user-reviewed mapping decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_id: str
    official_ghe_code: str | None = None
    not_applicable: bool = False
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target(self) -> "ReconciliationDecision":
        if self.not_applicable == bool(self.official_ghe_code):
            raise ValueError(
                "choose exactly one: an official GHE or not applicable"
            )
        return self


def _candidate(block: ErgoBlock, official: GHE) -> ReconciliationCandidate | None:
    source_code = canonical_ghe_code(block.source_code)
    code_equal = bool(source_code and source_code == official.canonical_code)
    similarity = ghe_name_similarity(block.source_name, official.name)
    reasons: list[str] = []
    score = 0.0
    if code_equal:
        reasons.append("same_code")
        score += 0.55
    if similarity == 1.0:
        reasons.append("same_name")
        score += 0.45
    elif similarity >= 0.65:
        reasons.append("similar_name")
        score += 0.35 * similarity
    if score < 0.3:
        return None
    return ReconciliationCandidate(
        official_ghe_code=official.canonical_code,
        official_ghe_name=official.name,
        score=round(min(score, 1.0), 4),
        reasons=reasons,
    )


def build_reconciliation_plan(
    ergo_blocks: Sequence[ErgoBlock], official_ghes: Sequence[GHE]
) -> ReconciliationPlan:
    """Suggest matches; only unambiguous code+name matches are automatic."""

    official_codes = [ghe.canonical_code for ghe in official_ghes]
    if len(official_codes) != len(set(official_codes)):
        raise ValueError("official GHE codes must be unique")
    items: list[ReconciliationItem] = []
    warnings: list[str] = []
    for block in ergo_blocks:
        candidates = [
            candidate
            for official in official_ghes
            if (candidate := _candidate(block, official)) is not None
        ]
        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        source_code = canonical_ghe_code(block.source_code)
        source_name = normalize_ghe_name(block.source_name)
        exact = [
            candidate
            for candidate in candidates
            if "same_code" in candidate.reasons
            and (
                "same_name" in candidate.reasons
                or not source_name
            )
        ]
        if len(exact) == 1:
            status = ReconciliationStatus.AUTO_MATCHED
            target = exact[0].official_ghe_code
            reason = "Correspondência exata de código e nome da fonte."
        else:
            status = ReconciliationStatus.NEEDS_REVIEW
            target = None
            reason = None
            if not candidates:
                warnings.append(
                    f"{block.source_id}: nenhum GHE oficial candidato foi encontrado."
                )
            elif source_code and candidates[0].official_ghe_code != source_code:
                warnings.append(
                    f"{block.source_id}: o nome sugere um código oficial diferente."
                )
            else:
                warnings.append(
                    f"{block.source_id}: a correspondência exige revisão explícita."
                )
        items.append(
            ReconciliationItem(
                source_id=block.source_id,
                source_code=block.source_code,
                source_name=block.source_name,
                candidates=candidates,
                status=status,
                official_ghe_code=target,
                decision_reason=reason,
            )
        )
    return ReconciliationPlan(items=items, warnings=warnings)


def apply_reconciliation_decisions(
    plan: ReconciliationPlan,
    decisions: Sequence[ReconciliationDecision | dict[str, object]],
    official_ghes: Sequence[GHE],
) -> ReconciliationPlan:
    """Apply reviewed decisions to a copy and retain their audit trail."""

    result = plan.model_copy(deep=True)
    by_source = {item.source_id: item for item in result.items}
    official_codes = {ghe.canonical_code for ghe in official_ghes}
    seen_decisions: set[str] = set()
    for raw_decision in decisions:
        decision = (
            raw_decision
            if isinstance(raw_decision, ReconciliationDecision)
            else ReconciliationDecision.model_validate(raw_decision)
        )
        if decision.source_id in seen_decisions:
            raise ValueError("duplicate reconciliation decision")
        seen_decisions.add(decision.source_id)
        item = by_source.get(decision.source_id)
        if item is None:
            raise ValueError("reconciliation decision references an unknown source")
        if decision.not_applicable:
            item.status = ReconciliationStatus.NEEDS_REVIEW
            item.official_ghe_code = None
            item.decision_reason = decision.reason
            item.status = ReconciliationStatus.NOT_APPLICABLE
        else:
            target = canonical_ghe_code(decision.official_ghe_code)
            if target not in official_codes:
                raise ValueError("reconciliation target is not an official GHE")
            item.official_ghe_code = target
            item.decision_reason = decision.reason
            item.status = ReconciliationStatus.CONFIRMED
        item.decided_at = datetime.now(UTC)

    assignments: dict[str, list[str]] = {}
    for item in result.items:
        if item.official_ghe_code:
            assignments.setdefault(item.official_ghe_code, []).append(item.source_id)
    result.warnings = [
        warning
        for warning in result.warnings
        if not any(source_id in warning for source_id in seen_decisions)
    ]
    for official_code, sources in assignments.items():
        if len(sources) > 1:
            result.warnings.append(
                f"{official_code}: mais de um bloco Ergo foi associado; revisar decisão."
            )
    return result


def select_ergo_blocks_for_generation(
    blocks: Sequence[ErgoBlock],
    plan: ReconciliationPlan,
    compatibility: CompatibilityException | None = None,
) -> list[ErgoBlock]:
    """Return only explicitly reconciled blocks, with a traced compatibility override."""

    if not plan.complete:
        raise ValueError("all Ergo reconciliation items must be reviewed")
    items = {item.source_id: item for item in plan.items}
    source_blocks = {block.source_id: block for block in blocks}
    if set(items) != set(source_blocks):
        raise ValueError("reconciliation plan and Ergo block set differ")

    selected_ids = {
        source_id
        for source_id, item in items.items()
        if item.status != ReconciliationStatus.NOT_APPLICABLE
    }
    if compatibility is not None:
        if not compatibility.acknowledged:
            raise ValueError("compatibility exception must be acknowledged")
        known = set(source_blocks)
        referenced = set(compatibility.included_ergo_source_ids) | set(
            compatibility.omitted_ergo_source_ids
        )
        if not referenced <= known:
            raise ValueError("compatibility exception references an unknown block")
        selected_ids |= set(compatibility.included_ergo_source_ids)
        selected_ids -= set(compatibility.omitted_ergo_source_ids)

    return [block for block in blocks if block.source_id in selected_ids]
