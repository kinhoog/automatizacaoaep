from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document

from app.services import document_renderer
from app.services.document_renderer import (
    LegacyConversionError,
    convert_legacy_doc_to_docx,
)


def test_binary_doc_conversion_uses_isolated_libreoffice_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "entrada.doc"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"sintetico")
    fake_soffice = tmp_path / "soffice.exe"
    fake_soffice.write_bytes(b"synthetic")
    output_dir = tmp_path / "convertido"
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        document_renderer,
        "find_libreoffice",
        lambda explicit=None: fake_soffice,
    )

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        profiles = list(output_dir.glob("aep-lo-doc-*/user/registrymodifications.xcu"))
        assert len(profiles) == 1
        policy = profiles[0].read_text(encoding="utf-8")
        assert "MacroSecurityLevel" in policy
        assert "DisableMacrosExecution" in policy
        assert "<value>true</value>" in policy
        destination = Path(command[command.index("--outdir") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        document = Document()
        document.add_paragraph("CONTEÚDO SINTÉTICO SEM VALIDADE")
        document.save(destination / "entrada.docx")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(document_renderer.subprocess, "run", fake_run)

    converted = convert_legacy_doc_to_docx(source, output_dir)

    assert converted == output_dir.resolve() / "entrada.docx"
    assert converted.is_file()
    command = observed["command"]
    assert isinstance(command, list)
    assert "--headless" in command
    assert "--convert-to" in command
    assert any(
        str(value).startswith("-env:UserInstallation=file:")
        for value in command
    )
    assert observed["kwargs"] == {
        "capture_output": True,
        "text": True,
        "timeout": 120,
        "check": False,
    }


def test_binary_doc_conversion_fails_closed_without_libreoffice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "entrada.doc"
    source.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"sintetico")
    monkeypatch.setattr(
        document_renderer,
        "find_libreoffice",
        lambda explicit=None: None,
    )

    with pytest.raises(LegacyConversionError, match="LibreOffice"):
        convert_legacy_doc_to_docx(source, tmp_path / "convertido")
