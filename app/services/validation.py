"""Upload and normalized-model validation with privacy-safe messages."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from PIL import Image, UnidentifiedImageError

from app.models import (
    AnalysisMode,
    ArtifactMetadata,
    FileKind,
    ImageRole,
    NormalizedAEP,
    ReconciliationStatus,
    Severity,
    TechnicalCategory,
    ValidationReport,
)

from .normalization import canonical_ghe_code, normalize_key

_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_MIME_TYPES = {
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.HTML_DOC: "text/html",
    FileKind.OLE_DOC: "application/msword",
    FileKind.PNG: "image/png",
    FileKind.JPEG: "image/jpeg",
    FileKind.WEBP: "image/webp",
}


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    allowed_extensions: frozenset[str]
    allowed_kinds: frozenset[FileKind]
    max_size_bytes: int = 30 * 1024 * 1024
    required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_extensions",
            frozenset(
                extension.lower()
                if extension.startswith(".")
                else f".{extension.lower()}"
                for extension in self.allowed_extensions
            ),
        )


_POLICIES: dict[str, UploadPolicy] = {
    "ghe": UploadPolicy(frozenset({".xlsx"}), frozenset({FileKind.XLSX})),
    "psychosocial": UploadPolicy(
        frozenset({".docx"}), frozenset({FileKind.DOCX})
    ),
    "ergo": UploadPolicy(
        frozenset({".doc", ".docx"}),
        frozenset({FileKind.HTML_DOC, FileKind.OLE_DOC, FileKind.DOCX}),
    ),
    "technical_integrated": UploadPolicy(
        frozenset({".docx"}), frozenset({FileKind.DOCX})
    ),
    "technical_psychosocial": UploadPolicy(
        frozenset({".docx"}), frozenset({FileKind.DOCX})
    ),
    "technical_ergonomic": UploadPolicy(
        frozenset({".docx"}), frozenset({FileKind.DOCX})
    ),
    "registration_card": UploadPolicy(
        frozenset({".png", ".jpg", ".jpeg", ".webp"}),
        frozenset({FileKind.PNG, FileKind.JPEG, FileKind.WEBP}),
    ),
    "logo": UploadPolicy(
        frozenset({".png", ".jpg", ".jpeg", ".webp"}),
        frozenset({FileKind.PNG, FileKind.JPEG, FileKind.WEBP}),
        required=False,
    ),
}


def policy_for(role: str) -> UploadPolicy:
    try:
        return _POLICIES[role]
    except KeyError as exc:
        raise ValueError("unknown upload role") from exc


def sanitize_upload_filename(filename: str) -> str:
    """Strip path components and unsafe characters from a display filename."""

    leaf = Path(filename.replace("\\", "/")).name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", leaf).strip("._")
    return stem[:120] or "upload"


def _zip_kind(payload: bytes) -> FileKind:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return FileKind.UNKNOWN
    if "xl/workbook.xml" in names:
        return FileKind.XLSX
    if "word/document.xml" in names:
        return FileKind.DOCX
    return FileKind.UNKNOWN


def detect_file_kind(source: str | Path | bytes) -> FileKind:
    """Detect a supported type from its signature and package contents."""

    if isinstance(source, bytes):
        payload = source
    else:
        try:
            payload = Path(source).read_bytes()
        except OSError:
            return FileKind.UNKNOWN
    if payload.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return _zip_kind(payload)
    if payload.startswith(_OLE_SIGNATURE):
        return FileKind.OLE_DOC
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if payload.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return FileKind.WEBP
    sample = payload[:65536].lower().replace(b"\x00", b"").lstrip(
        b"\xef\xbb\xbf\xff\xfe"
    )
    if any(
        marker in sample
        for marker in (
            b"<!doctype html",
            b"<html",
            b"<head",
            b"<body",
            b"<table",
            b"xmlns:w=",
        )
    ):
        return FileKind.HTML_DOC
    return FileKind.UNKNOWN


def _check_zip_safety(payload: bytes, report: ValidationReport) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > 5000:
                report.add(
                    Severity.ERROR,
                    "archive_too_many_entries",
                    "O pacote contém entradas demais para processamento seguro.",
                )
            total_uncompressed = 0
            for info in infos:
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts:
                    report.add(
                        Severity.ERROR,
                        "unsafe_archive_path",
                        "O pacote contém um caminho interno inseguro.",
                    )
                    break
                total_uncompressed += info.file_size
                if info.flag_bits & 0x1:
                    report.add(
                        Severity.ERROR,
                        "encrypted_archive_entry",
                        "O pacote contém uma entrada criptografada.",
                    )
                    break
                if (
                    info.file_size > 10 * 1024 * 1024
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > 200
                ):
                    report.add(
                        Severity.ERROR,
                        "suspicious_compression_ratio",
                        "O pacote possui taxa de compressão insegura.",
                    )
                    break
            if total_uncompressed > 250 * 1024 * 1024:
                report.add(
                    Severity.ERROR,
                    "archive_expanded_size",
                    "O pacote excede o limite seguro após descompressão.",
                )
            lower_names = {name.casefold() for name in archive.namelist()}
            if any(
                name.endswith("vbaproject.bin") or "/embeddings/" in name
                for name in lower_names
            ):
                report.add(
                    Severity.ERROR,
                    "active_or_embedded_content",
                    "Macros ou objetos incorporados não são aceitos.",
                )
    except (OSError, zipfile.BadZipFile):
        report.add(
            Severity.ERROR,
            "invalid_zip_package",
            "O pacote Office está corrompido.",
        )


def _check_image(payload: bytes, kind: FileKind, report: ValidationReport) -> None:
    expected = {
        FileKind.PNG: "PNG",
        FileKind.JPEG: "JPEG",
        FileKind.WEBP: "WEBP",
    }[kind]
    try:
        with Image.open(io.BytesIO(payload)) as image:
            actual = (image.format or "").upper()
            image.verify()
        if actual != expected:
            report.add(
                Severity.ERROR,
                "image_signature_mismatch",
                "A extensão e o conteúdo real da imagem são incompatíveis.",
            )
    except (UnidentifiedImageError, OSError, SyntaxError):
        report.add(
            Severity.ERROR,
            "invalid_image",
            "A imagem está corrompida ou não é suportada.",
        )


def _extension_matches_kind(extension: str, kind: FileKind) -> bool:
    expected: dict[str, set[FileKind]] = {
        ".xlsx": {FileKind.XLSX},
        ".docx": {FileKind.DOCX},
        ".doc": {FileKind.HTML_DOC, FileKind.OLE_DOC},
        ".png": {FileKind.PNG},
        ".jpg": {FileKind.JPEG},
        ".jpeg": {FileKind.JPEG},
        ".webp": {FileKind.WEBP},
    }
    return kind in expected.get(extension, set())


def validate_upload(
    source: str | Path | None,
    *,
    role: str,
    policy: UploadPolicy | None = None,
) -> tuple[ArtifactMetadata | None, ValidationReport]:
    """Validate one backend-stored upload and return safe metadata plus issues."""

    chosen_policy = policy or policy_for(role)
    report = ValidationReport()
    if source is None:
        if chosen_policy.required:
            report.add(
                Severity.ERROR,
                "required_file_missing",
                "Um arquivo obrigatório não foi informado.",
                field=role,
            )
        return None, report

    path = Path(source)
    try:
        if not path.is_file():
            raise OSError
        payload = path.read_bytes()
    except OSError:
        report.add(
            Severity.ERROR,
            "file_unavailable",
            "O arquivo enviado não está disponível para leitura.",
            field=role,
        )
        return None, report

    extension = path.suffix.lower()
    if extension not in chosen_policy.allowed_extensions:
        report.add(
            Severity.ERROR,
            "extension_not_allowed",
            "A extensão do arquivo não é aceita para este campo.",
            field=role,
        )
    size = len(payload)
    if size == 0:
        report.add(
            Severity.ERROR,
            "empty_file",
            "O arquivo enviado está vazio.",
            field=role,
        )
    elif size > chosen_policy.max_size_bytes:
        report.add(
            Severity.ERROR,
            "file_too_large",
            "O arquivo excede o limite de tamanho permitido.",
            field=role,
            details={"max_size_bytes": chosen_policy.max_size_bytes},
        )

    kind = detect_file_kind(payload)
    if kind not in chosen_policy.allowed_kinds:
        report.add(
            Severity.ERROR,
            "real_type_not_allowed",
            "O tipo real do arquivo não é aceito para este campo.",
            field=role,
        )
    if extension and kind != FileKind.UNKNOWN and not _extension_matches_kind(
        extension, kind
    ):
        report.add(
            Severity.ERROR,
            "extension_content_mismatch",
            "A extensão não corresponde ao conteúdo real do arquivo.",
            field=role,
        )
    if kind in {FileKind.DOCX, FileKind.XLSX}:
        _check_zip_safety(payload, report)
    if kind in {FileKind.PNG, FileKind.JPEG, FileKind.WEBP}:
        _check_image(payload, kind, report)
    if kind == FileKind.OLE_DOC:
        report.add(
            Severity.WARNING,
            "legacy_conversion_required",
            "O DOC binário será convertido em ambiente isolado, sem macros.",
            field=role,
        )
    if kind == FileKind.HTML_DOC and b"<script" in payload.lower():
        report.add(
            Severity.WARNING,
            "html_active_content_ignored",
            "Conteúdo ativo do HTML será ignorado durante a extração.",
            field=role,
        )

    metadata = ArtifactMetadata(
        artifact_id=f"{role}-{hashlib.sha256(payload).hexdigest()[:12]}",
        kind=kind,
        extension=extension or ".bin",
        media_type=_MIME_TYPES.get(kind),
        size_bytes=size,
        sha256=hashlib.sha256(payload).hexdigest(),
        original_filename=path.name,
        runtime_path=path,
    )
    return metadata, report


def _propagate_warnings(
    report: ValidationReport, code: str, warnings: Iterable[str]
) -> None:
    for index, warning in enumerate(warnings):
        report.add(
            Severity.WARNING,
            code,
            warning,
            source_id=f"{code}-{index + 1}",
        )


def validate_normalized_aep(
    data: NormalizedAEP, *, require_complete_reconciliation: bool = True
) -> ValidationReport:
    """Validate the unified model before document assembly."""

    report = ValidationReport()
    if not data.company.legal_name:
        report.add(
            Severity.ERROR,
            "legal_name_missing",
            "A razão social é obrigatória.",
            field="company.legal_name",
        )
    if data.company.registration_card is None:
        report.add(
            Severity.ERROR,
            "registration_card_missing",
            "O cartão CNPJ é obrigatório.",
            field="company.registration_card",
        )
    if not data.document.competence:
        report.add(
            Severity.ERROR,
            "competence_missing",
            "A competência do documento é obrigatória.",
            field="document.competence",
        )
    if not data.document.ergo_base_date:
        report.add(
            Severity.ERROR,
            "ergo_base_date_missing",
            "A data-base do Ergo é obrigatória.",
            field="document.ergo_base_date",
        )
    if not data.document.psychosocial_base_date:
        report.add(
            Severity.ERROR,
            "psychosocial_base_date_missing",
            "A data-base psicossocial é obrigatória.",
            field="document.psychosocial_base_date",
        )

    if not data.official_ghes:
        report.add(
            Severity.ERROR,
            "official_ghes_missing",
            "A planilha não forneceu GHEs oficiais.",
            field="official_ghes",
        )
    official_codes = [ghe.canonical_code for ghe in data.official_ghes]
    if len(official_codes) != len(set(official_codes)):
        report.add(
            Severity.ERROR,
            "duplicate_official_ghe",
            "Existem códigos de GHE duplicados na fonte oficial.",
            field="official_ghes",
        )
    for ghe in data.official_ghes:
        if ghe.population == 0:
            report.add(
                Severity.WARNING,
                "zero_ghe_population",
                "Um GHE oficial possui população igual a zero.",
                source_id=ghe.canonical_code,
            )
    if data.total_population <= 0:
        report.add(
            Severity.ERROR,
            "population_missing",
            "A população oficial total deve ser maior que zero.",
            field="official_ghes",
        )

    ergo_ids = {block.source_id for block in data.ergo.blocks}
    reconciliation_ids = {item.source_id for item in data.reconciliation.items}
    if not data.ergo.blocks:
        report.add(
            Severity.ERROR,
            "ergo_blocks_missing",
            "Nenhum bloco de GHE foi extraído do Ergo.",
            field="ergo.blocks",
        )
    if ergo_ids != reconciliation_ids:
        report.add(
            Severity.ERROR,
            "reconciliation_scope_mismatch",
            "A reconciliação não cobre exatamente os blocos do Ergo.",
            field="reconciliation",
        )
    for item in data.reconciliation.items:
        if (
            require_complete_reconciliation
            and item.status == ReconciliationStatus.NEEDS_REVIEW
        ):
            report.add(
                Severity.ERROR,
                "reconciliation_pending",
                "Uma divergência de GHE ainda requer decisão explícita.",
                source_id=item.source_id,
            )
        elif item.status == ReconciliationStatus.NOT_APPLICABLE:
            report.add(
                Severity.WARNING,
                "ergo_block_not_applicable",
                "Um bloco Ergo foi marcado explicitamente como não aplicável.",
                source_id=item.source_id,
            )
        if (
            item.official_ghe_code
            and canonical_ghe_code(item.official_ghe_code) not in official_codes
        ):
            report.add(
                Severity.ERROR,
                "reconciliation_target_unknown",
                "Uma decisão aponta para GHE não existente na planilha.",
                source_id=item.source_id,
            )

    if not data.psychosocial.images:
        report.add(
            Severity.ERROR,
            "psychosocial_images_missing",
            "Nenhuma imagem psicossocial foi extraída.",
            field="psychosocial.images",
        )
    if data.psychosocial.images and not any(
        image.role == ImageRole.GENERAL_PANEL
        for image in data.psychosocial.images
    ):
        report.add(
            Severity.WARNING,
            "general_panel_not_identified",
            "O painel psicossocial geral não foi identificado com segurança.",
            field="psychosocial.images",
        )
    source_coverage_severity = (
        Severity.ERROR
        if require_complete_reconciliation
        else Severity.WARNING
    )
    image_coverage = {
        canonical_ghe_code(image.official_ghe_code)
        for image in data.psychosocial.images
        if image.official_ghe_code
    }
    for code in official_codes:
        if code not in image_coverage:
            report.add(
                source_coverage_severity,
                "psychosocial_ghe_image_missing",
                "Um GHE oficial não possui imagem psicossocial associada.",
                source_id=code,
            )

    expected_roles = (
        ["integrated"]
        if data.technical.mode == AnalysisMode.INTEGRATED
        else ["psychosocial_agent", "ergonomic_agent"]
    )
    if sorted(data.technical.source_roles) != sorted(expected_roles):
        report.add(
            Severity.ERROR,
            "technical_mode_sources_invalid",
            "Os arquivos técnicos não correspondem ao modo de análise escolhido.",
            field="technical.source_roles",
        )
    if not data.technical.sections:
        report.add(
            Severity.ERROR,
            "technical_sections_missing",
            "Nenhum conteúdo aprovado foi extraído do relatório técnico.",
            field="technical.sections",
        )
    for ghe in data.official_ghes:
        code = ghe.canonical_code
        name = normalize_key(ghe.name)
        matching_analysis = next(
            (
                analysis
                for analysis in data.technical.analyses
                if canonical_ghe_code(
                    analysis.official_ghe_code or analysis.ghe_code_hint
                )
                == code
                or (
                    name
                    and name in normalize_key(analysis.ghe_name_hint or "")
                )
            ),
            None,
        )
        if matching_analysis is None or not (
            matching_analysis.sections
            or matching_analysis.technical_reading
        ):
            report.add(
                source_coverage_severity,
                "technical_ghe_analysis_missing",
                "Um GHE oficial não possui análise técnica aprovada associada.",
                source_id=code,
                field="technical.analyses",
            )
    categories = {section.category for section in data.technical.sections}
    for category, code, message in (
        (
            TechnicalCategory.PRIORITIZATION,
            "prioritization_missing",
            "A priorização aprovada não foi localizada.",
        ),
        (
            TechnicalCategory.ACTION_PLAN,
            "action_plan_missing",
            "O plano de ação aprovado não foi localizado.",
        ),
        (
            TechnicalCategory.CONCLUSION,
            "technical_conclusion_missing",
            "A conclusão técnica aprovada não foi localizada.",
        ),
    ):
        if category not in categories:
            report.add(
                Severity.ERROR,
                code,
                message,
                field="technical.sections",
            )
    if TechnicalCategory.ACTION_PLAN in categories and not data.technical.action_plan:
        report.add(
            source_coverage_severity,
            "action_plan_rows_missing",
            "A seção de plano de ação não contém itens extraíveis.",
            field="technical.action_plan",
        )
    if (
        TechnicalCategory.PRIORITIZATION in categories
        and not data.technical.priorities
    ):
        report.add(
            source_coverage_severity,
            "prioritization_rows_missing",
            "A seção de priorização não contém itens extraíveis.",
            field="technical.priorities",
        )
    if TechnicalCategory.CONCLUSION in categories and not data.technical.conclusion:
        report.add(
            source_coverage_severity,
            "conclusion_text_missing",
            "A seção de conclusão não contém texto extraível.",
            field="technical.conclusion",
        )

    compatibility = data.document.compatibility
    if compatibility is not None:
        report.add(
            Severity.WARNING,
            "private_compatibility_mode",
            "Uma exceção privada de compatibilidade do piloto está registrada.",
            field="document.compatibility",
            details={
                "mode": compatibility.mode,
                "included_ergo_source_ids": compatibility.included_ergo_source_ids,
                "omitted_ergo_source_ids": compatibility.omitted_ergo_source_ids,
            },
        )
        if not compatibility.acknowledged:
            report.add(
                Severity.ERROR,
                "compatibility_not_acknowledged",
                "A exceção privada precisa de confirmação antes da geração.",
                field="document.compatibility.acknowledged",
            )
        referenced = set(compatibility.included_ergo_source_ids) | set(
            compatibility.omitted_ergo_source_ids
        )
        if not referenced <= ergo_ids:
            report.add(
                Severity.ERROR,
                "compatibility_unknown_ergo_block",
                "A exceção privada referencia um bloco Ergo inexistente.",
                field="document.compatibility",
            )

    _propagate_warnings(report, "ergo_extraction_warning", data.ergo.warnings)
    _propagate_warnings(
        report, "psychosocial_extraction_warning", data.psychosocial.warnings
    )
    _propagate_warnings(
        report, "technical_extraction_warning", data.technical.warnings
    )
    _propagate_warnings(
        report, "reconciliation_warning", data.reconciliation.warnings
    )
    return report
