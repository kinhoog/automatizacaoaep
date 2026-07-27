"""Local-only image preparation for the generated AEP.

The compiler never sends images outside the machine.  This module converts
supported uploads to PNG, trims report whitespace and builds the same compact
panel/chart compositions used by the retained Word layout.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from app.models import GHE, ImageAsset, ImageRole, PsychosocialBlock
from app.services.normalization import canonical_ghe_code, normalize_key

_WHITE = (255, 255, 255, 255)
_BLUE = (12, 79, 151)
_TEAL = (42, 181, 185)
_LIGHT_BLUE = (237, 246, 251)
_DARK = (23, 41, 58)


def _asset_bytes(asset: ImageAsset | None) -> bytes | None:
    if asset is None:
        return None
    if asset.blob:
        return asset.blob
    if asset.runtime_path and Path(asset.runtime_path).is_file():
        return Path(asset.runtime_path).read_bytes()
    return None


def open_asset(asset: ImageAsset) -> Image.Image:
    payload = _asset_bytes(asset)
    if payload is None:
        raise ValueError("A imagem não está disponível no diretório da execução.")
    with Image.open(io.BytesIO(payload)) as source:
        source.load()
        return ImageOps.exif_transpose(source).convert("RGBA")


def image_to_png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.convert("RGBA").save(output, format="PNG", optimize=True)
    return output.getvalue()


def asset_to_png_bytes(asset: ImageAsset) -> bytes:
    return image_to_png_bytes(open_asset(asset))


def trim_report_whitespace(
    image: Image.Image,
    *,
    tolerance: int = 18,
    padding: int = 4,
) -> Image.Image:
    """Trim near-white margins while retaining a small safe border."""

    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, _WHITE)
    difference = ImageChops.difference(rgba, white).convert("L")
    mask = difference.point(lambda value: 255 if value > tolerance else 0)
    box = mask.getbbox()
    if box is None:
        return rgba
    left = max(0, box[0] - padding)
    top = max(0, box[1] - padding)
    right = min(rgba.width, box[2] + padding)
    bottom = min(rgba.height, box[3] + padding)
    return rgba.crop((left, top, right, bottom))


def _resize_width(image: Image.Image, width: int) -> Image.Image:
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def compose_vertical(
    images: Iterable[Image.Image],
    *,
    width: int = 1200,
    gap: int = 0,
    background: tuple[int, int, int, int] = _WHITE,
) -> Image.Image:
    prepared = [
        _resize_width(trim_report_whitespace(image), width) for image in images
    ]
    if not prepared:
        raise ValueError("Nenhuma imagem foi informada para a composição.")
    height = sum(image.height for image in prepared) + gap * (len(prepared) - 1)
    canvas = Image.new("RGBA", (width, height), background)
    y = 0
    for image in prepared:
        canvas.alpha_composite(image, (0, y))
        y += image.height + gap
    return canvas


def _role_candidates(
    block: PsychosocialBlock,
    roles: set[ImageRole],
) -> list[ImageAsset]:
    return [image for image in block.images if image.role in roles]


def _by_dimensions(
    images: Iterable[ImageAsset],
    predicate,
) -> ImageAsset | None:
    return next(
        (
            image
            for image in images
            if image.width_px
            and image.height_px
            and predicate(image.width_px, image.height_px)
        ),
        None,
    )


def choose_psychosocial_images(
    block: PsychosocialBlock,
) -> tuple[ImageAsset | None, ImageAsset | None, ImageAsset | None]:
    """Choose panel, chart/radar and risk matrix using role then geometry."""

    panel = next(
        iter(
            _role_candidates(
                block, {ImageRole.GHE_PANEL, ImageRole.GENERAL_PANEL}
            )
        ),
        None,
    )
    chart = next(
        iter(_role_candidates(block, {ImageRole.CHART, ImageRole.RADAR})),
        None,
    )
    matrix = next(
        iter(_role_candidates(block, {ImageRole.RISK_MATRIX})),
        None,
    )
    ordered = sorted(block.images, key=lambda item: item.order)
    panel = panel or _by_dimensions(
        ordered, lambda width, height: width / height >= 2.2
    )
    chart = chart or _by_dimensions(
        (
            image
            for image in ordered
            if panel is None or image.image_id != panel.image_id
        ),
        lambda width, height: 1.6 <= width / height <= 3.0,
    )
    matrix = matrix or _by_dimensions(
        reversed(ordered), lambda width, height: width / height >= 3.0
    )
    return panel, chart, matrix


def match_psychosocial_block(
    blocks: Iterable[PsychosocialBlock],
    ghe: GHE,
) -> PsychosocialBlock | None:
    code = ghe.canonical_code
    name = normalize_key(ghe.name)
    best: tuple[int, PsychosocialBlock] | None = None
    for block in blocks:
        score = 0
        block_code = canonical_ghe_code(
            block.official_ghe_code or block.ghe_code_hint
        )
        if block_code == code:
            score += 10
        block_name = normalize_key(block.ghe_name_hint or block.title)
        if name and name in block_name:
            score += 4
        if score and (best is None or score > best[0]):
            best = (score, block)
    return best[1] if best else None


def build_psychosocial_composite(
    block: PsychosocialBlock,
) -> tuple[bytes | None, bytes | None]:
    panel, chart, matrix = choose_psychosocial_images(block)
    composite: bytes | None = None
    if panel and chart:
        composite = image_to_png_bytes(
            compose_vertical([open_asset(panel), open_asset(chart)])
        )
    elif panel:
        composite = image_to_png_bytes(trim_report_whitespace(open_asset(panel)))
    elif chart:
        composite = image_to_png_bytes(trim_report_whitespace(open_asset(chart)))
    matrix_png = (
        image_to_png_bytes(trim_report_whitespace(open_asset(matrix)))
        if matrix
        else None
    )
    return composite, matrix_png


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    width: int,
    start_size: int,
    minimum: int = 22,
    bold: bool = False,
) -> ImageFont.ImageFont:
    for size in range(start_size, minimum - 1, -1):
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= width:
            return font
    return _font(minimum, bold=bold)


def build_hierarchy_image(
    company_name: str,
    ghes: list[GHE],
    *,
    width: int = 1400,
) -> bytes:
    """Create a source-driven, privacy-local hierarchy diagram."""

    columns = min(max(len(ghes), 1), 3)
    rows = max(1, (len(ghes) + columns - 1) // columns)
    height = 300 + rows * 250
    canvas = Image.new("RGBA", (width, height), _WHITE)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (140, 35, width - 140, 190),
        radius=28,
        fill=_BLUE,
        outline=_TEAL,
        width=5,
    )
    title_font = _fit_text(
        draw, company_name, width=width - 360, start_size=45, bold=True
    )
    bbox = draw.textbbox((0, 0), company_name, font=title_font)
    draw.text(
        ((width - (bbox[2] - bbox[0])) / 2, 88),
        company_name,
        fill="white",
        font=title_font,
    )
    draw.line((width / 2, 190, width / 2, 235), fill=_TEAL, width=6)

    cell_width = (width - 120) / columns
    for index, ghe in enumerate(ghes):
        row = index // columns
        column = index % columns
        x0 = 60 + column * cell_width + 18
        y0 = 240 + row * 250
        x1 = 60 + (column + 1) * cell_width - 18
        y1 = y0 + 190
        center = (x0 + x1) / 2
        if row == 0:
            draw.line((width / 2, 225, center, y0), fill=_TEAL, width=4)
        draw.rounded_rectangle(
            (x0, y0, x1, y1),
            radius=22,
            fill=_LIGHT_BLUE,
            outline=_BLUE,
            width=4,
        )
        heading = f"{ghe.canonical_code} — {ghe.name}"
        heading_font = _fit_text(
            draw, heading, width=int(x1 - x0 - 34), start_size=31, bold=True
        )
        heading_box = draw.textbbox((0, 0), heading, font=heading_font)
        draw.text(
            (center - (heading_box[2] - heading_box[0]) / 2, y0 + 37),
            heading,
            fill=_DARK,
            font=heading_font,
        )
        population = (
            f"{ghe.population} colaborador"
            f"{'' if ghe.population == 1 else 'es'}"
        )
        pop_font = _font(27)
        pop_box = draw.textbbox((0, 0), population, font=pop_font)
        draw.text(
            (center - (pop_box[2] - pop_box[0]) / 2, y0 + 113),
            population,
            fill=_BLUE,
            font=pop_font,
        )
    return image_to_png_bytes(canvas)
