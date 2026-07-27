from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services.pipeline import (
    CompatibilityProfileError,
    DocumentPipeline,
    _compatibility_input_fingerprint,
)


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "public_synthetic"
)


def _files() -> dict[str, Path]:
    return {
        "ghe_spreadsheet": FIXTURE_DIR / "ghe_sinteticos.xlsx",
        "psychosocial_report": FIXTURE_DIR / "psicossocial_sintetico.docx",
        "ergo_report": FIXTURE_DIR / "ergo_sintetico.doc",
        "integrated_report": (
            FIXTURE_DIR / "tecnico_integrado_sintetico.docx"
        ),
        "cnpj_card": FIXTURE_DIR / "cartao_cnpj_sintetico.png",
    }


def _settings(
    tmp_path: Path, profile_path: Path | None
) -> Settings:
    return Settings(
        base_dir=tmp_path,
        host="127.0.0.1",
        port=8000,
        max_file_bytes=25 * 1024 * 1024,
        job_ttl_minutes=1,
        template_path=tmp_path / "private_templates" / "template.docx",
        template_manifest_path=(
            tmp_path / "private_templates" / "template.manifest.json"
        ),
        render_on_generate=False,
        libreoffice_path=None,
        uploads_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
        outputs_dir=tmp_path / "outputs",
        compatibility_profile_path=profile_path,
    )


def _payload() -> dict[str, object]:
    return {
        "company_name": "Empresa Sintética — Sem Validade",
        "competence": "2026-07",
        "ergo_reference_date": "2026-07-01",
        "psychosocial_reference_date": "2026-07-02",
        "analysis_mode": "integrated",
        "compatibility_mode": True,
        "compatibility_acknowledged": True,
    }


def test_compatibility_requires_a_private_configured_profile(
    tmp_path: Path,
) -> None:
    pipeline = DocumentPipeline(settings=_settings(tmp_path, None))

    with pytest.raises(CompatibilityProfileError) as captured:
        pipeline.build_model(_files(), _payload())

    assert captured.value.code == "compatibility_profile_unavailable"


def test_compatibility_applies_only_to_the_exact_profile_fingerprint(
    tmp_path: Path,
) -> None:
    private_dir = tmp_path / "private_templates"
    private_dir.mkdir()
    profile_path = private_dir / "pilot-profile.json"
    fingerprint = _compatibility_input_fingerprint(_files(), "integrated")
    profile_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "pilot_reference",
                "analysis_mode": "integrated",
                "input_fingerprint": fingerprint,
                "included_ergo_ordinals": [1, 2, 3],
                "omitted_ergo_ordinals": [4],
            }
        ),
        encoding="utf-8",
    )
    pipeline = DocumentPipeline(
        settings=_settings(tmp_path, profile_path)
    )

    model = pipeline.build_model(_files(), _payload())

    assert model.document.compatibility is not None
    assert model.document.compatibility.included_ergo_source_ids == [
        block.source_id for block in model.ergo.blocks[:3]
    ]
    assert model.document.compatibility.omitted_ergo_source_ids == [
        model.ergo.blocks[3].source_id
    ]
    assert model.document.compatibility.acknowledged is True

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["input_fingerprint"] = "0" * 64
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(CompatibilityProfileError) as captured:
        pipeline.build_model(_files(), _payload())
    assert captured.value.code == "compatibility_profile_mismatch"
