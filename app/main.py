"""Aplicação web local do Automatizador de Documentos AEP.

Este módulo mantém a camada HTTP deliberadamente fina: ele valida e armazena
uploads em uma área isolada por execução, controla o ciclo de vida dos jobs e
delega a leitura técnica e a montagem do Word a ``DocumentPipeline``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import shutil
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterable, Mapping

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.config import settings as default_settings
from app.services.file_security import (
    UploadValidationError,
    inspect_file,
    sha256_file,
)
from app.services.hosted_template import (
    HostedTemplateError,
    materialize_hosted_template,
    remove_materialized_template,
)

try:
    from app.services.pipeline import DocumentPipeline
except ImportError:  # A API também pode ser importada durante o bootstrap.
    DocumentPipeline = None  # type: ignore[assignment,misc]

PIPELINE_INSTANCE: Any | None = None
PIPELINE_STARTUP_ERROR: str | None = None
HOSTED_TEMPLATE_RUNTIME_DIR: Path | None = None
DEFAULT_PIPELINE_OWNED = False


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
STATIC_DIR = APP_DIR / "static"
RUNTIME_ROOT = (
    default_settings.runtime_dir
    or Path(os.getenv("AEP_RUNTIME_DIR", str(PROJECT_ROOT / "uploads")))
).expanduser()
MAX_FILE_SIZE = max(1, int(os.getenv("AEP_MAX_FILE_MB", "25"))) * 1024 * 1024
MAX_REQUEST_SIZE = max(
    MAX_FILE_SIZE, int(os.getenv("AEP_MAX_REQUEST_MB", "250")) * 1024 * 1024
)
MAX_FORM_FIELDS = 16
MAX_TEXT_PART_SIZE = 16 * 1024
JOB_TTL_SECONDS = default_settings.job_ttl_seconds
ALLOWED_ORIGINS = default_settings.allowed_origins
REQUIRE_ORIGIN = default_settings.require_origin
JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")

logger = logging.getLogger("aep.web")


@dataclass(slots=True)
class UploadSpec:
    aliases: tuple[str, ...]
    extensions: frozenset[str]
    required: bool = False


UPLOAD_SPECS: dict[str, UploadSpec] = {
    "ghe_spreadsheet": UploadSpec(
        ("ghe_spreadsheet", "ghe_file", "planilha_ghes", "ghe_planilha"),
        frozenset({".xlsx"}),
        required=True,
    ),
    "psychosocial_report": UploadSpec(
        (
            "psychosocial_report",
            "psychosocial_raw_report",
            "relatorio_psicossocial",
        ),
        frozenset({".docx"}),
        required=True,
    ),
    "ergo_report": UploadSpec(
        ("ergo_report", "ergo_raw_report", "relatorio_ergo"),
        frozenset({".doc", ".docx"}),
        required=True,
    ),
    "integrated_report": UploadSpec(
        ("integrated_report", "technical_report", "relatorio_integrado"),
        frozenset({".docx"}),
    ),
    "psychosocial_analysis": UploadSpec(
        (
            "psychosocial_analysis",
            "agent1_report",
            "analise_psicossocial",
        ),
        frozenset({".docx"}),
    ),
    "ergonomic_analysis": UploadSpec(
        ("ergonomic_analysis", "agent2_report", "analise_ergonomica"),
        frozenset({".docx"}),
    ),
    "cnpj_card": UploadSpec(
        ("cnpj_card", "cnpj_image", "cartao_cnpj"),
        frozenset({".png", ".jpg", ".jpeg", ".webp"}),
        required=True,
    ),
    "company_logo": UploadSpec(
        ("company_logo", "logo", "logo_empresa"),
        frozenset({".png", ".jpg", ".jpeg", ".webp"}),
    ),
}
MAX_UPLOAD_FILES = len(UPLOAD_SPECS)

TEXT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "company_name": ("company_name", "legal_name", "razao_social"),
    "competence": ("competence", "competencia"),
    "ergo_reference_date": (
        "ergo_reference_date",
        "ergo_base_date",
        "data_base_ergo",
    ),
    "psychosocial_reference_date": (
        "psychosocial_reference_date",
        "psychosocial_base_date",
        "data_base_psicossocial",
    ),
    "analysis_mode": ("analysis_mode", "modo_analise"),
    "compatibility_mode": ("compatibility_mode", "modo_compatibilidade"),
}

REQUIRED_TEXT_FIELDS = {
    "company_name",
    "competence",
    "ergo_reference_date",
    "psychosocial_reference_date",
}


class _MultipartLimitError(MultiPartException):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class _BoundedMultiPartParser(MultiPartParser):
    """Aplica limites aos arquivos antes de gravá-los no spool temporário."""

    def __init__(self, *args: Any, max_file_size: int, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._max_file_size = max_file_size
        self._current_file_size = 0

    def on_part_begin(self) -> None:
        self._current_file_size = 0
        super().on_part_begin()

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._current_part.file is not None:
            self._current_file_size += end - start
            if self._current_file_size > self._max_file_size:
                raise _MultipartLimitError(
                    "Um dos arquivos excede o limite permitido.",
                    code="file_too_large",
                )
        super().on_part_data(data, start, end)


async def _bounded_request_stream(request: Request):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_REQUEST_SIZE:
            raise _MultipartLimitError(
                "O conjunto de arquivos excede o limite permitido.",
                code="request_too_large",
            )
        yield chunk


async def _parse_bounded_multipart(request: Request):
    parser = _BoundedMultiPartParser(
        request.headers,
        _bounded_request_stream(request),
        max_files=MAX_UPLOAD_FILES,
        max_fields=MAX_FORM_FIELDS,
        max_part_size=MAX_TEXT_PART_SIZE,
        max_file_size=MAX_FILE_SIZE,
    )
    try:
        return await parser.parse()
    except _MultipartLimitError as exc:
        _raise_form_error(
            str(exc),
            code=exc.code,
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    except MultiPartException as exc:
        message = str(exc)
        is_limit = "maximum" in message.casefold() or "exceeded" in message.casefold()
        _raise_form_error(
            (
                "O formulário excede a quantidade ou o tamanho permitidos."
                if is_limit
                else "Não foi possível interpretar o formulário enviado."
            ),
            code="multipart_limit" if is_limit else "malformed_multipart",
            http_status=(
                status.HTTP_413_CONTENT_TOO_LARGE
                if is_limit
                else status.HTTP_400_BAD_REQUEST
            ),
        )


@dataclass(slots=True)
class JobRecord:
    job_id: str
    job_dir: Path
    created_at: float
    updated_at: float
    status: str = "receiving"
    stage: str = "Recebendo arquivos"
    progress: int = 5
    fields: dict[str, Any] = field(default_factory=dict)
    files: dict[str, Path] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    compatibility: dict[str, Any] | None = None
    document_path: Path | None = None
    validation_report_path: Path | None = None
    error_message: str | None = None
    active_downloads: int = 0

    def touch(self) -> None:
        self.updated_at = time.time()


class JobDeletionOutcome(str, Enum):
    """Resultado interno da tentativa atômica de excluir um job."""

    DELETED = "deleted"
    BUSY = "busy"
    RETRY = "retry"


JOBS: dict[str, JobRecord] = {}
JOBS_LOCK = RLock()
DELETING_JOBS: set[str] = set()
RUNNING_TASKS: set[asyncio.Task[Any]] = set()


class GenerateRequest(BaseModel):
    """JSON aceito para iniciar a montagem do documento."""

    model_config = ConfigDict(extra="ignore")

    job_id: str
    reconciliations: list[dict[str, Any]] | dict[str, Any] | None = None
    reconciliation: list[dict[str, Any]] | dict[str, Any] | None = None
    compatibility_mode: bool = False

    @field_validator("job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        value = value.strip().lower()
        if not JOB_ID_PATTERN.fullmatch(value):
            raise ValueError("Identificador de execução inválido.")
        return value

    def reconciliation_payload(self) -> list[dict[str, Any]] | dict[str, Any]:
        return self.reconciliations or self.reconciliation or []


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _safe_job_dir(job_id: str, *, create: bool = False) -> Path:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("invalid job id")
    root = RUNTIME_ROOT.resolve()
    job_dir = (root / job_id).resolve()
    if not _is_within(job_dir, root) or job_dir == root:
        raise RuntimeError("unsafe runtime path")
    if create:
        job_dir.mkdir(parents=True, exist_ok=False)
    return job_dir


def _public_error(
    message: str,
    *,
    code: str,
    field_name: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if field_name:
        item["field"] = field_name
    return item


def _raise_form_error(
    message: str,
    *,
    code: str = "invalid_form",
    fields: Mapping[str, str] | None = None,
    http_status: int = status.HTTP_422_UNPROCESSABLE_CONTENT,
) -> None:
    raise HTTPException(
        status_code=http_status,
        detail={
            "code": code,
            "message": message,
            "fields": dict(fields or {}),
        },
    )


def _safe_filename(filename: str | None, fallback: str) -> str:
    raw = (filename or fallback).replace("\\", "/").rsplit("/", 1)[-1]
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    raw = SAFE_NAME_PATTERN.sub("-", raw).strip("._-")
    if not raw:
        return fallback
    return raw[-120:]


def _canonical_extension(upload: UploadFile, spec: UploadSpec) -> str:
    safe_name = _safe_filename(upload.filename, "arquivo")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in spec.extensions:
        expected = ", ".join(sorted(spec.extensions))
        _raise_form_error(
            f"Formato não aceito. Use: {expected}.",
            code="unsupported_extension",
            fields={upload.filename or "arquivo": expected},
        )
    return suffix


def _validate_real_type(path: Path, extension: str, field_name: str) -> None:
    try:
        inspect_file(path, f"arquivo{extension}")
    except (OSError, UploadValidationError):
        _raise_form_error(
            "O conteúdo do arquivo não corresponde ao formato informado.",
            code="type_mismatch",
            fields={field_name: "tipo real incompatível"},
        )


async def _save_upload(
    upload: UploadFile,
    field_name: str,
    spec: UploadSpec,
    job_dir: Path,
) -> Path:
    extension = _canonical_extension(upload, spec)
    target = (job_dir / f"{field_name}{extension}").resolve()
    if not _is_within(target, job_dir):
        _raise_form_error(
            "Nome de arquivo inválido.",
            code="unsafe_filename",
            fields={field_name: "nome inválido"},
        )

    size = 0
    try:
        with target.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise OverflowError
                output.write(chunk)
    except OverflowError:
        target.unlink(missing_ok=True)
        _raise_form_error(
            f"O arquivo excede o limite de {MAX_FILE_SIZE // (1024 * 1024)} MB.",
            code="file_too_large",
            fields={field_name: "arquivo muito grande"},
            http_status=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    except FileExistsError:
        _raise_form_error(
            "O arquivo foi enviado mais de uma vez.",
            code="duplicate_file",
            fields={field_name: "arquivo duplicado"},
        )
    except OSError:
        target.unlink(missing_ok=True)
        _raise_form_error(
            "Não foi possível armazenar o arquivo com segurança.",
            code="storage_error",
            fields={field_name: "falha de armazenamento"},
            http_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    finally:
        await upload.close()

    if size == 0:
        target.unlink(missing_ok=True)
        _raise_form_error(
            "O arquivo está vazio.",
            code="empty_file",
            fields={field_name: "arquivo vazio"},
        )

    _validate_real_type(target, extension, field_name)
    return target


def _first_form_value(
    form: Mapping[str, Any], aliases: Iterable[str]
) -> Any | None:
    for alias in aliases:
        value = form.get(alias)
        if value is not None and value != "":
            return value
    return None


def _normalize_analysis_mode(value: Any) -> str:
    mode = str(value or "integrated").strip().lower()
    aliases = {
        "integrado": "integrated",
        "integrated": "integrated",
        "separado": "separate",
        "separate": "separate",
        "separated": "separate",
    }
    if mode not in aliases:
        _raise_form_error(
            "Escolha análise integrada ou análises separadas.",
            code="invalid_analysis_mode",
            fields={"analysis_mode": "opção inválida"},
        )
    return aliases[mode]


def _normalize_checkbox(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "sim"}


def _validate_text_fields(fields: dict[str, Any]) -> None:
    missing = {
        name: "campo obrigatório"
        for name in REQUIRED_TEXT_FIELDS
        if not str(fields.get(name, "")).strip()
    }
    if missing:
        _raise_form_error(
            "Preencha os dados obrigatórios do documento.",
            code="missing_fields",
            fields=missing,
        )

    if len(fields["company_name"]) > 240:
        _raise_form_error(
            "A razão social é longa demais.",
            code="field_too_long",
            fields={"company_name": "máximo de 240 caracteres"},
        )

    for field_name in ("ergo_reference_date", "psychosocial_reference_date"):
        try:
            date.fromisoformat(fields[field_name])
        except (TypeError, ValueError):
            _raise_form_error(
                "Informe as datas no formato AAAA-MM-DD.",
                code="invalid_date",
                fields={field_name: "data inválida"},
            )

    competence = str(fields["competence"]).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", competence):
        _raise_form_error(
            "Informe a competência no formato AAAA-MM.",
            code="invalid_competence",
            fields={"competence": "competência inválida"},
        )


def _extract_form_inputs(
    form: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, UploadFile]]:
    fields: dict[str, Any] = {}
    for canonical, aliases in TEXT_FIELD_ALIASES.items():
        fields[canonical] = _first_form_value(form, aliases)
    fields["analysis_mode"] = _normalize_analysis_mode(fields["analysis_mode"])
    fields["compatibility_mode"] = _normalize_checkbox(
        fields["compatibility_mode"]
    )
    for name in REQUIRED_TEXT_FIELDS:
        if fields.get(name) is not None:
            fields[name] = str(fields[name]).strip()
    _validate_text_fields(fields)

    alias_to_canonical = {
        alias: canonical
        for canonical, spec in UPLOAD_SPECS.items()
        for alias in spec.aliases
    }
    uploads: dict[str, UploadFile] = {}
    multi_items = getattr(form, "multi_items", None)
    items = multi_items() if callable(multi_items) else form.items()
    for field_name, value in items:
        if not isinstance(value, UploadFile) or not value.filename:
            continue
        canonical = alias_to_canonical.get(str(field_name))
        if canonical is None:
            _raise_form_error(
                "O formulário contém um campo de arquivo desconhecido.",
                code="unexpected_file",
                fields={str(field_name): "campo não aceito"},
            )
        if canonical in uploads:
            _raise_form_error(
                "O mesmo arquivo foi enviado mais de uma vez.",
                code="duplicate_file",
                fields={canonical: "arquivo duplicado"},
            )
        uploads[canonical] = value

    for canonical, spec in UPLOAD_SPECS.items():
        value = _first_form_value(form, spec.aliases)
        if value is not None and not isinstance(value, UploadFile):
            _raise_form_error(
                "O campo deve conter um arquivo.",
                code="file_expected",
                fields={canonical: "arquivo esperado"},
            )

    missing_files = {
        name: "arquivo obrigatório"
        for name, spec in UPLOAD_SPECS.items()
        if spec.required and name not in uploads
    }
    if fields["analysis_mode"] == "integrated":
        if "integrated_report" not in uploads:
            missing_files["integrated_report"] = "arquivo obrigatório"
    else:
        if "psychosocial_analysis" not in uploads:
            missing_files["psychosocial_analysis"] = "arquivo obrigatório"
        if "ergonomic_analysis" not in uploads:
            missing_files["ergonomic_analysis"] = "arquivo obrigatório"
    if missing_files:
        _raise_form_error(
            "Adicione todos os arquivos obrigatórios.",
            code="missing_files",
            fields=missing_files,
        )
    return fields, uploads


async def _close_form_uploads(form: Mapping[str, Any]) -> None:
    seen: set[int] = set()
    multi_items = getattr(form, "multi_items", None)
    items = multi_items() if callable(multi_items) else form.items()
    for _, value in items:
        if isinstance(value, UploadFile) and id(value) not in seen:
            seen.add(id(value))
            await value.close()


def _get_job(job_id: str) -> JobRecord:
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    with JOBS_LOCK:
        record = JOBS.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return record


def _set_job_state(
    record: JobRecord,
    *,
    job_status: str,
    stage: str,
    progress: int,
    error_message: str | None = None,
) -> None:
    with JOBS_LOCK:
        record.status = job_status
        record.stage = stage
        record.progress = max(0, min(100, progress))
        record.error_message = error_message
        record.touch()


def _message_list(value: Any, default_level: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    result: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, Mapping):
            message = str(
                item.get("message")
                or item.get("mensagem")
                or item.get("detail")
                or item.get("code")
                or ""
            ).strip()
            if not message:
                continue
            result.append(
                {
                    "level": str(item.get("level") or item.get("severity") or default_level),
                    "code": str(item.get("code") or default_level),
                    "message": message,
                }
            )
        else:
            message = str(item).strip()
            if message:
                result.append(
                    {
                        "level": default_level,
                        "code": default_level,
                        "message": message,
                    }
                )
    return result


def _jsonable(value: Any) -> Any:
    """Converte resultados da pipeline sem expor caminhos absolutos."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return value.name
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _dig(data: Any, *paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = data
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                break
            current = current[key]
        else:
            return current
    return None


def _normalize_ghe(item: Any) -> dict[str, Any]:
    data = _jsonable(item)
    if not isinstance(data, Mapping):
        return {"code": "", "name": str(data), "employees": 0}
    sectors = data.get("sectors") or data.get("setores") or []
    roles = data.get("roles") or data.get("positions") or data.get("cargos") or []
    return {
        "id": str(data.get("id") or data.get("code") or data.get("codigo") or ""),
        "code": str(data.get("code") or data.get("codigo") or data.get("id") or ""),
        "name": str(data.get("name") or data.get("nome") or ""),
        "sectors": _jsonable(sectors),
        "roles": _jsonable(roles),
        "employees": int(
            data.get("employees")
            or data.get("employee_count")
            or data.get("population")
            or data.get("quantidade_colaboradores")
            or 0
        ),
    }


def _normalize_validation_result(raw: Any) -> dict[str, Any]:
    public = _jsonable(raw)
    if not isinstance(public, Mapping):
        public = {"result": public}

    ghe_source = _dig(
        public,
        ("ghes",),
        ("official_ghes",),
        ("summary", "ghes"),
        ("normalized", "official_ghes"),
        ("normalized_aep", "official_ghes"),
    )
    ghes = [
        _normalize_ghe(item)
        for item in (ghe_source if isinstance(ghe_source, list) else [])
    ]
    population = _dig(
        public,
        ("total_population",),
        ("population",),
        ("summary", "total_population"),
        ("normalized", "total_population"),
        ("normalized_aep", "total_population"),
    )
    if population is None:
        population = sum(ghe["employees"] for ghe in ghes)

    warnings = _message_list(
        _dig(
            public,
            ("warnings",),
            ("validation", "warnings"),
            ("validation_report", "warnings"),
        ),
        "warning",
    )
    errors = _message_list(
        _dig(
            public,
            ("errors",),
            ("validation", "errors"),
            ("validation_report", "errors"),
        ),
        "error",
    )
    reconciliation = _dig(
        public,
        ("reconciliation",),
        ("reconciliation_plan",),
        ("validation", "reconciliation"),
    )
    if not isinstance(reconciliation, Mapping):
        reconciliation = {"items": reconciliation or []}
    compatibility = _dig(
        public,
        ("compatibility",),
        ("document", "compatibility"),
        ("normalized", "document", "compatibility"),
    )
    if not isinstance(compatibility, Mapping):
        compatibility = None

    return {
        "summary": {
            "ghe_count": len(ghes),
            "total_population": int(population or 0),
            "ghes": ghes,
        },
        "warnings": warnings,
        "errors": errors,
        "reconciliation": _jsonable(reconciliation),
        "compatibility": _jsonable(compatibility),
    }


def _pipeline_payload(record: JobRecord) -> dict[str, Any]:
    paths = dict(record.files)
    data = dict(record.fields)
    return {
        "job_id": record.job_id,
        "job_dir": record.job_dir,
        "files": paths,
        "paths": paths,
        "fields": data,
        "data": data,
        "dados": data,
        "analysis_mode": record.fields.get("analysis_mode", "integrated"),
        "compatibility_mode": bool(record.fields.get("compatibility_mode")),
    }


def _instantiate_pipeline(record: JobRecord) -> Any:
    if DocumentPipeline is None:
        raise RuntimeError("pipeline_unavailable")
    if PIPELINE_INSTANCE is not None:
        return PIPELINE_INSTANCE

    signature = inspect.signature(DocumentPipeline)
    parameters = signature.parameters
    kwargs: dict[str, Any] = {}
    if "work_dir" in parameters:
        kwargs["work_dir"] = record.job_dir
    elif "job_dir" in parameters:
        kwargs["job_dir"] = record.job_dir
    elif "runtime_dir" in parameters:
        kwargs["runtime_dir"] = record.job_dir
    if "job_id" in parameters:
        kwargs["job_id"] = record.job_id
    return DocumentPipeline(**kwargs)


def _initialize_default_pipeline() -> None:
    """Inicializa o compilador somente após validar os secrets hospedados."""

    global DEFAULT_PIPELINE_OWNED
    global HOSTED_TEMPLATE_RUNTIME_DIR
    global PIPELINE_INSTANCE
    global PIPELINE_STARTUP_ERROR

    if PIPELINE_INSTANCE is not None or DocumentPipeline is None:
        return
    materialized_runtime: Path | None = None
    try:
        materialized = materialize_hosted_template(default_settings)
        materialized_runtime = materialized.runtime_dir
        HOSTED_TEMPLATE_RUNTIME_DIR = materialized.runtime_dir
        PIPELINE_INSTANCE = DocumentPipeline(settings=materialized.settings)
        PIPELINE_STARTUP_ERROR = None
        DEFAULT_PIPELINE_OWNED = True
    except HostedTemplateError as exc:
        remove_materialized_template(materialized_runtime)
        HOSTED_TEMPLATE_RUNTIME_DIR = None
        PIPELINE_INSTANCE = None
        PIPELINE_STARTUP_ERROR = exc.code
        DEFAULT_PIPELINE_OWNED = False
        logger.error(
            "Pipeline recusada por configuração privada inválida código=%s",
            exc.code,
        )
    except Exception as exc:
        remove_materialized_template(materialized_runtime)
        HOSTED_TEMPLATE_RUNTIME_DIR = None
        PIPELINE_INSTANCE = None
        PIPELINE_STARTUP_ERROR = "pipeline_startup_failed"
        DEFAULT_PIPELINE_OWNED = False
        logger.error(
            "Pipeline não pôde ser inicializada tipo=%s",
            type(exc).__name__,
        )


def _shutdown_default_pipeline() -> None:
    global DEFAULT_PIPELINE_OWNED
    global HOSTED_TEMPLATE_RUNTIME_DIR
    global PIPELINE_INSTANCE

    if not DEFAULT_PIPELINE_OWNED:
        return
    PIPELINE_INSTANCE = None
    remove_materialized_template(HOSTED_TEMPLATE_RUNTIME_DIR)
    HOSTED_TEMPLATE_RUNTIME_DIR = None
    DEFAULT_PIPELINE_OWNED = False


def _pipeline_is_ready() -> bool:
    if DocumentPipeline is None or PIPELINE_INSTANCE is None:
        return False
    pipeline_settings = getattr(PIPELINE_INSTANCE, "settings", None)
    if pipeline_settings is None:
        return False
    if bool(
        getattr(
            pipeline_settings,
            "allow_synthetic_template_fallback",
            False,
        )
    ):
        return True

    template_path = Path(
        getattr(pipeline_settings, "template_path", "")
    )
    manifest_path = Path(
        getattr(pipeline_settings, "template_manifest_path", "")
    )
    if not template_path.is_file() or not manifest_path.is_file():
        return False
    try:
        from docx import Document as WordDocument

        from app.services.document_assembler import (
            _load_template_manifest,
            _validate_template_manifest,
        )

        inspection = inspect_file(template_path, template_path.name)
        manifest = _load_template_manifest(manifest_path)
        _validate_template_manifest(
            WordDocument(template_path),
            template_path,
            manifest,
        )
    except Exception:
        return False
    return inspection.real_type == "docx"


def _method_arguments(
    method: Callable[..., Any],
    payload: dict[str, Any],
    reconciliation: Any = None,
) -> tuple[list[Any], dict[str, Any]]:
    signature = inspect.signature(method)
    parameters = list(signature.parameters.values())
    kwargs: dict[str, Any] = {}
    args: list[Any] = []
    canonical = {
        "payload": payload,
        "context": payload,
        "input_data": payload,
        "inputs": payload,
        "files": payload["files"],
        "paths": payload["paths"],
        "file_paths": payload["files"],
        "uploads": payload["files"],
        "fields": payload["fields"],
        "data": payload["data"],
        "dados": payload["dados"],
        "metadata": payload["fields"],
        "job_id": payload["job_id"],
        "job_dir": payload["job_dir"],
        "work_dir": payload["job_dir"],
        "runtime_dir": payload["job_dir"],
        "analysis_mode": payload["analysis_mode"],
        "compatibility_mode": payload.get("compatibility_mode", False),
        "reconciliation": reconciliation,
        "reconciliations": reconciliation,
        "decisions": reconciliation,
    }

    has_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    required_unknown: list[inspect.Parameter] = []
    for parameter in parameters:
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if parameter.name in canonical:
            kwargs[parameter.name] = canonical[parameter.name]
        elif parameter.default is inspect.Parameter.empty:
            required_unknown.append(parameter)

    if len(parameters) == 1 and required_unknown:
        args.append(payload)
        required_unknown.clear()
    if required_unknown:
        names = ", ".join(item.name for item in required_unknown)
        raise TypeError(f"unsupported pipeline signature: {names}")
    if has_var_kwargs:
        kwargs.setdefault("payload", payload)
        if reconciliation is not None:
            kwargs.setdefault("reconciliation", reconciliation)
    return args, kwargs


async def _invoke_pipeline(
    record: JobRecord,
    method_names: tuple[str, ...],
    *,
    reconciliation: Any = None,
) -> Any:
    pipeline = _instantiate_pipeline(record)
    method = next(
        (
            getattr(pipeline, name)
            for name in method_names
            if callable(getattr(pipeline, name, None))
        ),
        None,
    )
    if method is None:
        raise RuntimeError("pipeline_method_unavailable")
    payload = _pipeline_payload(record)
    args, kwargs = _method_arguments(method, payload, reconciliation)
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await run_in_threadpool(lambda: method(*args, **kwargs))


def _extract_artifact_path(
    raw: Any, keys: tuple[str, ...], record: JobRecord
) -> Path | None:
    candidate: Any = None
    if isinstance(raw, (str, Path)):
        candidate = raw
    elif isinstance(raw, Mapping):
        for key in keys:
            if raw.get(key):
                candidate = raw[key]
                break
    else:
        for key in keys:
            value = getattr(raw, key, None)
            if value:
                candidate = value
                break
    if candidate is None:
        return None

    path = Path(candidate)
    if not path.is_absolute():
        path = record.job_dir / path
    path = path.resolve()
    allowed_roots = (
        record.job_dir,
        PROJECT_ROOT / "outputs",
        PROJECT_ROOT / "generated",
    )
    if not any(_is_within(path, root) for root in allowed_roots):
        raise RuntimeError("unsafe_artifact_path")
    if not path.is_file():
        raise RuntimeError("artifact_not_found")
    return path


def _extract_artifact_bytes(raw: Any, keys: tuple[str, ...]) -> bytes | None:
    if not isinstance(raw, Mapping):
        return None
    for key in keys:
        value = raw.get(key)
        if isinstance(value, bytes):
            return value
    return None


def _write_validation_report(record: JobRecord) -> Path:
    report_path = (record.job_dir / "validation-report.json").resolve()
    if not _is_within(report_path, record.job_dir):
        raise RuntimeError("unsafe report path")
    report = {
        "schema_version": "1.0",
        "job_id": record.job_id,
        "generated_at": _utc_iso(time.time()),
        "status": record.status,
        "document": {
            "competence": record.fields.get("competence"),
            "analysis_mode": record.fields.get("analysis_mode"),
            "compatibility_mode": record.compatibility is not None,
        },
        "summary": record.validation.get("summary", {}),
        "warnings": record.warnings,
        "errors": record.errors,
        "reconciliation": record.reconciliation,
        "compatibility": record.compatibility,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    record.validation_report_path = report_path
    return report_path


def _job_snapshot(record: JobRecord) -> dict[str, Any]:
    downloads = {
        "document": (
            f"/api/jobs/{record.job_id}/document"
            if record.document_path is not None
            else None
        ),
        "download": (
            f"/api/jobs/{record.job_id}/download"
            if record.document_path is not None
            else None
        ),
        "validation_report": (
            f"/api/jobs/{record.job_id}/validation-report"
            if record.validation_report_path is not None
            else None
        ),
        "pdf": None,
    }
    return {
        "job_id": record.job_id,
        "status": record.status,
        "stage": record.stage,
        "progress": record.progress,
        "created_at": _utc_iso(record.created_at),
        "updated_at": _utc_iso(record.updated_at),
        "summary": record.validation.get("summary", {}),
        "warnings": record.warnings,
        "errors": record.errors,
        "reconciliation": record.reconciliation,
        "compatibility": record.compatibility,
        "error": record.error_message,
        "downloads": downloads,
    }


def _discard_pipeline_state(job_id: str) -> None:
    pipeline = PIPELINE_INSTANCE
    discard = getattr(pipeline, "discard", None)
    if not callable(discard):
        return
    try:
        discard(job_id)
    except Exception as exc:
        logger.warning(
            "Falha ao descartar estado privado job=%s tipo=%s",
            job_id,
            type(exc).__name__,
        )


def _remove_runtime_tree(path: Path, *, attempts: int = 3) -> bool:
    root = RUNTIME_ROOT.resolve()
    if path.is_symlink():
        logger.warning("Limpeza recusou vínculo simbólico no runtime.")
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if resolved == root or not _is_within(resolved, root):
        logger.warning("Limpeza recusou caminho fora do runtime.")
        return False

    for attempt in range(max(1, attempts)):
        try:
            shutil.rmtree(resolved, ignore_errors=False)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 < attempts:
                time.sleep(0.02 * (attempt + 1))
    return False


def _delete_job_data(job_id: str) -> JobDeletionOutcome:
    """Reserva e remove um job sem competir com processamento ou download."""

    if not JOB_ID_PATTERN.fullmatch(job_id):
        return JobDeletionOutcome.RETRY
    with JOBS_LOCK:
        record = JOBS.get(job_id)
        if (
            job_id in DELETING_JOBS
            or (
                record is not None
                and (
                    record.status in {"receiving", "validating", "generating"}
                    or record.active_downloads > 0
                )
            )
        ):
            return JobDeletionOutcome.BUSY
        DELETING_JOBS.add(job_id)
        if record is not None:
            JOBS.pop(job_id, None)

    outcome = JobDeletionOutcome.RETRY
    try:
        job_dir = record.job_dir if record is not None else _safe_job_dir(job_id)
        if _remove_runtime_tree(job_dir):
            _discard_pipeline_state(job_id)
            outcome = JobDeletionOutcome.DELETED
    finally:
        with JOBS_LOCK:
            if outcome is not JobDeletionOutcome.DELETED and record is not None:
                JOBS.setdefault(job_id, record)
            DELETING_JOBS.discard(job_id)
    return outcome


def _cleanup_orphan_runtime_dirs(timestamp: float) -> int:
    try:
        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        children = tuple(RUNTIME_ROOT.iterdir())
    except OSError:
        logger.warning("A área temporária não pôde ser varrida.")
        return 0

    with JOBS_LOCK:
        active_ids = set(JOBS) | set(DELETING_JOBS)
    removed = 0
    for child in children:
        if (
            child.name in active_ids
            or not JOB_ID_PATTERN.fullmatch(child.name)
            or child.is_symlink()
            or not child.is_dir()
        ):
            continue
        try:
            age = timestamp - child.stat().st_mtime
        except OSError:
            continue
        if age < JOB_TTL_SECONDS:
            continue
        if _remove_runtime_tree(child):
            _discard_pipeline_state(child.name)
            removed += 1
        else:
            logger.warning("Falha ao limpar diretório órfão job=%s", child.name)
    return removed


def cleanup_expired_jobs(now: float | None = None) -> int:
    """Remove jobs expirados sem aceitar caminhos externos ao runtime."""

    timestamp = now if now is not None else time.time()
    with JOBS_LOCK:
        expired = [
            record
            for record in JOBS.values()
            if timestamp - record.updated_at >= JOB_TTL_SECONDS
            and record.status not in {"receiving", "validating", "generating"}
            and record.active_downloads == 0
        ]

    removed = 0
    for record in expired:
        outcome = _delete_job_data(record.job_id)
        if outcome is JobDeletionOutcome.DELETED:
            removed += 1
        elif outcome is JobDeletionOutcome.RETRY:
            logger.warning(
                "Falha ao limpar execução expirada job=%s; nova tentativa agendada",
                record.job_id,
            )
    removed += _cleanup_orphan_runtime_dirs(timestamp)
    return removed


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(min(60, max(10, JOB_TTL_SECONDS // 4)))
        await run_in_threadpool(cleanup_expired_jobs)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(_initialize_default_pipeline)
    await run_in_threadpool(cleanup_expired_jobs)
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        await run_in_threadpool(_shutdown_default_pipeline)


app = FastAPI(
    title="Automatizador de Documentos AEP",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
    expose_headers=[
        "Content-Disposition",
        "Content-Length",
        "X-AEP-Content-Length",
        "X-AEP-Content-SHA256",
    ],
    max_age=600,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _apply_security_headers(response: Response, *, api_path: bool) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if api_path:
        existing_cache_control = response.headers.get("Cache-Control", "")
        response.headers["Cache-Control"] = (
            "no-store, no-transform"
            if "no-transform" in existing_cache_control.casefold()
            else "no-store"
        )
        response.headers["Pragma"] = "no-cache"
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next: Callable[..., Any]):
    api_path = request.url.path.startswith("/api/")
    protected_api = api_path and request.url.path != "/api/health"
    origin = request.headers.get("origin")
    if protected_api and (
        (REQUIRE_ORIGIN and not origin)
        or (origin is not None and origin not in ALLOWED_ORIGINS)
    ):
        return _apply_security_headers(
            JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "detail": {
                        "code": "origin_not_allowed",
                        "message": "Origem da requisição não autorizada.",
                    }
                },
            ),
            api_path=True,
        )

    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > MAX_REQUEST_SIZE:
                return _apply_security_headers(
                    JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={
                            "detail": {
                                "code": "request_too_large",
                                "message": (
                                    "O conjunto de arquivos excede o limite permitido."
                                ),
                                "fields": {},
                            }
                        },
                    ),
                    api_path=api_path,
                )
        except ValueError:
            return _apply_security_headers(
                JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Cabeçalho de tamanho inválido."},
                ),
                api_path=api_path,
            )

    response = await call_next(request)
    _apply_security_headers(response, api_path=api_path)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'"
    )
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    cleanup_expired_jobs()
    pipeline_ready = _pipeline_is_ready()
    return JSONResponse(
        status_code=(
            status.HTTP_200_OK
            if pipeline_ready
            else status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        content={
            "status": "ok" if pipeline_ready else "degraded",
            "service": "Automatizador de Documentos AEP",
            "version": app.version,
            "pipeline_ready": pipeline_ready,
            "processing": "temporary",
        },
    )


@app.post("/api/validate")
async def validate_files(request: Request) -> JSONResponse:
    cleanup_expired_jobs()
    if not _pipeline_is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "pipeline_not_ready",
                "message": (
                    "O modelo privado e seu manifesto ainda não estão disponíveis "
                    "ou não passaram na verificação de integridade."
                ),
                "fields": {},
            },
        )
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" not in content_type:
        _raise_form_error(
            "Envie os dados como formulário multipart.",
            code="multipart_required",
            http_status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    try:
        form = await _parse_bounded_multipart(request)
    except HTTPException:
        raise
    except Exception:
        _raise_form_error(
            "Não foi possível interpretar o formulário enviado.",
            code="malformed_multipart",
            http_status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        fields, uploads = _extract_form_inputs(form)
    except Exception:
        await _close_form_uploads(form)
        raise

    job_id = secrets.token_hex(16)
    try:
        job_dir = _safe_job_dir(job_id, create=True)
    except OSError:
        await _close_form_uploads(form)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "storage_unavailable",
                "message": "A área temporária local não está disponível.",
                "fields": {},
            },
        )
    timestamp = time.time()
    record = JobRecord(
        job_id=job_id,
        job_dir=job_dir,
        created_at=timestamp,
        updated_at=timestamp,
        fields=fields,
    )
    with JOBS_LOCK:
        JOBS[job_id] = record

    try:
        _set_job_state(
            record,
            job_status="receiving",
            stage="Armazenando arquivos com segurança",
            progress=12,
        )
        for canonical, upload in uploads.items():
            record.files[canonical] = await _save_upload(
                upload, canonical, UPLOAD_SPECS[canonical], job_dir
            )
        _set_job_state(
            record,
            job_status="validating",
            stage="Extraindo e validando documentos",
            progress=30,
        )
        raw_result = await _invoke_pipeline(
            record, ("validate", "validate_inputs", "run_validation")
        )
        normalized = _normalize_validation_result(raw_result)
        record.validation = normalized
        record.warnings = normalized["warnings"]
        record.errors = normalized["errors"]
        record.reconciliation = normalized["reconciliation"]
        record.compatibility = normalized["compatibility"]
        needs_reconciliation = bool(
            record.reconciliation.get("items")
            or record.reconciliation.get("required")
        )
        final_status = (
            "validation_failed"
            if record.errors
            else "needs_reconciliation"
            if needs_reconciliation
            else "validated"
        )
        _set_job_state(
            record,
            job_status=final_status,
            stage=(
                "Corrija os erros encontrados"
                if record.errors
                else "Revise a reconciliação dos GHEs"
                if needs_reconciliation
                else "Arquivos validados"
            ),
            progress=48 if record.errors else 52,
        )
        _write_validation_report(record)
        if final_status == "validation_failed":
            _discard_pipeline_state(job_id)
        return JSONResponse(
            status_code=200,
            content=_job_snapshot(record),
        )
    except HTTPException:
        if _remove_runtime_tree(job_dir):
            with JOBS_LOCK:
                JOBS.pop(job_id, None)
            _discard_pipeline_state(job_id)
        raise
    except RuntimeError as exc:
        _discard_pipeline_state(job_id)
        if str(exc) == "pipeline_unavailable":
            message = "O compilador ainda não está disponível nesta instalação."
            code = "pipeline_unavailable"
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        elif str(exc) == "pipeline_method_unavailable":
            message = "A etapa de validação não está configurada."
            code = "validation_unavailable"
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        elif getattr(exc, "code", "") in {
            "compatibility_profile_unavailable",
            "compatibility_profile_invalid",
            "compatibility_profile_mismatch",
        }:
            message = str(exc)
            code = str(getattr(exc, "code"))
            http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        else:
            message = "A validação não pôde ser concluída."
            code = "validation_failed"
            http_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        _set_job_state(
            record,
            job_status="failed",
            stage="Falha na validação",
            progress=30,
            error_message=message,
        )
        record.errors = [_public_error(message, code=code)]
        _write_validation_report(record)
        logger.error("Falha na validação job=%s tipo=%s", job_id, type(exc).__name__)
        raise HTTPException(
            status_code=http_status,
            detail={"code": code, "message": message, "job_id": job_id},
        )
    except Exception as exc:
        _discard_pipeline_state(job_id)
        message = "A validação não pôde ser concluída."
        _set_job_state(
            record,
            job_status="failed",
            stage="Falha na validação",
            progress=30,
            error_message=message,
        )
        record.errors = [_public_error(message, code="validation_failed")]
        _write_validation_report(record)
        logger.error("Falha na validação job=%s tipo=%s", job_id, type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "validation_failed",
                "message": message,
                "job_id": job_id,
            },
        )
    finally:
        await _close_form_uploads(form)


async def _execute_generation(
    record: JobRecord,
    reconciliation: Any,
) -> None:
    try:
        _set_job_state(
            record,
            job_status="generating",
            stage="Aplicando reconciliação dos GHEs",
            progress=60,
        )
        raw_result = await _invoke_pipeline(
            record,
            ("generate", "assemble", "run_generation"),
            reconciliation=reconciliation,
        )
        public_result = _jsonable(raw_result)
        if isinstance(public_result, Mapping):
            generated_reconciliation = public_result.get("reconciliation")
            if isinstance(generated_reconciliation, Mapping):
                record.reconciliation = dict(generated_reconciliation)
            if "compatibility" in public_result:
                generated_compatibility = public_result.get("compatibility")
                record.compatibility = (
                    dict(generated_compatibility)
                    if isinstance(generated_compatibility, Mapping)
                    else None
                )
            generated_warnings = public_result.get("warnings")
            if generated_warnings is not None:
                record.warnings = _message_list(
                    generated_warnings, "warning"
                )
            generated_errors = public_result.get("errors")
            if generated_errors is not None:
                record.errors = _message_list(generated_errors, "error")
        _set_job_state(
            record,
            job_status="generating",
            stage="Finalizando documento editável",
            progress=90,
        )

        document_bytes = _extract_artifact_bytes(
            raw_result, ("document_bytes", "docx_bytes")
        )
        if document_bytes is not None:
            target = record.job_dir / "documento-aep.docx"
            target.write_bytes(document_bytes)
            record.document_path = target.resolve()
        else:
            record.document_path = _extract_artifact_path(
                raw_result,
                ("document_path", "docx_path", "output_path", "document"),
                record,
            )
        if record.document_path is None:
            raise RuntimeError("document_not_produced")
        _validate_real_type(record.document_path, ".docx", "document")

        report_path = _extract_artifact_path(
            raw_result,
            (
                "validation_report_path",
                "report_path",
                "validation_report",
            ),
            record,
        )
        if report_path is not None and report_path.suffix.lower() == ".json":
            record.validation_report_path = report_path
        else:
            _write_validation_report(record)

        _set_job_state(
            record,
            job_status="completed",
            stage="Documento AEP pronto",
            progress=100,
        )
        _write_validation_report(record)
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc) == "pipeline_unavailable":
            message = "O compilador ainda não está disponível nesta instalação."
        elif (
            isinstance(exc, RuntimeError)
            and str(exc) == "pipeline_method_unavailable"
        ):
            message = "A etapa de geração não está configurada."
        else:
            message = "Não foi possível gerar o documento. Revise a validação."
        _set_job_state(
            record,
            job_status="failed",
            stage="Falha na geração",
            progress=max(55, record.progress),
            error_message=message,
        )
        record.errors.append(_public_error(message, code="generation_failed"))
        _write_validation_report(record)
        logger.error(
            "Falha na geração job=%s tipo=%s",
            record.job_id,
            type(exc).__name__,
        )
    finally:
        _discard_pipeline_state(record.job_id)


@app.post("/api/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_document(payload: GenerateRequest) -> JSONResponse:
    cleanup_expired_jobs()
    record = _get_job(payload.job_id)
    if record.status in {"receiving", "validating", "generating"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "job_busy",
                "message": "Esta execução ainda está sendo processada.",
            },
        )
    if record.status in {"validation_failed", "failed"} or record.errors:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "validation_errors",
                "message": "Corrija os erros de validação antes de gerar.",
            },
        )
    validated_compatibility = record.compatibility is not None
    if payload.compatibility_mode != validated_compatibility:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "validation_stale",
                "message": (
                    "O modo de compatibilidade mudou. "
                    "Valide os arquivos novamente."
                ),
            },
        )
    if record.status == "completed":
        return JSONResponse(status_code=200, content=_job_snapshot(record))
    if record.status not in {"validated", "needs_reconciliation"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "job_not_validated",
                "message": "Valide os arquivos antes de gerar o documento.",
            },
        )

    reconciliation = payload.reconciliation_payload()
    if record.status == "needs_reconciliation" and not reconciliation:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "reconciliation_required",
                "message": "Revise todas as correspondências de GHE.",
            },
        )

    _set_job_state(
        record,
        job_status="generating",
        stage="Preparando geração",
        progress=55,
    )
    task = asyncio.create_task(
        _execute_generation(
            record,
            reconciliation,
        )
    )
    RUNNING_TASKS.add(task)
    task.add_done_callback(RUNNING_TASKS.discard)
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=_job_snapshot(record),
    )


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict[str, Any]:
    cleanup_expired_jobs()
    return _job_snapshot(_get_job(job_id.lower()))


def _artifact_integrity(path: Path) -> tuple[int, str]:
    """Calcula metadados dos bytes originais e recusa alteração concorrente."""

    before = path.stat()
    digest = sha256_file(path)
    after = path.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise OSError("artifact_changed_during_integrity_check")
    return after.st_size, digest


class _DownloadFinalizer:
    """Executa exatamente uma finalização, inclusive após desconexão."""

    def __init__(self, callback: Callable[[bool], None]) -> None:
        self._callback = callback
        self._done = False
        self._lock = RLock()

    @property
    def done(self) -> bool:
        with self._lock:
            return self._done

    def finish(self, completed: bool) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        self._callback(completed)


class _ManagedFileResponse(FileResponse):
    """Libera o claim do download mesmo se o envio não terminar."""

    def __init__(
        self,
        *args: Any,
        finalizer: _DownloadFinalizer,
        **kwargs: Any,
    ) -> None:
        self._download_finalizer = finalizer
        super().__init__(
            *args,
            background=BackgroundTask(finalizer.finish, True),
            **kwargs,
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if not self._download_finalizer.done:
                await run_in_threadpool(self._download_finalizer.finish, False)


async def _download_response(
    request: Request,
    record: JobRecord,
    path: Path | None,
    *,
    media_type: str,
    filename: str,
    delete_after: bool = False,
) -> _ManagedFileResponse:
    if request.headers.get("range") is not None:
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail={
                "code": "partial_download_not_supported",
                "message": "Baixe o arquivo completo em uma única solicitação.",
            },
        )
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo ainda não disponível.",
        )
    if not _is_within(path, record.job_dir):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo não encontrado.",
        )
    with JOBS_LOCK:
        current = JOBS.get(record.job_id)
        if current is not record or record.job_id in DELETING_JOBS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Execução não encontrada.",
            )
        record.active_downloads += 1
        record.touch()

    try:
        artifact_size, artifact_sha256 = await run_in_threadpool(
            _artifact_integrity,
            path,
        )
    except OSError:
        with JOBS_LOCK:
            current = JOBS.get(record.job_id)
            if current is record:
                current.active_downloads = max(0, current.active_downloads - 1)
                current.touch()
        logger.warning(
            "Artefato indisponível durante verificação job=%s",
            record.job_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Arquivo não encontrado.",
        )

    def after_response(completed: bool) -> None:
        with JOBS_LOCK:
            current = JOBS.get(record.job_id)
            if current is record:
                current.active_downloads = max(0, current.active_downloads - 1)
                current.touch()
        if delete_after and completed:
            outcome = _delete_job_data(record.job_id)
            if outcome is JobDeletionOutcome.RETRY:
                logger.warning(
                    "Falha ao excluir job após download job=%s; TTL mantido",
                    record.job_id,
                )
            return

    finalizer = _DownloadFinalizer(after_response)
    return _ManagedFileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-AEP-Content-Length": str(artifact_size),
            "X-AEP-Content-SHA256": artifact_sha256,
        },
        finalizer=finalizer,
    )


@app.get("/api/jobs/{job_id}/document")
async def download_document(job_id: str, request: Request) -> FileResponse:
    record = _get_job(job_id.lower())
    return await _download_response(
        request,
        record,
        record.document_path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename="Documento AEP - AUTOMATICO.docx",
    )


@app.get("/api/jobs/{job_id}/download")
async def download_and_delete_document(
    job_id: str,
    request: Request,
) -> FileResponse:
    """Baixa o DOCX e agenda a exclusão após o envio da resposta."""

    record = _get_job(job_id.lower())
    return await _download_response(
        request,
        record,
        record.document_path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        filename="Documento AEP - AUTOMATICO.docx",
        delete_after=True,
    )


@app.get("/api/jobs/{job_id}/validation-report")
async def download_validation_report(
    job_id: str,
    request: Request,
) -> FileResponse:
    record = _get_job(job_id.lower())
    return await _download_response(
        request,
        record,
        record.validation_report_path,
        media_type="application/json",
        filename="Relatorio de Validacao AEP.json",
    )


@app.delete("/api/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str) -> Response:
    normalized = job_id.strip().lower()
    if not JOB_ID_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Execução não encontrada.",
        )
    outcome = _delete_job_data(normalized)
    if outcome is JobDeletionOutcome.BUSY:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "job_busy",
                "message": "A execução ainda está em processamento ou download.",
            },
        )
    if outcome is JobDeletionOutcome.RETRY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "cleanup_pending",
                "message": "A exclusão será repetida pela limpeza automática.",
            },
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
