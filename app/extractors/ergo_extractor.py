"""Ergonomic report extractor for HTML-disguised ``.doc`` and DOCX files."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import unquote, unquote_to_bytes, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from PIL import Image, UnidentifiedImageError

from app.models import (
    ContentKind,
    ErgoBlock,
    ErgoContentElement,
    ErgoReport,
    FileKind,
    ImageAsset,
    ImageRole,
)
from app.services.normalization import (
    clean_text,
    looks_like_heading,
    normalize_key,
    parse_ghe_reference,
)

from . import ConversionRequiredError, ExtractionError, UnsupportedSourceError

_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_HTML_MARKERS = (
    b"<html",
    b"<!doctype html",
    b"<head",
    b"<body",
    b"<table",
    b"xmlns:w=",
)


def _detect_kind(payload: bytes) -> FileKind:
    if payload.startswith(_ZIP_SIGNATURES):
        return FileKind.DOCX
    if payload.startswith(_OLE_SIGNATURE):
        return FileKind.OLE_DOC
    sample = payload[:65536].lstrip(b"\xef\xbb\xbf\xff\xfe\x00")
    lowered = sample.lower().replace(b"\x00", b"")
    if any(marker in lowered for marker in _HTML_MARKERS):
        return FileKind.HTML_DOC
    return FileKind.UNKNOWN


def _decode_html(payload: bytes) -> str:
    probe = payload[:4096].decode("ascii", errors="ignore")
    charset_match = re.search(
        r"(?i)charset\s*=\s*[\"']?\s*([a-z0-9._-]+)", probe
    )
    candidates = [
        charset_match.group(1) if charset_match else None,
        "utf-8-sig",
        "cp1252",
        "latin-1",
    ]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def _image_metadata(
    blob: bytes,
    *,
    image_id: str,
    order: int,
    source_part: str | None,
) -> ImageAsset:
    image_format: str | None = None
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    try:
        with Image.open(io.BytesIO(blob)) as image:
            image_format = (image.format or "").upper() or None
            width, height = image.size
            content_type = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError):
        # The occurrence is still preserved and later validation reports the
        # inability to inspect it.
        pass
    return ImageAsset(
        image_id=image_id,
        order=order,
        role=ImageRole.OTHER,
        sha256=hashlib.sha256(blob).hexdigest(),
        width_px=width,
        height_px=height,
        image_format=image_format,
        content_type=content_type,
        source_part=source_part,
        blob=blob,
    )


def _safe_relative_image(source_path: Path, reference: str) -> bytes | None:
    parts = urlsplit(reference)
    if parts.scheme and parts.scheme.casefold() not in {"file"}:
        return None
    if parts.scheme == "file":
        # Absolute file URLs from the producing workstation are intentionally
        # not followed.
        return None
    relative_text = unquote(parts.path).replace("\\", "/").lstrip("/")
    if not relative_text:
        return None
    base = source_path.parent.resolve()
    candidate = (base / relative_text).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate.read_bytes()


def _html_image_blob(source_path: Path, reference: str) -> bytes | None:
    if reference.casefold().startswith("data:"):
        try:
            header, encoded = reference.split(",", 1)
        except ValueError:
            return None
        try:
            if ";base64" in header.casefold():
                return base64.b64decode(encoded, validate=True)
            return unquote_to_bytes(encoded)
        except (ValueError, binascii.Error):
            return None
    return _safe_relative_image(source_path, reference)


def _is_ghe_heading(text: str, *, structural: bool = False) -> bool:
    code, _ = parse_ghe_reference(text)
    if not code or len(text) > 180:
        return False
    key = normalize_key(text)
    return structural or key.startswith(
        ("ghe ", "grupo ghe ", "analise ghe ", "resultados ghe ", "ghe n ")
    )


def _table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = [
            clean_text(cell.get_text(" ", strip=True))
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        if cells and any(cells):
            rows.append(cells)
    return rows


def _walk_html(node: Tag) -> Iterator[tuple[str, object, bool]]:
    """Yield semantic HTML units once, in visual DOM order."""

    for child in node.children:
        if isinstance(child, NavigableString):
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.casefold()
        if name in {"script", "style", "head", "meta", "link"}:
            continue
        if name in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            text = clean_text(child.get_text(" ", strip=True))
            if text:
                kind = (
                    "heading"
                    if name.startswith("h") or _is_ghe_heading(text)
                    else "text"
                )
                yield kind, text, name.startswith("h")
            for image in child.find_all(["img", "v:imagedata"]):
                yield "image", image, False
            continue
        if name == "table":
            rows = _table_rows(child)
            heading = next(
                (
                    cell
                    for row in rows[:3]
                    for cell in row
                    if _is_ghe_heading(cell, structural=True)
                ),
                None,
            )
            if heading:
                yield "heading", heading, True
            if rows:
                yield "table", rows, False
            for image in child.find_all(["img", "v:imagedata"]):
                yield "image", image, False
            continue
        if name in {"img", "v:imagedata"}:
            yield "image", child, False
            continue
        yield from _walk_html(child)


def _iter_docx_blocks(parent: _Document | _Cell) -> Iterator[Paragraph | Table]:
    parent_element = (
        parent.element.body if isinstance(parent, _Document) else parent._tc
    )
    for child in parent_element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _paragraph_images(
    paragraph: Paragraph, image_order: int
) -> tuple[list[ImageAsset], int]:
    images: list[ImageAsset] = []
    try:
        blips = paragraph._p.xpath(".//a:blip")
    except Exception:
        blips = []
    for blip in blips:
        relationship_id = blip.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        )
        if not relationship_id:
            continue
        try:
            part = paragraph.part.related_parts[relationship_id]
            blob = part.blob
        except (KeyError, AttributeError):
            continue
        images.append(
            _image_metadata(
                blob,
                image_id=f"ergo-image-{image_order:04d}",
                order=image_order,
                source_part=str(getattr(part, "partname", "")) or None,
            )
        )
        image_order += 1
    return images, image_order


def _tagged_elements_from_html(
    source_path: Path, payload: bytes
) -> tuple[list[tuple[ErgoContentElement, bool]], list[str]]:
    soup = BeautifulSoup(_decode_html(payload), "lxml")
    root = soup.body or soup
    result: list[tuple[ErgoContentElement, bool]] = []
    warnings: list[str] = []
    order = 0
    image_order = 0
    for unit_kind, payload_value, structural in _walk_html(root):
        if unit_kind in {"heading", "text"}:
            text = clean_text(payload_value)
            if not text:
                continue
            kind = (
                ContentKind.HEADING
                if unit_kind == "heading"
                else ContentKind.PARAGRAPH
            )
            result.append(
                (
                    ErgoContentElement(order=order, kind=kind, text=text),
                    bool(structural or unit_kind == "heading"),
                )
            )
            order += 1
        elif unit_kind == "table":
            rows = payload_value
            if not rows:
                continue
            result.append(
                (
                    ErgoContentElement(
                        order=order,
                        kind=ContentKind.TABLE,
                        rows=rows,
                    ),
                    False,
                )
            )
            order += 1
        elif unit_kind == "image":
            tag = payload_value
            reference = clean_text(tag.get("src") or tag.get("o:href") or "")
            blob = _html_image_blob(source_path, reference) if reference else None
            if blob is None:
                warnings.append(
                    "Uma imagem referenciada no Ergo não pôde ser resolvida."
                )
                continue
            image = _image_metadata(
                blob,
                image_id=f"ergo-image-{image_order:04d}",
                order=image_order,
                source_part=reference,
            )
            result.append(
                (
                    ErgoContentElement(
                        order=order,
                        kind=ContentKind.IMAGE,
                        image=image,
                    ),
                    False,
                )
            )
            image_order += 1
            order += 1
    return result, warnings


def _tagged_elements_from_docx(
    source_path: Path,
) -> tuple[list[tuple[ErgoContentElement, bool]], list[str]]:
    try:
        document = Document(source_path)
    except Exception as exc:
        raise ExtractionError("O relatório Ergo DOCX está corrompido.") from exc
    result: list[tuple[ErgoContentElement, bool]] = []
    warnings: list[str] = []
    order = 0
    image_order = 0
    for block in _iter_docx_blocks(document):
        if isinstance(block, Paragraph):
            text = clean_text(block.text)
            structural = looks_like_heading(text, block.style.name if block.style else None)
            if text:
                result.append(
                    (
                        ErgoContentElement(
                            order=order,
                            kind=(
                                ContentKind.HEADING
                                if structural
                                else ContentKind.PARAGRAPH
                            ),
                            text=text,
                        ),
                        structural,
                    )
                )
                order += 1
            images, image_order = _paragraph_images(block, image_order)
            for image in images:
                result.append(
                    (
                        ErgoContentElement(
                            order=order,
                            kind=ContentKind.IMAGE,
                            image=image,
                        ),
                        False,
                    )
                )
                order += 1
        else:
            rows = [
                [clean_text(cell.text) for cell in row.cells]
                for row in block.rows
            ]
            rows = [row for row in rows if any(row)]
            if rows:
                result.append(
                    (
                        ErgoContentElement(
                            order=order,
                            kind=ContentKind.TABLE,
                            rows=rows,
                        ),
                        False,
                    )
                )
                order += 1
            for row in block.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        images, image_order = _paragraph_images(
                            paragraph, image_order
                        )
                        for image in images:
                            result.append(
                                (
                                    ErgoContentElement(
                                        order=order,
                                        kind=ContentKind.IMAGE,
                                        image=image,
                                    ),
                                    False,
                                )
                            )
                            order += 1
    return result, warnings


def _text_values(element: ErgoContentElement) -> Iterable[str]:
    if element.text:
        yield element.text
    for row in element.rows:
        yield from (cell for cell in row if cell)


def _append_labeled_values(block: ErgoBlock, element: ErgoContentElement) -> None:
    for value in _text_values(element):
        key = normalize_key(value)
        if key.startswith(("pergunta ", "questao ")):
            block.questions.append(value)
        elif key.startswith(("resposta ", "resultado ")):
            block.answers.append(value)
        elif key.startswith(("observacao ", "observacoes ", "comentario ")):
            block.observations.append(value)
        elif key.startswith(
            (
                "orientacao ",
                "orientacoes ",
                "recomendacao ",
                "recomendacoes ",
                "medida proposta ",
            )
        ):
            block.guidance.append(value)


def _assemble_report(
    tagged: list[tuple[ErgoContentElement, bool]],
    detected_format: FileKind,
    warnings: list[str],
) -> ErgoReport:
    preamble: list[ErgoContentElement] = []
    blocks: list[ErgoBlock] = []
    active: ErgoBlock | None = None
    seen_boundaries: set[tuple[str, str]] = set()

    for element, structural in tagged:
        boundary_text = next(iter(_text_values(element)), "")
        code, name = parse_ghe_reference(boundary_text)
        is_boundary = bool(
            code
            and _is_ghe_heading(boundary_text, structural=structural)
            and (code, normalize_key(name)) not in seen_boundaries
        )
        if is_boundary:
            active = ErgoBlock(
                source_id=f"ergo-{len(blocks) + 1:03d}",
                order=len(blocks),
                title=boundary_text,
                source_code=code,
                source_name=name,
            )
            blocks.append(active)
            seen_boundaries.add((code or "", normalize_key(name)))
        if active is None:
            preamble.append(element)
        else:
            active.elements.append(element)
            _append_labeled_values(active, element)

    if not blocks:
        warnings.append(
            "Nenhum título de GHE foi identificado automaticamente no relatório Ergo."
        )
    return ErgoReport(
        detected_format=detected_format,
        preamble=preamble,
        blocks=blocks,
        warnings=warnings,
    )


class ErgoExtractor:
    """Extract all source blocks without silently dropping or renumbering any."""

    def extract(self, source: str | Path) -> ErgoReport:
        source_path = Path(source)
        try:
            payload = source_path.read_bytes()
        except OSError as exc:
            raise ExtractionError("Não foi possível ler o relatório Ergo.") from exc
        detected = _detect_kind(payload)
        if detected == FileKind.OLE_DOC:
            raise ConversionRequiredError(
                "O relatório Ergo é um DOC binário e requer conversão segura "
                "com LibreOffice antes da extração."
            )
        if detected == FileKind.HTML_DOC:
            tagged, warnings = _tagged_elements_from_html(source_path, payload)
        elif detected == FileKind.DOCX:
            tagged, warnings = _tagged_elements_from_docx(source_path)
        else:
            raise UnsupportedSourceError(
                "O tipo real do relatório Ergo não é HTML, DOCX ou DOC binário."
            )
        return _assemble_report(tagged, detected, warnings)


def extract_ergo(source: str | Path) -> ErgoReport:
    return ErgoExtractor().extract(source)
