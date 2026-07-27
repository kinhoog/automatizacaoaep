from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document

from app.config import Settings
from app.services import pipeline
from app.services.document_renderer import LegacyConversionError
from app.services.pipeline import (
    PipelineError,
    _extract_ergo_with_legacy_conversion,
)


def _settings(tmp_path: Path) -> Settings:
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
        libreoffice_path=tmp_path / "soffice.exe",
        uploads_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
        outputs_dir=tmp_path / "outputs",
    )


def test_pipeline_converts_and_removes_binary_ergo_intermediate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "ergo.doc"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"SYNTHETIC")
    sentinel = object()
    inspected: list[Path] = []

    def fake_convert(
        input_path: Path,
        output_dir: Path,
        **kwargs,
    ) -> Path:
        assert input_path == source
        assert kwargs["libreoffice_path"] == _settings(tmp_path).libreoffice_path
        output_dir.mkdir(parents=True)
        converted = output_dir / "ergo.docx"
        document = Document()
        document.add_paragraph("RELATÓRIO SINTÉTICO SEM VALIDADE")
        document.save(converted)
        return converted

    monkeypatch.setattr(pipeline, "convert_legacy_doc_to_docx", fake_convert)
    monkeypatch.setattr(
        pipeline,
        "inspect_file",
        lambda path, name: inspected.append(path),
    )
    monkeypatch.setattr(pipeline, "extract_ergo", lambda path: sentinel)

    result = _extract_ergo_with_legacy_conversion(source, _settings(tmp_path))

    assert result is sentinel
    assert len(inspected) == 1
    assert not inspected[0].exists()
    assert not (tmp_path / "_converted_ergo").exists()


def test_pipeline_reports_missing_legacy_converter_without_internal_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "ergo.doc"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"SYNTHETIC")
    monkeypatch.setattr(
        pipeline,
        "convert_legacy_doc_to_docx",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            LegacyConversionError("LibreOffice indisponível.")
        ),
    )

    with pytest.raises(PipelineError, match="LibreOffice indisponível"):
        _extract_ergo_with_legacy_conversion(source, _settings(tmp_path))
