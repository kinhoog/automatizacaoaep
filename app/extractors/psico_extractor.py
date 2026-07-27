"""Psychosocial DOCX image extraction and positional GHE association."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from PIL import Image, UnidentifiedImageError

from app.models import (
    GHE,
    ImageAsset,
    ImageRole,
    PsychosocialBlock,
    PsychosocialReport,
)
from app.services.normalization import (
    clean_text,
    ghe_name_similarity,
    looks_like_heading,
    normalize_key,
    parse_ghe_reference,
)

from . import ExtractionError, UnsupportedSourceError


@dataclass(slots=True)
class _TextUnit:
    text: str
    heading: bool


@dataclass(slots=True)
class _ImageUnit:
    blob: bytes
    source_part: str | None
    alt_text: str | None = None


def _iter_blocks(parent: _Document | _Cell) -> Iterator[Paragraph | Table]:
    parent_element = (
        parent.element.body if isinstance(parent, _Document) else parent._tc
    )
    for child in parent_element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _paragraph_image_units(paragraph: Paragraph) -> Iterator[_ImageUnit]:
    relationship_nodes: list[tuple[object, str]] = []
    try:
        relationship_nodes.extend((node, "embed") for node in paragraph._p.xpath(".//a:blip"))
    except Exception:
        pass
    try:
        relationship_nodes.extend(
            (node, "id") for node in paragraph._p.xpath(".//v:imagedata")
        )
    except Exception:
        pass

    for node, relationship_kind in relationship_nodes:
        if relationship_kind == "embed":
            relationship_id = node.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
            )
        else:
            relationship_id = node.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
        if not relationship_id:
            continue
        try:
            part = paragraph.part.related_parts[relationship_id]
            blob = part.blob
        except (KeyError, AttributeError):
            continue
        alt_text: str | None = None
        if relationship_kind == "embed":
            try:
                drawings = node.xpath("ancestor::w:drawing[1]")
                properties = (
                    drawings[0].xpath(".//wp:docPr") if drawings else []
                )
                if properties:
                    alt_text = clean_text(
                        properties[0].get("descr")
                        or properties[0].get("title")
                        or properties[0].get("name")
                    ) or None
            except Exception:
                alt_text = None
        else:
            alt_text = clean_text(
                node.get("alt") or node.get("title")
            ) or None
        yield _ImageUnit(
            blob=blob,
            source_part=str(getattr(part, "partname", "")) or None,
            alt_text=alt_text,
        )


def _document_units(document: _Document) -> Iterator[_TextUnit | _ImageUnit]:
    seen_cells: set[int] = set()
    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = clean_text(block.text)
            heading = looks_like_heading(
                text, block.style.name if block.style else None
            )
            if text:
                yield _TextUnit(text=text, heading=heading)
            yield from _paragraph_image_units(block)
            continue

        for row in block.rows:
            for cell in row.cells:
                cell_identity = id(cell._tc)
                if cell_identity in seen_cells:
                    continue
                seen_cells.add(cell_identity)
                for cell_block in _iter_blocks(cell):
                    if isinstance(cell_block, Paragraph):
                        text = clean_text(cell_block.text)
                        heading = looks_like_heading(
                            text,
                            cell_block.style.name if cell_block.style else None,
                        )
                        if text:
                            yield _TextUnit(text=text, heading=heading)
                        yield from _paragraph_image_units(cell_block)


def _contextual_text(text: str) -> bool:
    key = normalize_key(text)
    code, _ = parse_ghe_reference(text)
    return bool(
        code
        or any(
            token in key
            for token in (
                "painel",
                "grafico",
                "radar",
                "matriz",
                "dominio",
                "favorabilidade",
                "resumo",
                "risco",
            )
        )
    )


def _classify_role(context: str, ghe_code: str | None) -> ImageRole:
    key = normalize_key(context)
    if "matriz" in key and "risco" in key:
        return ImageRole.RISK_MATRIX
    if "radar" in key:
        return ImageRole.RADAR
    if "dominio" in key and any(
        word in key for word in ("resumo", "sintese", "painel")
    ):
        return ImageRole.DOMAIN_SUMMARY
    if "favorabilidade" in key or "favoravel" in key:
        return ImageRole.FAVORABILITY
    if "painel geral" in key or "visao geral" in key:
        return ImageRole.GENERAL_PANEL
    if ghe_code and "painel" in key:
        return ImageRole.GHE_PANEL
    if "grafico" in key or "chart" in key:
        return ImageRole.CHART
    return ImageRole.OTHER


def _make_asset(
    unit: _ImageUnit,
    *,
    order: int,
    context: str,
    caption: str | None,
    ghe_code: str | None,
    ghe_name: str | None,
) -> ImageAsset:
    image_format: str | None = None
    width: int | None = None
    height: int | None = None
    content_type: str | None = None
    try:
        with Image.open(io.BytesIO(unit.blob)) as image:
            image_format = (image.format or "").upper() or None
            width, height = image.size
            content_type = Image.MIME.get(image.format or "")
    except (UnidentifiedImageError, OSError):
        pass
    return ImageAsset(
        image_id=f"psico-image-{order:04d}",
        order=order,
        role=_classify_role(context, ghe_code),
        sha256=hashlib.sha256(unit.blob).hexdigest(),
        width_px=width,
        height_px=height,
        image_format=image_format,
        content_type=content_type,
        caption=caption,
        context=context or None,
        ghe_code_hint=ghe_code,
        ghe_name_hint=ghe_name,
        source_part=unit.source_part,
        blob=unit.blob,
    )


def _nearest_context(
    units: list[_TextUnit | _ImageUnit], index: int
) -> tuple[str, str | None, str | None, str | None]:
    previous: list[str] = []
    following: list[str] = []
    ghe_code: str | None = None
    ghe_name: str | None = None

    image_unit = units[index]
    own_context = (
        image_unit.alt_text
        if isinstance(image_unit, _ImageUnit) and image_unit.alt_text
        else None
    )
    if own_context:
        code, name = parse_ghe_reference(own_context)
        ghe_code, ghe_name = code, name
        if _contextual_text(own_context):
            previous.append(own_context)

    for candidate in reversed(units[:index]):
        if not isinstance(candidate, _TextUnit):
            continue
        code, name = parse_ghe_reference(candidate.text)
        if code and ghe_code is None:
            ghe_code, ghe_name = code, name
        if _contextual_text(candidate.text) and len(previous) < 4:
            previous.append(candidate.text)
        if ghe_code and len(previous) >= 2:
            break
    previous.reverse()
    for candidate in units[index + 1 :]:
        if not isinstance(candidate, _TextUnit):
            continue
        if _contextual_text(candidate.text) and len(following) < 2:
            following.append(candidate.text)
        if len(following) >= 2:
            break
    combined = " | ".join(previous + following)
    caption = own_context or (
        following[0] if following else (previous[-1] if previous else None)
    )
    return combined, caption, ghe_code, ghe_name


def _build_blocks(
    headings: list[str], images: list[ImageAsset]
) -> list[PsychosocialBlock]:
    blocks: list[PsychosocialBlock] = []
    general_images = [
        image for image in images if image.ghe_code_hint is None
    ]
    if general_images:
        for index, image in enumerate(general_images):
            if image.role == ImageRole.OTHER and index == 0:
                image.role = ImageRole.GENERAL_PANEL
        blocks.append(
            PsychosocialBlock(
                block_id="psico-general",
                order=0,
                title=next(
                    (
                        heading
                        for heading in headings
                        if "geral" in normalize_key(heading)
                    ),
                    "Painel geral",
                ),
                headings=[
                    heading
                    for heading in headings
                    if parse_ghe_reference(heading)[0] is None
                ],
                images=general_images,
            )
        )

    seen_codes: list[str] = []
    for image in images:
        if image.ghe_code_hint and image.ghe_code_hint not in seen_codes:
            seen_codes.append(image.ghe_code_hint)
    for code in seen_codes:
        block_images = [
            image for image in images if image.ghe_code_hint == code
        ]
        for index, image in enumerate(block_images):
            if image.role == ImageRole.OTHER and index == 0:
                image.role = ImageRole.GHE_PANEL
        title = next(
            (
                heading
                for heading in headings
                if parse_ghe_reference(heading)[0] == code
            ),
            code,
        )
        _, name = parse_ghe_reference(title)
        blocks.append(
            PsychosocialBlock(
                block_id=f"psico-ghe-{len(blocks):03d}",
                order=len(blocks),
                title=title,
                ghe_code_hint=code,
                ghe_name_hint=name,
                headings=[
                    heading
                    for heading in headings
                    if parse_ghe_reference(heading)[0] == code
                ],
                images=block_images,
            )
        )
    return blocks


def associate_psychosocial_images(
    report: PsychosocialReport, official_ghes: Sequence[GHE]
) -> PsychosocialReport:
    """Associate occurrences conservatively using code, name and block position."""

    result = report.model_copy(deep=True)
    by_code = {ghe.canonical_code: ghe for ghe in official_ghes}

    def choose(image: ImageAsset) -> str | None:
        code_match = by_code.get(image.ghe_code_hint or "")
        name_scores = sorted(
            (
                (ghe_name_similarity(image.ghe_name_hint, ghe.name), ghe)
                for ghe in official_ghes
                if image.ghe_name_hint
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        name_match = (
            name_scores[0][1]
            if name_scores
            and name_scores[0][0] >= 0.82
            and (len(name_scores) == 1 or name_scores[0][0] > name_scores[1][0])
            else None
        )
        if code_match and name_match and code_match.canonical_code != name_match.canonical_code:
            result.warnings.append(
                f"{image.image_id}: código e nome indicam GHEs oficiais diferentes."
            )
            return None
        if code_match and (
            not image.ghe_name_hint
            or ghe_name_similarity(image.ghe_name_hint, code_match.name) >= 0.65
        ):
            return code_match.canonical_code
        if name_match:
            return name_match.canonical_code
        return None

    official_by_image: dict[str, str | None] = {}
    for image in result.images:
        image.official_ghe_code = choose(image)
        official_by_image[image.image_id] = image.official_ghe_code
        if image.ghe_code_hint and image.official_ghe_code is None:
            result.warnings.append(
                f"{image.image_id}: associação oficial requer revisão."
            )
    for block in result.blocks:
        block_codes = {
            official_by_image.get(image.image_id)
            for image in block.images
            if official_by_image.get(image.image_id)
        }
        if len(block_codes) == 1:
            block.official_ghe_code = next(iter(block_codes))
        for image in block.images:
            image.official_ghe_code = official_by_image.get(image.image_id)
    return result


class PsychosocialExtractor:
    def extract(
        self,
        source: str | Path,
        official_ghes: Sequence[GHE] | None = None,
    ) -> PsychosocialReport:
        source_path = Path(source)
        try:
            signature = source_path.read_bytes()[:4]
        except OSError as exc:
            raise ExtractionError(
                "Não foi possível ler o relatório psicossocial."
            ) from exc
        if signature != b"PK\x03\x04":
            raise UnsupportedSourceError(
                "O relatório psicossocial deve ser um DOCX válido."
            )
        try:
            document = Document(source_path)
        except Exception as exc:
            raise ExtractionError(
                "O relatório psicossocial DOCX está corrompido."
            ) from exc

        units = list(_document_units(document))
        headings = [
            unit.text
            for unit in units
            if isinstance(unit, _TextUnit) and unit.heading
        ]
        images: list[ImageAsset] = []
        warnings: list[str] = []
        for unit_index, unit in enumerate(units):
            if not isinstance(unit, _ImageUnit):
                continue
            context, caption, ghe_code, ghe_name = _nearest_context(
                units, unit_index
            )
            asset = _make_asset(
                unit,
                order=len(images),
                context=context,
                caption=caption,
                ghe_code=ghe_code,
                ghe_name=ghe_name,
            )
            if asset.width_px is None:
                warnings.append(
                    f"{asset.image_id}: formato de imagem não reconhecido."
                )
            images.append(asset)
        if not images:
            warnings.append(
                "Nenhuma imagem incorporada foi encontrada no relatório psicossocial."
            )
        report = PsychosocialReport(
            headings=headings,
            images=images,
            blocks=_build_blocks(headings, images),
            warnings=warnings,
        )
        if official_ghes is not None:
            report = associate_psychosocial_images(report, official_ghes)
        return report


def extract_psychosocial(
    source: str | Path,
    official_ghes: Sequence[GHE] | None = None,
) -> PsychosocialReport:
    return PsychosocialExtractor().extract(source, official_ghes)
