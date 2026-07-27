"""Materialização segura do template privado em ambientes hospedados."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from docx import Document

from app.config import Settings
from app.services.document_assembler import (
    _load_template_manifest,
    _validate_template_manifest,
)
from app.services.file_security import UploadValidationError, inspect_file


MAX_TEMPLATE_BASE64_BYTES = 16 * 1024 * 1024
MAX_TEMPLATE_PART_BASE64_BYTES = 500 * 1024
MAX_MANIFEST_BASE64_BYTES = 2 * 1024 * 1024
MAX_COMPATIBILITY_PROFILE_BASE64_BYTES = 256 * 1024


class HostedTemplateError(RuntimeError):
    """Erro de configuração seguro, sem conteúdo ou caminhos privados."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class HostedTemplateMaterialization:
    settings: Settings
    runtime_dir: Path | None


def _read_encoded(
    source: Path,
    *,
    encoded_limit: int,
    label: str,
) -> bytes:
    try:
        if source.is_symlink() or not source.is_file():
            raise OSError
        size = source.stat().st_size
        if size <= 0:
            raise HostedTemplateError(
                f"O arquivo secreto de {label} está vazio.",
                code="hosted_template_secret_invalid",
            )
        if size > encoded_limit:
            raise HostedTemplateError(
                f"O segredo Base64 de {label} excede o limite permitido.",
                code="hosted_template_secret_size",
            )
        encoded = b"".join(source.read_bytes().split())
    except HostedTemplateError:
        raise
    except OSError as exc:
        raise HostedTemplateError(
            f"O arquivo secreto de {label} não está disponível.",
            code="hosted_template_secret_unavailable",
        ) from exc
    if not encoded:
        raise HostedTemplateError(
            f"O arquivo secreto de {label} está vazio.",
            code="hosted_template_secret_invalid",
        )
    return encoded


def _decode_base64(encoded: bytes, *, label: str) -> bytes:
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HostedTemplateError(
            f"O arquivo secreto de {label} não contém Base64 válido.",
            code="hosted_template_secret_invalid",
        ) from exc
    if not decoded:
        raise HostedTemplateError(
            f"O arquivo secreto de {label} está vazio.",
            code="hosted_template_secret_invalid",
        )
    return decoded


def _read_and_decode(
    source: Path,
    *,
    encoded_limit: int,
    label: str,
) -> bytes:
    return _decode_base64(
        _read_encoded(
            source,
            encoded_limit=encoded_limit,
            label=label,
        ),
        label=label,
    )


def _read_and_decode_template_parts(sources: tuple[Path, ...]) -> bytes:
    if not sources or len(sources) > 64:
        raise HostedTemplateError(
            "A lista de partes do template é inválida.",
            code="hosted_template_secret_invalid",
        )
    validated_sources: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        if not source.is_absolute() or source.is_symlink():
            raise HostedTemplateError(
                "A lista de partes do template contém caminho inseguro.",
                code="hosted_template_secret_invalid",
            )
        resolved = source.resolve()
        if resolved in seen:
            raise HostedTemplateError(
                "A lista de partes do template contém duplicidade.",
                code="hosted_template_secret_invalid",
            )
        seen.add(resolved)
        validated_sources.append(source)

    parts: list[bytes] = []
    total = 0
    for index, source in enumerate(validated_sources, start=1):
        encoded = _read_encoded(
            source,
            encoded_limit=MAX_TEMPLATE_PART_BASE64_BYTES,
            label=f"parte {index} do template",
        )
        total += len(encoded)
        if total > MAX_TEMPLATE_BASE64_BYTES:
            raise HostedTemplateError(
                "As partes do template excedem o limite permitido.",
                code="hosted_template_secret_size",
            )
        parts.append(encoded)
    return _decode_base64(b"".join(parts), label="template")


def _write_private(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(payload)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise HostedTemplateError(
            "Não foi possível materializar o template privado.",
            code="hosted_template_materialization_failed",
        ) from exc


def _remove_stale_materializations(runtime_parent: Path) -> None:
    """Remove materializações deixadas por término não gracioso anterior."""

    resolved_parent = runtime_parent.resolve()
    for candidate in runtime_parent.glob(".hosted-template-*"):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise HostedTemplateError(
                "A área temporária do template não pôde ser saneada.",
                code="hosted_template_materialization_failed",
            ) from exc
        if resolved.parent != resolved_parent:
            continue
        try:
            shutil.rmtree(resolved, ignore_errors=False)
        except OSError as exc:
            raise HostedTemplateError(
                "A área temporária do template não pôde ser saneada.",
                code="hosted_template_materialization_failed",
            ) from exc


def _validate_materialized(template_path: Path, manifest_path: Path) -> None:
    try:
        inspection = inspect_file(template_path, template_path.name)
        if inspection.real_type != "docx":
            raise HostedTemplateError(
                "O template hospedado não é um DOCX válido.",
                code="hosted_template_invalid",
            )
        raw_manifest: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, dict):
            raise ValueError("manifest root")
        manifest = _load_template_manifest(manifest_path)
        _validate_template_manifest(
            Document(template_path),
            template_path,
            manifest,
        )
    except HostedTemplateError:
        raise
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        UploadValidationError,
        KeyError,
        TypeError,
    ) as exc:
        raise HostedTemplateError(
            "O template hospedado ou seu manifesto falhou na validação.",
            code="hosted_template_invalid",
        ) from exc
    except Exception as exc:
        # O validador estrutural usa exceções específicas de contrato que não
        # devem atravessar a fronteira HTTP nem revelar detalhes do manifesto.
        raise HostedTemplateError(
            "O template hospedado ou seu manifesto falhou na validação.",
            code="hosted_template_invalid",
        ) from exc


def materialize_hosted_template(
    settings: Settings,
) -> HostedTemplateMaterialization:
    """Materializa os segredos privados e devolve Settings para o runtime."""

    template_parts = settings.hosted_template_base64_files
    if template_parts and settings.hosted_template_base64_file is not None:
        raise HostedTemplateError(
            "A configuração do template privado é ambígua.",
            code="hosted_template_secret_invalid",
        )
    template_secret = (
        None if template_parts else settings.hosted_template_base64_file
    )
    manifest_secret = settings.hosted_template_manifest_base64_file
    compatibility_secret = settings.hosted_compatibility_profile_base64_file
    has_template_secret = bool(template_parts) or template_secret is not None
    if (
        not has_template_secret
        and manifest_secret is None
        and compatibility_secret is None
    ):
        return HostedTemplateMaterialization(settings=settings, runtime_dir=None)
    if has_template_secret != (manifest_secret is not None):
        raise HostedTemplateError(
            "Os dois arquivos secretos do template são obrigatórios.",
            code="hosted_template_secret_incomplete",
        )

    template_payload = (
        (
            _read_and_decode_template_parts(template_parts)
            if template_parts
            else _read_and_decode(
                template_secret,
                encoded_limit=MAX_TEMPLATE_BASE64_BYTES,
                label="template",
            )
        )
        if has_template_secret
        else None
    )
    manifest_payload = (
        _read_and_decode(
            manifest_secret,
            encoded_limit=MAX_MANIFEST_BASE64_BYTES,
            label="manifesto",
        )
        if manifest_secret is not None
        else None
    )
    compatibility_payload = (
        _read_and_decode(
            compatibility_secret,
            encoded_limit=MAX_COMPATIBILITY_PROFILE_BASE64_BYTES,
            label="perfil de compatibilidade",
        )
        if compatibility_secret is not None
        else None
    )

    runtime_parent = (
        settings.runtime_dir
        if settings.runtime_dir is not None
        else Path(tempfile.gettempdir()) / "aep-jobs"
    )
    try:
        runtime_parent.mkdir(parents=True, exist_ok=True)
        _remove_stale_materializations(runtime_parent)
        runtime_dir = Path(
            tempfile.mkdtemp(prefix=".hosted-template-", dir=runtime_parent)
        ).resolve()
        os.chmod(runtime_dir, 0o700)
    except OSError as exc:
        raise HostedTemplateError(
            "A área temporária do template não está disponível.",
            code="hosted_template_materialization_failed",
        ) from exc

    template_path = runtime_dir / "aep-template.docx"
    manifest_path = runtime_dir / "aep-template.manifest.json"
    compatibility_path = runtime_dir / "aep-compatibility-profile.json"
    try:
        if template_payload is not None and manifest_payload is not None:
            _write_private(template_path, template_payload)
            _write_private(manifest_path, manifest_payload)
            _validate_materialized(template_path, manifest_path)
        if compatibility_payload is not None:
            _write_private(compatibility_path, compatibility_payload)
            try:
                profile = json.loads(
                    compatibility_path.read_text(encoding="utf-8")
                )
                included = (
                    profile.get("included_ergo_ordinals")
                    if isinstance(profile, dict)
                    else None
                )
                omitted = (
                    profile.get("omitted_ergo_ordinals")
                    if isinstance(profile, dict)
                    else None
                )
                ordinals = (
                    [*included, *omitted]
                    if isinstance(included, list)
                    and isinstance(omitted, list)
                    else []
                )
                if (
                    not isinstance(profile, dict)
                    or profile.get("schema_version") != 1
                    or profile.get("mode") != "pilot_reference"
                    or profile.get("analysis_mode")
                    not in {"integrated", "separate"}
                    or not re.fullmatch(
                        r"[0-9a-fA-F]{64}",
                        str(profile.get("input_fingerprint") or ""),
                    )
                    or not isinstance(included, list)
                    or not isinstance(omitted, list)
                    or not included
                    or any(
                        type(item) is not int or item < 1
                        for item in ordinals
                    )
                    or len(ordinals) != len(set(ordinals))
                    or set(ordinals) != set(range(1, len(ordinals) + 1))
                ):
                    raise ValueError("profile root")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise HostedTemplateError(
                    "O perfil hospedado de compatibilidade é inválido.",
                    code="hosted_compatibility_profile_invalid",
                ) from exc
    except Exception:
        shutil.rmtree(runtime_dir, ignore_errors=True)
        raise

    return HostedTemplateMaterialization(
        settings=replace(
            settings,
            template_path=(
                template_path
                if template_payload is not None
                else settings.template_path
            ),
            template_manifest_path=(
                manifest_path
                if manifest_payload is not None
                else settings.template_manifest_path
            ),
            compatibility_profile_path=(
                compatibility_path
                if compatibility_payload is not None
                else settings.compatibility_profile_path
            ),
            trusted_private_runtime_dir=runtime_dir,
        ),
        runtime_dir=runtime_dir,
    )


def remove_materialized_template(runtime_dir: Path | None) -> None:
    if runtime_dir is None:
        return
    try:
        if runtime_dir.is_symlink():
            return
        shutil.rmtree(runtime_dir, ignore_errors=False)
    except FileNotFoundError:
        return
    except OSError:
        # O diretório está em armazenamento temporário e será recuperado pelo
        # ciclo de vida do container caso o sistema operacional bloqueie aqui.
        return
