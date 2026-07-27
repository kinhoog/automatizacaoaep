from __future__ import annotations

import hashlib
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile
from lxml import etree
from PIL import Image, UnidentifiedImageError


ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
IMAGE_FORMATS = {
    ".png": {"PNG"},
    ".jpg": {"JPEG"},
    ".jpeg": {"JPEG"},
    ".webp": {"WEBP"},
}
MAX_OFFICE_MEMBERS = 20_000
MAX_OFFICE_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_OFFICE_MEMBER_BYTES = 100 * 1024 * 1024
MAX_OFFICE_COMPRESSION_RATIO = 200
DOCX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document.main+xml"
)
XLSX_MAIN_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "spreadsheetml.sheet.main+xml"
)


class UploadValidationError(ValueError):
    """Erro de arquivo seguro para exibição ao usuário."""


@dataclass(frozen=True, slots=True)
class FileInspection:
    extension: str
    real_type: str
    size: int
    sha256: str
    image_width: int | None = None
    image_height: int | None = None


def safe_display_name(filename: str | None) -> str:
    name = Path(filename or "arquivo").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    return name[:120] or "arquivo"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _looks_like_html(prefix: bytes) -> bool:
    stripped = prefix.lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    lowered = stripped[:512].lower()
    return lowered.startswith((b"<!doctype html", b"<html", b"<head", b"<body"))


def _parse_office_xml(payload: bytes, member_name: str) -> etree._Element:
    prefix = payload[:4096].upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise UploadValidationError(
            f"O pacote Office contém XML não permitido em {member_name}."
        )
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
    )
    try:
        return etree.fromstring(payload, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise UploadValidationError(
            f"O XML principal do pacote Office está corrompido ({member_name})."
        ) from exc


def _validate_office_members(infos: list[zipfile.ZipInfo]) -> None:
    if not infos or len(infos) > MAX_OFFICE_MEMBERS:
        raise UploadValidationError(
            "O pacote Office possui uma quantidade insegura de componentes."
        )

    names: set[str] = set()
    total_uncompressed = 0
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        is_directory = info.is_dir() or normalized.endswith("/")
        normalized_path = normalized.rstrip("/") if is_directory else normalized
        parts = normalized_path.split("/")
        if (
            not normalized_path
            or normalized_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            raise UploadValidationError(
                "O pacote Office contém um caminho interno inseguro."
            )
        folded = normalized_path.casefold()
        if folded in names:
            raise UploadValidationError(
                "O pacote Office contém componentes internos duplicados."
            )
        names.add(folded)
        if info.flag_bits & 0x1:
            raise UploadValidationError(
                "Pacotes Office criptografados não são aceitos."
            )
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise UploadValidationError(
                "O pacote Office contém um vínculo interno inseguro."
            )
        if is_directory:
            continue
        if info.file_size > MAX_OFFICE_MEMBER_BYTES:
            raise UploadValidationError(
                "Um componente do pacote Office excede o limite seguro."
            )
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_OFFICE_UNCOMPRESSED_BYTES:
            raise UploadValidationError(
                "O arquivo compactado excede o limite seguro de conteúdo."
            )
        if (
            info.file_size >= 1024 * 1024
            and info.file_size / max(info.compress_size, 1)
            > MAX_OFFICE_COMPRESSION_RATIO
        ):
            raise UploadValidationError(
                "O pacote Office possui compactação potencialmente maliciosa."
            )
        if folded.endswith("/vbaproject.bin") or folded.endswith(
            "/vbaprojectsignature.bin"
        ):
            raise UploadValidationError(
                "Documentos Office com macros não são aceitos."
            )


def _validate_relationships(archive: zipfile.ZipFile, names: set[str]) -> None:
    for name in names:
        if not name.casefold().endswith(".rels"):
            continue
        root = _parse_office_xml(archive.read(name), name)
        for relationship in root.iter():
            if str(relationship.get("TargetMode", "")).casefold() == "external":
                relationship_type = str(
                    relationship.get("Type", "")
                ).casefold()
                if not relationship_type.endswith("/hyperlink"):
                    raise UploadValidationError(
                        "O pacote Office contém referência externa não permitida."
                    )


def _inspect_zip(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_office_members(infos)
            names = {item.filename for item in infos}
            if "[Content_Types].xml" not in names:
                raise UploadValidationError(
                    "O pacote Office não possui a declaração de conteúdo esperada."
                )
            content_types = _parse_office_xml(
                archive.read("[Content_Types].xml"),
                "[Content_Types].xml",
            )
            declared_types = {
                str(item.get("ContentType", "")) for item in content_types.iter()
            }
            if any("macroenabled" in item.casefold() for item in declared_types):
                raise UploadValidationError(
                    "Documentos Office com macros não são aceitos."
                )
            if (
                "word/document.xml" in names
                and DOCX_MAIN_CONTENT_TYPE in declared_types
            ):
                _parse_office_xml(
                    archive.read("word/document.xml"), "word/document.xml"
                )
                _validate_relationships(archive, names)
                return "docx"
            if (
                "xl/workbook.xml" in names
                and XLSX_MAIN_CONTENT_TYPE in declared_types
            ):
                _parse_office_xml(
                    archive.read("xl/workbook.xml"), "xl/workbook.xml"
                )
                _validate_relationships(archive, names)
                return "xlsx"
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise UploadValidationError("O arquivo Office está corrompido.") from exc
    raise UploadValidationError("O pacote Office não possui a estrutura esperada.")


def inspect_file(path: Path, original_name: str) -> FileInspection:
    extension = Path(original_name).suffix.lower()
    size = path.stat().st_size
    with path.open("rb") as stream:
        prefix = stream.read(1024)

    width: int | None = None
    height: int | None = None
    if extension in IMAGE_FORMATS:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if image.format not in IMAGE_FORMATS[extension]:
                    raise UploadValidationError(
                        "A extensão da imagem não corresponde ao conteúdo."
                    )
                width, height = image.size
                real_type = image.format.lower()
        except (UnidentifiedImageError, OSError) as exc:
            raise UploadValidationError("A imagem é inválida ou está corrompida.") from exc
    elif extension == ".docx":
        if not prefix.startswith(ZIP_MAGIC):
            raise UploadValidationError("O arquivo informado não é um DOCX válido.")
        real_type = _inspect_zip(path)
        if real_type != "docx":
            raise UploadValidationError("O arquivo informado não é um DOCX válido.")
    elif extension == ".xlsx":
        if not prefix.startswith(ZIP_MAGIC):
            raise UploadValidationError("O arquivo informado não é um XLSX válido.")
        real_type = _inspect_zip(path)
        if real_type != "xlsx":
            raise UploadValidationError("O arquivo informado não é um XLSX válido.")
    elif extension == ".doc":
        if _looks_like_html(prefix):
            real_type = "html-doc"
        elif prefix.startswith(OLE_MAGIC):
            real_type = "binary-doc"
        else:
            raise UploadValidationError(
                "O arquivo .doc não é HTML compatível nem um documento binário reconhecido."
            )
    else:
        raise UploadValidationError(f"Formato não suportado: {extension or 'sem extensão'}.")

    return FileInspection(
        extension=extension,
        real_type=real_type,
        size=size,
        sha256=sha256_file(path),
        image_width=width,
        image_height=height,
    )


async def save_upload_limited(
    upload: UploadFile,
    destination: Path,
    *,
    max_bytes: int,
    allowed_extensions: set[str],
) -> FileInspection:
    original_name = safe_display_name(upload.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in allowed_extensions:
        accepted = ", ".join(sorted(allowed_extensions))
        raise UploadValidationError(f"Formato inválido. Aceitos: {accepted}.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with destination.open("xb") as stream:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise UploadValidationError(
                        f"O arquivo excede o limite de {max_bytes // (1024 * 1024)} MB."
                    )
                stream.write(chunk)
    except FileExistsError as exc:
        raise UploadValidationError("Falha ao reservar o arquivo temporário.") from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    if total == 0:
        destination.unlink(missing_ok=True)
        raise UploadValidationError("O arquivo enviado está vazio.")
    try:
        return inspect_file(destination, original_name)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def copy_stream_limited(
    source: BinaryIO,
    destination: Path,
    *,
    max_bytes: int,
) -> int:
    """Auxiliar síncrono usado por scripts e testes sem contornar os mesmos limites."""
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise UploadValidationError("O arquivo excede o limite permitido.")
            target.write(chunk)
    return total
