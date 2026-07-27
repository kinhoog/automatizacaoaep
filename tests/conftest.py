from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from app.extractors.ergo_extractor import extract_ergo
from app.extractors.ghe_extractor import extract_ghes
from app.extractors.psico_extractor import extract_psychosocial
from app.extractors.technical_report_extractor import extract_technical_report
from app.models import (
    CompanyData,
    DocumentData,
    ImageAsset,
    ImageRole,
    NormalizedAEP,
)
from app.services.reconciliation import (
    apply_reconciliation_decisions,
    build_reconciliation_plan,
)
from app.services.validation import validate_normalized_aep


@pytest.fixture
def public_fixtures() -> Path:
    """Return the only directory from which versioned test inputs are read."""

    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "public_synthetic"
    )
    assert path.is_dir()
    return path


@pytest.fixture
def ghe_result(public_fixtures: Path):
    return extract_ghes(public_fixtures / "ghe_sinteticos.xlsx")


@pytest.fixture
def ergo_report(public_fixtures: Path):
    return extract_ergo(public_fixtures / "ergo_sintetico.doc")


@pytest.fixture
def psychosocial_report(public_fixtures: Path, ghe_result):
    return extract_psychosocial(
        public_fixtures / "psicossocial_sintetico.docx",
        ghe_result.ghes,
    )


@pytest.fixture
def integrated_report(public_fixtures: Path):
    return extract_technical_report(
        integrated_path=public_fixtures
        / "tecnico_integrado_sintetico.docx"
    )


def _image_asset(path: Path, image_id: str) -> ImageAsset:
    blob = path.read_bytes()
    with Image.open(path) as image:
        width, height = image.size
        image_format = image.format
    return ImageAsset(
        image_id=image_id,
        order=0,
        role=ImageRole.OTHER,
        sha256=hashlib.sha256(blob).hexdigest(),
        width_px=width,
        height_px=height,
        image_format=image_format,
        content_type=Image.MIME.get(image_format or ""),
        source_part=path.name,
        blob=blob,
        runtime_path=path,
    )


@pytest.fixture
def normalized_model(
    public_fixtures: Path,
    ghe_result,
    ergo_report,
    psychosocial_report,
    integrated_report,
) -> NormalizedAEP:
    plan = build_reconciliation_plan(ergo_report.blocks, ghe_result.ghes)
    unmatched = [
        item
        for item in plan.items
        if item.official_ghe_code is None
    ]
    assert len(unmatched) == 1
    plan = apply_reconciliation_decisions(
        plan,
        [
            {
                "source_id": unmatched[0].source_id,
                "not_applicable": True,
                "reason": "Bloco extra exclusivamente sintético.",
            }
        ],
        ghe_result.ghes,
    )
    model = NormalizedAEP(
        company=CompanyData(
            legal_name="EMPRESA SINTETICA HORIZONTE LTDA",
            registration_card=_image_asset(
                public_fixtures / "cartao_cnpj_sintetico.png",
                "registration-card-synthetic",
            ),
            logo=None,
        ),
        document=DocumentData(
            competence="JULHO/2026 - SEM VALIDADE",
            ergo_base_date="2026-07-01",
            psychosocial_base_date="2026-07-02",
        ),
        official_ghes=ghe_result.ghes,
        ergo=ergo_report,
        psychosocial=psychosocial_report,
        technical=integrated_report,
        reconciliation=plan,
    )
    model.validation = validate_normalized_aep(model)
    assert model.validation.valid
    return model
