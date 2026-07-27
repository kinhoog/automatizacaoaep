"""End-to-end local compilation pipeline used by HTTP routes and pilot scripts."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, UnidentifiedImageError

from app.config import Settings, settings as default_settings
from app.extractors import (
    extract_ergo,
    extract_ghes,
    extract_psychosocial,
    extract_technical_report,
)
from app.models import (
    AnalysisMode,
    CompanyData,
    CompatibilityException,
    DocumentData,
    GHE,
    ImageAsset,
    ImageRole,
    NormalizedAEP,
    PsychosocialBlock,
    PsychosocialReport,
    TechnicalAnalysis,
    TechnicalReport,
)
from app.services.document_assembler import DocumentAssembler
from app.services.document_renderer import (
    LegacyConversionError,
    convert_legacy_doc_to_docx,
    render_docx,
)
from app.services.file_security import UploadValidationError, inspect_file
from app.services.job_store import JobNotFoundError, JobRecord, JobStore
from app.services.reconciliation import (
    apply_reconciliation_decisions,
    build_reconciliation_plan,
)
from app.services.validation import validate_normalized_aep


class PipelineError(RuntimeError):
    """Safe user-facing pipeline error without internal filesystem paths."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pipeline_error",
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field = field


_OLE_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")


class CompatibilityProfileError(PipelineError):
    """Safe failure raised when the private pilot profile cannot be applied."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)


_FILE_ALIASES: dict[str, tuple[str, ...]] = {
    "ghe": ("ghe", "ghe_spreadsheet", "ghe_file"),
    "psychosocial_raw": (
        "psychosocial_raw",
        "psychosocial_report",
        "psico_raw",
    ),
    "ergo_raw": ("ergo_raw", "ergo_report", "ergo"),
    "technical_integrated": (
        "technical_integrated",
        "integrated_report",
        "technical_report",
    ),
    "psychosocial_analysis": (
        "psychosocial_analysis",
        "psychosocial_agent",
    ),
    "ergonomic_analysis": ("ergonomic_analysis", "ergonomic_agent"),
    "cnpj_card": ("cnpj_card", "registration_card", "company_card"),
    "logo": ("logo", "company_logo"),
}


def _first_file(
    files: Mapping[str, Path],
    role: str,
    *,
    required: bool = True,
) -> Path | None:
    for alias in _FILE_ALIASES[role]:
        candidate = files.get(alias)
        if candidate is not None:
            path = Path(candidate)
            if path.is_file():
                return path
    if required:
        raise PipelineError(f"O arquivo obrigatório “{role}” não foi recebido.")
    return None


def _image_asset(path: Path, image_id: str, role: ImageRole) -> ImageAsset:
    payload = path.read_bytes()
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            width, height = image.size
            image_format = (image.format or "").upper()
            content_type = Image.MIME.get(image.format or "")
    except (OSError, UnidentifiedImageError) as exc:
        raise PipelineError("Uma das imagens obrigatórias é inválida.") from exc
    return ImageAsset(
        image_id=image_id,
        order=0,
        role=role,
        sha256=hashlib.sha256(payload).hexdigest(),
        width_px=width,
        height_px=height,
        image_format=image_format,
        content_type=content_type,
        blob=payload,
        runtime_path=path,
    )


def _extract_ergo_with_legacy_conversion(
    source: Path,
    settings: Settings,
):
    try:
        signature = source.read_bytes()[: len(_OLE_SIGNATURE)]
    except OSError as exc:
        raise PipelineError("Não foi possível ler o relatório Ergo.") from exc
    if signature != _OLE_SIGNATURE:
        return extract_ergo(source)

    conversion_dir = source.parent / "_converted_ergo"
    converted: Path | None = None
    try:
        converted = convert_legacy_doc_to_docx(
            source,
            conversion_dir,
            libreoffice_path=settings.libreoffice_path,
        )
        inspect_file(converted, converted.name)
        return extract_ergo(converted)
    except (LegacyConversionError, UploadValidationError) as exc:
        raise PipelineError(str(exc)) from exc
    finally:
        if converted is not None:
            converted.unlink(missing_ok=True)
        try:
            conversion_dir.rmdir()
        except OSError:
            pass


def _assign_image_roles(
    images: list[ImageAsset],
    *,
    general: bool,
) -> None:
    for index, image in enumerate(images):
        ratio = (
            image.width_px / image.height_px
            if image.width_px and image.height_px
            else 0
        )
        if index == 0:
            image.role = (
                ImageRole.GENERAL_PANEL if general else ImageRole.GHE_PANEL
            )
        elif index == 1:
            image.role = ImageRole.CHART
        elif index == 2:
            image.role = ImageRole.DOMAIN_SUMMARY
        elif ratio >= 3 and index in {3, 4}:
            image.role = ImageRole.RISK_MATRIX
        else:
            image.role = ImageRole.OTHER


def _split_positional_psychosocial(
    report: PsychosocialReport,
    official_ghes: Sequence[GHE],
) -> PsychosocialReport:
    """Recover image-only reports using dashboard boundaries and visual order."""

    if len(report.blocks) > 1 or len(report.images) < 2:
        return report
    images = sorted(report.images, key=lambda image: image.order)
    dashboard_indexes = [
        index
        for index, image in enumerate(images)
        if image.width_px
        and image.height_px
        and 2.5 <= image.width_px / image.height_px <= 3.5
        and 350 <= image.height_px <= 700
    ]
    if len(dashboard_indexes) < 2:
        return report
    starts = dashboard_indexes
    segments = [
        images[start : (starts[index + 1] if index + 1 < len(starts) else len(images))]
        for index, start in enumerate(starts)
    ]
    blocks: list[PsychosocialBlock] = []
    general_images = segments[0]
    _assign_image_roles(general_images, general=True)
    blocks.append(
        PsychosocialBlock(
            block_id="psico-general",
            order=0,
            title="Painel geral",
            images=general_images,
        )
    )
    for index, segment in enumerate(segments[1:]):
        if index >= len(official_ghes):
            report.warnings.append(
                "Há um bloco psicossocial posicional sem GHE oficial disponível."
            )
            break
        ghe = official_ghes[index]
        _assign_image_roles(segment, general=False)
        for image in segment:
            image.ghe_code_hint = ghe.canonical_code
            image.ghe_name_hint = ghe.name
            image.official_ghe_code = ghe.canonical_code
        blocks.append(
            PsychosocialBlock(
                block_id=f"psico-ghe-{index + 1:03d}",
                order=index + 1,
                title=f"{ghe.canonical_code} — {ghe.name}",
                ghe_code_hint=ghe.canonical_code,
                ghe_name_hint=ghe.name,
                official_ghe_code=ghe.canonical_code,
                images=segment,
            )
        )
    result = report.model_copy(deep=True)
    result.blocks = blocks
    result.images = [image for block in blocks for image in block.images]
    result.warnings.append(
        "Relatório sem títulos textuais: imagens associadas por ordem visual "
        "dos painéis e revisáveis pelo código oficial."
    )
    return result


def _merge_technical_analyses(
    report: TechnicalReport,
) -> TechnicalReport:
    """Merge split sections that refer to the same GHE without changing prose."""

    grouped: dict[str, TechnicalAnalysis] = {}
    order: list[str] = []
    loose: list[TechnicalAnalysis] = []
    for analysis in report.analyses:
        code = analysis.official_ghe_code or analysis.ghe_code_hint
        if not code:
            loose.append(analysis)
            continue
        key = code.strip().upper()
        if key not in grouped:
            grouped[key] = analysis.model_copy(deep=True)
            order.append(key)
            continue
        target = grouped[key]
        target.sections.extend(
            section.model_copy(deep=True) for section in analysis.sections
        )
        target.technical_reading.extend(analysis.technical_reading)
        target.favorable_percentage = (
            target.favorable_percentage or analysis.favorable_percentage
        )
        target.classification = target.classification or analysis.classification
    result = report.model_copy(deep=True)
    result.analyses = [grouped[key] for key in order] + loose
    for index, analysis in enumerate(result.analyses):
        analysis.order = index
    return result


def _compatibility_input_fingerprint(
    files: Mapping[str, Path],
    analysis_mode: str,
) -> str:
    """Bind a private compatibility profile to one exact set of source files."""

    roles = ["ghe", "psychosocial_raw", "ergo_raw", "cnpj_card"]
    if analysis_mode == AnalysisMode.SEPARATE.value:
        roles.extend(("psychosocial_analysis", "ergonomic_analysis"))
    else:
        roles.append("technical_integrated")
    sources: list[dict[str, str]] = []
    for role in roles:
        path = _first_file(files, role)
        sources.append(
            {
                "role": role,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    canonical = json.dumps(
        {"analysis_mode": analysis_mode, "sources": sources},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _profile_ordinals(profile: Mapping[str, Any], key: str) -> list[int]:
    value = profile.get(key)
    if not isinstance(value, list) or any(type(item) is not int for item in value):
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        )
    ordinals = list(value)
    if any(item < 1 for item in ordinals) or len(ordinals) != len(set(ordinals)):
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        )
    return ordinals


def _load_compatibility_profile(settings: Settings) -> Mapping[str, Any]:
    path = settings.compatibility_profile_path
    if path is None:
        raise CompatibilityProfileError(
            "O modo de compatibilidade não está configurado nesta instalação.",
            code="compatibility_profile_unavailable",
        )
    private_root = (settings.base_dir / "private_templates").resolve()
    hosted_root = settings.trusted_private_runtime_dir
    trusted_roots = [private_root]
    if hosted_root is not None:
        trusted_roots.append(hosted_root.resolve())
    resolved = path.resolve()
    if (
        path.is_symlink()
        or not any(
            resolved != trusted_root and trusted_root in resolved.parents
            for trusted_root in trusted_roots
        )
        or not resolved.is_file()
    ):
        raise CompatibilityProfileError(
            "O modo de compatibilidade não está configurado nesta instalação.",
            code="compatibility_profile_unavailable",
        )
    try:
        if resolved.stat().st_size > 64 * 1024:
            raise ValueError("profile too large")
        profile = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        ) from exc
    if not isinstance(profile, Mapping):
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        )
    return profile


def _compatibility(
    enabled: bool,
    acknowledged: bool,
    files: Mapping[str, Path],
    analysis_mode: str,
    source_ids: list[str],
    settings: Settings,
) -> CompatibilityException | None:
    if not enabled:
        return None
    profile = _load_compatibility_profile(settings)
    expected_fingerprint = profile.get("input_fingerprint")
    if (
        profile.get("schema_version") != 1
        or profile.get("mode") != "pilot_reference"
        or profile.get("analysis_mode") != analysis_mode
        or not isinstance(expected_fingerprint, str)
        or len(expected_fingerprint) != 64
    ):
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        )
    actual_fingerprint = _compatibility_input_fingerprint(files, analysis_mode)
    if not hmac.compare_digest(
        expected_fingerprint.casefold(), actual_fingerprint
    ):
        raise CompatibilityProfileError(
            "Os arquivos não correspondem ao perfil privado de compatibilidade.",
            code="compatibility_profile_mismatch",
        )
    included_ordinals = _profile_ordinals(
        profile, "included_ergo_ordinals"
    )
    omitted_ordinals = _profile_ordinals(profile, "omitted_ergo_ordinals")
    all_ordinals = included_ordinals + omitted_ordinals
    if (
        not included_ordinals
        or len(all_ordinals) != len(set(all_ordinals))
        or set(all_ordinals) != set(range(1, len(source_ids) + 1))
    ):
        raise CompatibilityProfileError(
            "O perfil privado de compatibilidade é inválido.",
            code="compatibility_profile_invalid",
        )
    return CompatibilityException(
        mode="pilot_reference",
        reason=(
            "Compatibilidade explícita com o gabarito local: a seleção visual "
            "histórica difere da reconciliação oficial e permanece registrada."
        ),
        included_ergo_source_ids=[
            source_ids[ordinal - 1] for ordinal in included_ordinals
        ],
        omitted_ergo_source_ids=[
            source_ids[ordinal - 1] for ordinal in omitted_ordinals
        ],
        acknowledged=acknowledged,
    )


class DocumentPipeline:
    """Stateful local pipeline. Job IDs are random and paths are server-owned."""

    def __init__(
        self,
        settings: Settings | None = None,
        store: JobStore | None = None,
    ):
        self.settings = settings or default_settings
        self.store = store or JobStore(self.settings)
        self._external_models: dict[str, NormalizedAEP] = {}
        self._external_dirs: dict[str, Path] = {}
        self._external_files: dict[str, dict[str, Path]] = {}

    def create_job(self) -> JobRecord:
        self.store.cleanup_expired()
        return self.store.create()

    def discard(self, job_id: str) -> None:
        """Descarta referências em memória mantidas pelo adaptador HTTP."""

        self._external_models.pop(job_id, None)
        self._external_dirs.pop(job_id, None)
        self._external_files.pop(job_id, None)

    def build_model(
        self,
        files: Mapping[str, Path],
        payload: Mapping[str, Any],
    ) -> NormalizedAEP:
        try:
            try:
                ghe_result = extract_ghes(_first_file(files, "ghe"))
            except Exception as exc:
                raise PipelineError(
                    "A planilha oficial dos GHEs não pôde ser lida. "
                    "Confira se o arquivo correto foi colocado nesse campo.",
                    code="ghe_spreadsheet_invalid",
                    field="ghe_spreadsheet",
                ) from exc
            ghes = ghe_result.ghes
            if not ghes:
                raise PipelineError(
                    "A planilha enviada não contém GHEs utilizáveis.",
                    code="ghe_spreadsheet_empty",
                    field="ghe_spreadsheet",
                )

            try:
                ergo_path = _first_file(files, "ergo_raw")
                assert ergo_path is not None
                ergo = _extract_ergo_with_legacy_conversion(
                    ergo_path,
                    self.settings,
                )
            except Exception as exc:
                raise PipelineError(
                    "O relatório Ergo bruto não pôde ser lido. "
                    "Confira se o arquivo correto foi colocado nesse campo.",
                    code="ergo_report_invalid",
                    field="ergo_report",
                ) from exc
            if not ergo.blocks:
                raise PipelineError(
                    "O relatório Ergo bruto não contém os blocos esperados.",
                    code="ergo_report_empty",
                    field="ergo_report",
                )

            try:
                psychosocial = extract_psychosocial(
                    _first_file(files, "psychosocial_raw"), ghes
                )
            except Exception as exc:
                raise PipelineError(
                    "O relatório psicossocial bruto não pôde ser lido. "
                    "Confira se os relatórios DOCX não foram trocados.",
                    code="psychosocial_report_invalid",
                    field="psychosocial_report",
                ) from exc
            if not psychosocial.images:
                raise PipelineError(
                    "O relatório psicossocial bruto não contém os painéis "
                    "e imagens esperados. Confira se os relatórios DOCX "
                    "não foram trocados.",
                    code="psychosocial_report_empty",
                    field="psychosocial_report",
                )
            psychosocial = _split_positional_psychosocial(psychosocial, ghes)

            mode = str(payload.get("analysis_mode", "integrated")).casefold()
            technical_field = (
                None
                if mode == AnalysisMode.SEPARATE.value
                else "integrated_report"
            )
            try:
                if mode == AnalysisMode.SEPARATE.value:
                    technical = extract_technical_report(
                        psychosocial_path=_first_file(
                            files, "psychosocial_analysis"
                        ),
                        ergonomic_path=_first_file(
                            files, "ergonomic_analysis"
                        ),
                    )
                else:
                    technical = extract_technical_report(
                        integrated_path=_first_file(
                            files, "technical_integrated"
                        )
                    )
            except Exception as exc:
                raise PipelineError(
                    "A análise técnica aprovada não pôde ser lida. "
                    "Confira os arquivos selecionados para essa etapa.",
                    code="technical_report_invalid",
                    field=technical_field,
                ) from exc
            if not any(
                (
                    technical.sections,
                    technical.analyses,
                    technical.priorities,
                    technical.action_plan,
                    technical.conclusion,
                )
            ):
                raise PipelineError(
                    "A análise técnica enviada não contém os blocos "
                    "aprovados esperados. Confira se os relatórios DOCX "
                    "não foram trocados.",
                    code="technical_report_empty",
                    field=technical_field,
                )
            technical = _merge_technical_analyses(technical)

            try:
                card_path = _first_file(files, "cnpj_card")
                registration_card = _image_asset(
                    card_path, "registration-card", ImageRole.OTHER
                )
            except Exception as exc:
                raise PipelineError(
                    "A imagem do cartão CNPJ não pôde ser lida.",
                    code="cnpj_card_invalid",
                    field="cnpj_card",
                ) from exc
            logo_path = _first_file(files, "logo", required=False)
            try:
                company_logo = (
                    _image_asset(logo_path, "company-logo", ImageRole.OTHER)
                    if logo_path
                    else None
                )
            except Exception as exc:
                raise PipelineError(
                    "A imagem da logo opcional não pôde ser lida.",
                    code="company_logo_invalid",
                    field="company_logo",
                ) from exc
            company = CompanyData(
                legal_name=str(
                    payload.get("legal_name")
                    or payload.get("company_name")
                    or ""
                ).strip(),
                registration_card=registration_card,
                logo=company_logo,
            )
            source_ids = [block.source_id for block in ergo.blocks]
            compatibility = _compatibility(
                bool(payload.get("compatibility_mode", False)),
                bool(
                    payload.get(
                        "compatibility_acknowledged",
                        payload.get("compatibility_mode", False),
                    )
                ),
                files,
                mode,
                source_ids,
                self.settings,
            )
            document = DocumentData(
                competence=str(payload.get("competence", "")).strip(),
                ergo_base_date=str(
                    payload.get("ergo_base_date")
                    or payload.get("ergo_reference_date")
                    or ""
                ).strip(),
                psychosocial_base_date=str(
                    payload.get("psychosocial_base_date")
                    or payload.get("psychosocial_reference_date")
                    or ""
                ).strip(),
                compatibility=compatibility,
            )
            reconciliation = build_reconciliation_plan(ergo.blocks, ghes)
            model = NormalizedAEP(
                company=company,
                document=document,
                official_ghes=ghes,
                ergo=ergo,
                psychosocial=psychosocial,
                technical=technical,
                reconciliation=reconciliation,
            )
            model.validation = validate_normalized_aep(
                model, require_complete_reconciliation=False
            )
            return model
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "Não foi possível organizar os dados extraídos dos arquivos.",
                code="normalization_failed",
            ) from exc

    def validate(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            record = self.store.get(job_id)
        except JobNotFoundError:
            return self._validate_external(job_id, payload)
        self.store.update(
            job_id, status="processing", stage="extraindo", progress=25
        )
        try:
            model = self.build_model(record.files, payload)
            record.normalized = model
            report_payload = {
                "job_id": job_id,
                "validation": model.validation.audit_dict(),
                "official_ghes": [
                    ghe.audit_dict() for ghe in model.official_ghes
                ],
                "total_population": model.total_population,
                "reconciliation": model.reconciliation.audit_dict(),
                "compatibility": (
                    model.document.compatibility.audit_dict()
                    if model.document.compatibility
                    else None
                ),
            }
            record.validation_payload = report_payload
            report_path = record.output_dir / "validation-report.json"
            report_path.write_text(
                json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            record.validation_report_path = report_path
            self.store.update(
                job_id,
                status="validated",
                stage="reconciliação",
                progress=60,
            )
            return report_payload
        except Exception as exc:
            self.store.update(
                job_id,
                status="failed",
                stage="falha de validação",
                progress=100,
                error=(
                    str(exc)
                    if isinstance(exc, PipelineError)
                    else "Falha ao validar os arquivos."
                ),
            )
            raise

    def _validate_external(
        self,
        job_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw_files = payload.get("files") or payload.get("paths")
        fields = payload.get("fields") or payload.get("data") or payload
        job_dir_value = payload.get("job_dir")
        if not isinstance(raw_files, Mapping) or not job_dir_value:
            raise PipelineError("Os arquivos temporários da execução não estão disponíveis.")
        job_dir = Path(job_dir_value).resolve()
        files = {str(key): Path(value).resolve() for key, value in raw_files.items()}
        for path in files.values():
            try:
                path.relative_to(job_dir)
            except ValueError as exc:
                raise PipelineError("Um caminho temporário saiu da área da execução.") from exc
        model = self.build_model(files, fields if isinstance(fields, Mapping) else {})
        self._external_models[job_id] = model
        self._external_dirs[job_id] = job_dir
        self._external_files[job_id] = files
        result = {
            "job_id": job_id,
            "validation": model.validation.audit_dict(),
            "warnings": [
                issue.audit_dict() for issue in model.validation.warnings
            ],
            "errors": [
                issue.audit_dict() for issue in model.validation.errors
            ],
            "official_ghes": [
                ghe.audit_dict() for ghe in model.official_ghes
            ],
            "total_population": model.total_population,
            "reconciliation": model.reconciliation.audit_dict(),
            "compatibility": (
                model.document.compatibility.audit_dict()
                if model.document.compatibility
                else None
            ),
        }
        (job_dir / "pipeline-validation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    def generate(
        self,
        job_id: str,
        payload: Mapping[str, Any] | None = None,
        reconciliation: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            record = self.store.get(job_id)
        except JobNotFoundError:
            return self._generate_external(
                job_id, payload or {}, reconciliation or []
            )
        if record.normalized is None:
            raise PipelineError("A execução precisa ser validada antes da geração.")
        model: NormalizedAEP = record.normalized.model_copy(deep=True)
        requested_compatibility = bool(
            (payload or {}).get("compatibility_mode", False)
        )
        if requested_compatibility != (model.document.compatibility is not None):
            raise PipelineError(
                "O modo de compatibilidade mudou após a validação. "
                "Valide os arquivos novamente."
            )
        decisions = self._normalize_decisions(reconciliation or [])
        if decisions:
            model.reconciliation = apply_reconciliation_decisions(
                model.reconciliation, decisions, model.official_ghes
            )
        if model.document.compatibility and payload:
            if payload.get("compatibility_acknowledged") is not None:
                model.document.compatibility.acknowledged = bool(
                    payload["compatibility_acknowledged"]
                )
        model.validation = validate_normalized_aep(
            model, require_complete_reconciliation=True
        )
        if not model.validation.valid:
            codes = ", ".join(issue.code for issue in model.validation.errors)
            raise PipelineError(
                "A geração foi bloqueada pelas validações pendentes"
                + (f": {codes}" if codes else ".")
            )
        self.store.update(
            job_id, status="processing", stage="montando Word", progress=75
        )
        output = record.output_dir / "Documento AEP - AUTOMATICO.docx"
        assembler = DocumentAssembler(
            self.settings.template_path
            if self.settings.template_path.is_file()
            else None,
            self.settings.template_manifest_path
            if self.settings.template_manifest_path.is_file()
            else None,
        )
        assembler.assemble(model, output)
        record.document_path = output
        record.normalized = model
        record.validation_payload = {
            **(record.validation_payload or {}),
            "validation": model.validation.audit_dict(),
            "reconciliation": model.reconciliation.audit_dict(),
            "compatibility": (
                model.document.compatibility.audit_dict()
                if model.document.compatibility
                else None
            ),
        }
        if record.validation_report_path:
            record.validation_report_path.write_text(
                json.dumps(
                    record.validation_payload, ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
        render_info: dict[str, Any] | None = None
        if self.settings.render_on_generate:
            result = render_docx(
                output,
                record.work_dir / "render",
                libreoffice_path=self.settings.libreoffice_path,
            )
            render_info = {
                "renderer": result.renderer,
                "pages": len(result.page_images),
                "warnings": result.warnings,
            }
        self.store.remove_inputs(job_id)
        self.store.update(
            job_id, status="completed", stage="concluído", progress=100
        )
        return {
            **record.public_dict(),
            "document_path": str(output),
            "validation_report_path": (
                str(record.validation_report_path)
                if record.validation_report_path
                else None
            ),
            "render": render_info,
            "compatibility": record.validation_payload["compatibility"],
        }

    @staticmethod
    def _normalize_decisions(
        reconciliation: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if isinstance(reconciliation, Mapping):
            raw_items = (
                reconciliation.get("items")
                or reconciliation.get("decisions")
                or [reconciliation]
            )
        else:
            raw_items = reconciliation
        decisions: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            action = str(item.get("action") or "").casefold()
            not_applicable = bool(
                item.get("not_applicable") or action == "not_applicable"
            )
            official = (
                item.get("official_ghe_code")
                or item.get("official_ghe_id")
                or item.get("official_ghe")
            )
            decisions.append(
                {
                    "source_id": item.get("source_id") or item.get("ergo_id"),
                    "official_ghe_code": None if not_applicable else official,
                    "not_applicable": not_applicable,
                    "reason": str(
                        item.get("reason")
                        or "Decisão confirmada na interface de reconciliação."
                    ),
                }
            )
        return decisions

    def _generate_external(
        self,
        job_id: str,
        payload: Mapping[str, Any],
        reconciliation: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    ) -> dict[str, Any]:
        source_model = self._external_models.get(job_id)
        job_dir = self._external_dirs.get(job_id)
        if source_model is None or job_dir is None:
            raise PipelineError("A execução precisa ser validada antes da geração.")
        model = source_model.model_copy(deep=True)
        fields = payload.get("fields") if isinstance(payload, Mapping) else None
        compatibility_value = (
            fields.get("compatibility_mode")
            if isinstance(fields, Mapping)
            else payload.get("compatibility_mode")
        )
        requested_compatibility = bool(compatibility_value)
        if requested_compatibility != (model.document.compatibility is not None):
            raise PipelineError(
                "O modo de compatibilidade mudou após a validação. "
                "Valide os arquivos novamente."
            )
        decisions = self._normalize_decisions(reconciliation)
        if decisions:
            model.reconciliation = apply_reconciliation_decisions(
                model.reconciliation, decisions, model.official_ghes
            )
        if model.document.compatibility and compatibility_value is not None:
            model.document.compatibility.acknowledged = bool(compatibility_value)
        model.validation = validate_normalized_aep(
            model, require_complete_reconciliation=True
        )
        if not model.validation.valid:
            codes = ", ".join(issue.code for issue in model.validation.errors)
            raise PipelineError(
                f"A geração foi bloqueada pelas validações pendentes: {codes}."
            )
        output = job_dir / "Documento AEP - AUTOMATICO.docx"
        DocumentAssembler(
            self.settings.template_path
            if self.settings.template_path.is_file()
            else None,
            self.settings.template_manifest_path
            if self.settings.template_manifest_path.is_file()
            else None,
        ).assemble(model, output)
        report = {
            "job_id": job_id,
            "validation": model.validation.audit_dict(),
            "official_ghes": [
                ghe.audit_dict() for ghe in model.official_ghes
            ],
            "total_population": model.total_population,
            "reconciliation": model.reconciliation.audit_dict(),
            "compatibility": (
                model.document.compatibility.audit_dict()
                if model.document.compatibility
                else None
            ),
        }
        report_path = job_dir / "pipeline-validation.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for path in self._external_files.get(job_id, {}).values():
            try:
                path.relative_to(job_dir)
            except ValueError:
                continue
            if path.is_file() and path not in {output, report_path}:
                path.unlink()
        self._external_models[job_id] = model
        return {
            "document_path": str(output),
            "validation_report_path": str(report_path),
            "validation": model.validation.audit_dict(),
            "warnings": [
                issue.audit_dict() for issue in model.validation.warnings
            ],
            "errors": [
                issue.audit_dict() for issue in model.validation.errors
            ],
            "reconciliation": model.reconciliation.audit_dict(),
            "compatibility": report["compatibility"],
        }
