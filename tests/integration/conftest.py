from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as web_app
from app.config import Settings
from app.services.pipeline import DocumentPipeline


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "fixtures" / "public_synthetic"
)


def _test_settings(tmp_path: Path) -> Settings:
    return Settings(
        base_dir=tmp_path,
        host="127.0.0.1",
        port=8000,
        max_file_bytes=25 * 1024 * 1024,
        job_ttl_minutes=1,
        # A ausência deliberada exercita o gerador público completo sem tocar
        # no template privado retido da instalação.
        template_path=tmp_path / "template-publico-ausente.docx",
        template_manifest_path=(
            FIXTURE_DIR / "template_manifesto_sintetico.json"
        ),
        render_on_generate=False,
        libreoffice_path=None,
        uploads_dir=tmp_path / "pipeline-uploads",
        generated_dir=tmp_path / "pipeline-generated",
        outputs_dir=tmp_path / "pipeline-outputs",
        allow_synthetic_template_fallback=True,
    )


@pytest.fixture
def api_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    runtime_root = tmp_path / "http-runtime"
    runtime_root.mkdir()
    pipeline = DocumentPipeline(settings=_test_settings(tmp_path))

    monkeypatch.setattr(web_app, "RUNTIME_ROOT", runtime_root)
    monkeypatch.setattr(web_app, "PIPELINE_INSTANCE", pipeline)
    monkeypatch.setattr(web_app, "JOB_TTL_SECONDS", 60)
    web_app.JOBS.clear()
    web_app.RUNNING_TASKS.clear()

    with TestClient(web_app.app) as client:
        yield client

    web_app.JOBS.clear()
    web_app.RUNNING_TASKS.clear()


@pytest.fixture
def valid_form_data() -> dict[str, str]:
    return {
        "company_name": "Empresa Sintética de Teste — Sem Validade",
        "competence": "2026-07",
        "ergo_reference_date": "2026-07-01",
        "psychosocial_reference_date": "2026-07-02",
        "analysis_mode": "integrated",
    }


@pytest.fixture
def valid_uploads() -> dict[str, tuple[str, bytes, str]]:
    specifications: dict[str, tuple[str, str]] = {
        "ghe_spreadsheet": (
            "ghe_sinteticos.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "psychosocial_report": (
            "psicossocial_sintetico.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "ergo_report": ("ergo_sintetico.doc", "application/msword"),
        "integrated_report": (
            "tecnico_integrado_sintetico.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "cnpj_card": ("cartao_cnpj_sintetico.png", "image/png"),
    }
    return {
        field: (name, (FIXTURE_DIR / name).read_bytes(), content_type)
        for field, (name, content_type) in specifications.items()
    }


@pytest.fixture
def validate_job(
    api_client: TestClient,
    valid_form_data: dict[str, str],
    valid_uploads: dict[str, tuple[str, bytes, str]],
) -> dict[str, Any]:
    response = api_client.post(
        "/api/validate", data=valid_form_data, files=valid_uploads
    )
    assert response.status_code == 200, response.text
    return response.json()
