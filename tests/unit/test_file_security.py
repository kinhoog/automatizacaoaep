from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.file_security import UploadValidationError, inspect_file


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
DOCUMENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p/></w:body>
</w:document>"""


def _write_docx(
    path: Path,
    *,
    relationships: bytes | None = None,
    include_macro: bool = False,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("word/", b"")
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("word/document.xml", DOCUMENT_XML)
        if relationships is not None:
            package.writestr("word/_rels/document.xml.rels", relationships)
        if include_macro:
            package.writestr("word/vbaProject.bin", b"macro sintetica")


def _relationships(relationship_type: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="{relationship_type}"
    Target="https://example.invalid/sintetico" TargetMode="External"/>
</Relationships>""".encode()


def test_office_inspection_accepts_directory_entries_and_external_hyperlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sintetico.docx"
    _write_docx(
        source,
        relationships=_relationships(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
        ),
    )

    inspection = inspect_file(source, source.name)

    assert inspection.real_type == "docx"
    assert inspection.size == source.stat().st_size


def test_office_inspection_rejects_macro_payload(tmp_path: Path) -> None:
    source = tmp_path / "sintetico.docx"
    _write_docx(source, include_macro=True)

    with pytest.raises(UploadValidationError, match="macros"):
        inspect_file(source, source.name)


def test_office_inspection_rejects_non_hyperlink_external_relationship(
    tmp_path: Path,
) -> None:
    source = tmp_path / "sintetico.docx"
    _write_docx(
        source,
        relationships=_relationships(
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        ),
    )

    with pytest.raises(UploadValidationError, match="referência externa"):
        inspect_file(source, source.name)
