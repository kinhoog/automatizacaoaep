from __future__ import annotations

from pathlib import Path

from app.models import CompanyData, ReconciliationStatus
from app.services.reconciliation import (
    apply_reconciliation_decisions,
    build_reconciliation_plan,
    select_ergo_blocks_for_generation,
)
from app.services.validation import (
    validate_normalized_aep,
    validate_upload,
)


def test_reconciliation_never_drops_unmatched_ergo_silently(
    ghe_result,
    ergo_report,
) -> None:
    plan = build_reconciliation_plan(ergo_report.blocks, ghe_result.ghes)
    assert len(plan.items) == len(ergo_report.blocks) == 4
    assert [
        item.status for item in plan.items[:3]
    ] == [ReconciliationStatus.AUTO_MATCHED] * 3

    extra = plan.items[3]
    assert extra.source_code == "GHE 99"
    assert extra.status == ReconciliationStatus.NEEDS_REVIEW
    assert extra.official_ghe_code is None
    assert not plan.complete

    reviewed = apply_reconciliation_decisions(
        plan,
        [
            {
                "source_id": extra.source_id,
                "not_applicable": True,
                "reason": "Sem correspondente na fonte oficial sintética.",
            }
        ],
        ghe_result.ghes,
    )
    assert reviewed.complete
    assert reviewed.items[3].status == ReconciliationStatus.NOT_APPLICABLE
    assert reviewed.items[3].decision_reason
    assert reviewed.items[3].decided_at is not None

    selected = select_ergo_blocks_for_generation(
        ergo_report.blocks,
        reviewed,
    )
    assert [block.source_code for block in selected] == [
        "GHE 10",
        "GHE 20",
        "GHE 30",
    ]


def test_absent_logo_is_valid(normalized_model) -> None:
    assert normalized_model.company.logo is None
    report = validate_normalized_aep(normalized_model)
    assert report.valid
    assert not any(issue.field == "company.logo" for issue in report.issues)


def test_registration_card_is_required(normalized_model) -> None:
    without_card = normalized_model.model_copy(deep=True)
    without_card.company = CompanyData(
        legal_name=without_card.company.legal_name,
        registration_card=None,
        logo=None,
    )

    report = validate_normalized_aep(without_card)
    assert not report.valid
    assert "registration_card_missing" in {
        issue.code for issue in report.errors
    }


def test_invalid_real_format_is_rejected(
    public_fixtures: Path,
) -> None:
    metadata, report = validate_upload(
        public_fixtures / "ergo_sintetico.doc",
        role="technical_integrated",
    )

    assert metadata is not None
    assert not report.valid
    assert {
        "extension_not_allowed",
        "real_type_not_allowed",
    }.issubset({issue.code for issue in report.errors})


def test_optional_logo_upload_may_be_absent() -> None:
    metadata, report = validate_upload(None, role="logo")
    assert metadata is None
    assert report.valid
    assert report.errors == []


def test_required_registration_card_upload_may_not_be_absent() -> None:
    metadata, report = validate_upload(None, role="registration_card")
    assert metadata is None
    assert not report.valid
    assert [issue.code for issue in report.errors] == [
        "required_file_missing"
    ]
