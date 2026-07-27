"""Prepare private Base64 secret files for the hosted AEP backend.

The output files are deliberately written below ``private_templates/`` by
default, a directory excluded from Git and Docker. Before encoding anything,
the script applies the same structural, hash and sanitization checks used by
the document assembler.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from docx import Document

from app.services.document_assembler import (
    _load_template_manifest,
    _validate_template_manifest,
)
from app.services.file_security import inspect_file


RENDER_SECRET_FILES_LIMIT_BYTES = 1024 * 1024
DEFAULT_OUTPUT_DIR = Path("private_templates/hosted_secret")
TEMPLATE_SECRET_NAME = "aep_template.docx.b64"
MANIFEST_SECRET_NAME = "aep_template.manifest.json.b64"
COMPATIBILITY_PROFILE_SECRET_NAME = "aep_compatibility_profile.json.b64"
METADATA_NAME = "hosted_template_secret.metadata.json"


class HostedTemplateSecretError(ValueError):
    """Safe configuration error raised before private artifacts are emitted."""


@dataclass(frozen=True, slots=True)
class SecretArtifact:
    filename: str
    source_bytes: int
    base64_bytes: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class HostedTemplateSecretBundle:
    template: SecretArtifact
    manifest: SecretArtifact
    compatibility_profile: SecretArtifact | None
    total_base64_bytes: int
    provider_limit_bytes: int
    remaining_bytes: int
    output_directory: Path


def _base64_size(source_size: int) -> int:
    return 4 * ((source_size + 2) // 3)


def _private_output_directory(project_root: Path, output_dir: Path) -> Path:
    private_root = (project_root / "private_templates").resolve()
    candidate = (
        output_dir.resolve()
        if output_dir.is_absolute()
        else (project_root / output_dir).resolve()
    )
    try:
        candidate.relative_to(private_root)
    except ValueError as exc:
        raise HostedTemplateSecretError(
            "Os segredos hospedados devem permanecer em private_templates/."
        ) from exc
    return candidate


def _validate_private_template(template_path: Path, manifest_path: Path) -> None:
    if not template_path.is_file() or not manifest_path.is_file():
        raise HostedTemplateSecretError(
            "O template saneado e seu manifesto são obrigatórios."
        )
    if template_path.suffix.casefold() != ".docx":
        raise HostedTemplateSecretError("O template saneado deve ser um DOCX.")
    try:
        inspection = inspect_file(template_path, template_path.name)
        if inspection.real_type != "docx":
            raise HostedTemplateSecretError(
                "O arquivo fornecido não é um DOCX válido."
            )
        manifest = _load_template_manifest(manifest_path)
        _validate_template_manifest(
            Document(template_path),
            template_path,
            manifest,
        )
    except HostedTemplateSecretError:
        raise
    except Exception as exc:
        raise HostedTemplateSecretError(
            "O template ou manifesto não passou na validação de integridade."
        ) from exc


def _validate_compatibility_profile(profile_path: Path) -> None:
    try:
        if (
            profile_path.is_symlink()
            or not profile_path.is_file()
            or profile_path.stat().st_size > 64 * 1024
        ):
            raise ValueError("invalid profile file")
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise HostedTemplateSecretError(
            "O perfil privado de compatibilidade é inválido."
        ) from exc
    if not isinstance(payload, dict):
        raise HostedTemplateSecretError(
            "O perfil privado de compatibilidade é inválido."
        )

    fingerprint = payload.get("input_fingerprint")
    included = payload.get("included_ergo_ordinals")
    omitted = payload.get("omitted_ergo_ordinals")
    ordinals = (
        [*included, *omitted]
        if isinstance(included, list) and isinstance(omitted, list)
        else []
    )
    if (
        payload.get("schema_version") != 1
        or payload.get("mode") != "pilot_reference"
        or payload.get("analysis_mode") not in {"integrated", "separate"}
        or not isinstance(fingerprint, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint)
        or not isinstance(included, list)
        or not isinstance(omitted, list)
        or not included
        or any(type(item) is not int or item < 1 for item in ordinals)
        or len(ordinals) != len(set(ordinals))
        or set(ordinals) != set(range(1, len(ordinals) + 1))
    ):
        raise HostedTemplateSecretError(
            "O perfil privado de compatibilidade é inválido."
        )


def _artifact(
    filename: str,
    payload: bytes,
) -> tuple[SecretArtifact, bytes]:
    encoded = base64.b64encode(payload)
    return (
        SecretArtifact(
            filename=filename,
            source_bytes=len(payload),
            base64_bytes=len(encoded),
            source_sha256=hashlib.sha256(payload).hexdigest(),
        ),
        encoded,
    )


def _write_private_text(
    path: Path,
    payload: bytes,
    *,
    trailing_newline: bool = False,
) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.unlink(missing_ok=True)
        with temporary.open("xb") as output:
            output.write(payload)
            if trailing_newline:
                output.write(b"\n")
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HostedTemplateSecretError(
            "Não foi possível gravar os artefatos privados."
        ) from exc


def prepare_hosted_template_secret(
    template_path: Path,
    manifest_path: Path | None = None,
    *,
    compatibility_profile_path: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    provider_limit_bytes: int = RENDER_SECRET_FILES_LIMIT_BYTES,
    project_root: Path | None = None,
) -> HostedTemplateSecretBundle:
    """Validate and encode a private template into Render secret-file payloads."""

    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    template = template_path.resolve()
    manifest = (
        manifest_path.resolve()
        if manifest_path is not None
        else template.with_suffix(".manifest.json")
    )
    compatibility_profile = (
        compatibility_profile_path.resolve()
        if compatibility_profile_path is not None
        else None
    )
    destination = _private_output_directory(root, output_dir)

    if provider_limit_bytes <= 0:
        raise HostedTemplateSecretError(
            "O limite do provedor deve ser maior que zero."
        )

    _validate_private_template(template, manifest)
    if compatibility_profile is not None:
        _validate_compatibility_profile(compatibility_profile)

    template_size = template.stat().st_size
    manifest_size = manifest.stat().st_size
    estimated_total = (
        _base64_size(template_size)
        + _base64_size(manifest_size)
        + (
            _base64_size(compatibility_profile.stat().st_size)
            if compatibility_profile is not None
            else 0
        )
    )
    if estimated_total > provider_limit_bytes:
        raise HostedTemplateSecretError(
            "Os arquivos Base64 excedem o limite conjunto configurado para "
            "o provedor. Use arquivos secretos privados fora do repositório "
            f"em um serviço com limite superior a {estimated_total} bytes."
        )

    template_artifact, template_encoded = _artifact(
        TEMPLATE_SECRET_NAME,
        template.read_bytes(),
    )
    manifest_artifact, manifest_encoded = _artifact(
        MANIFEST_SECRET_NAME,
        manifest.read_bytes(),
    )
    compatibility_artifact: SecretArtifact | None = None
    compatibility_encoded: bytes | None = None
    if compatibility_profile is not None:
        compatibility_artifact, compatibility_encoded = _artifact(
            COMPATIBILITY_PROFILE_SECRET_NAME,
            compatibility_profile.read_bytes(),
        )
    total = (
        template_artifact.base64_bytes
        + manifest_artifact.base64_bytes
        + (
            compatibility_artifact.base64_bytes
            if compatibility_artifact is not None
            else 0
        )
    )
    if total != estimated_total:
        raise HostedTemplateSecretError(
            "A medição dos artefatos Base64 foi inconsistente."
        )

    try:
        destination.mkdir(parents=True, exist_ok=True)
        os.chmod(destination, 0o700)
    except OSError as exc:
        raise HostedTemplateSecretError(
            "Não foi possível preparar o diretório privado de saída."
        ) from exc
    _write_private_text(destination / TEMPLATE_SECRET_NAME, template_encoded)
    _write_private_text(destination / MANIFEST_SECRET_NAME, manifest_encoded)
    if (
        compatibility_artifact is not None
        and compatibility_encoded is not None
    ):
        _write_private_text(
            destination / COMPATIBILITY_PROFILE_SECRET_NAME,
            compatibility_encoded,
        )
    else:
        (destination / COMPATIBILITY_PROFILE_SECRET_NAME).unlink(
            missing_ok=True
        )

    bundle = HostedTemplateSecretBundle(
        template=template_artifact,
        manifest=manifest_artifact,
        compatibility_profile=compatibility_artifact,
        total_base64_bytes=total,
        provider_limit_bytes=provider_limit_bytes,
        remaining_bytes=provider_limit_bytes - total,
        output_directory=destination,
    )
    metadata = {
        "schema_version": 1,
        "provider": "render",
        "secret_files": {
            "template": asdict(template_artifact),
            "manifest": asdict(manifest_artifact),
            "compatibility_profile": (
                asdict(compatibility_artifact)
                if compatibility_artifact is not None
                else None
            ),
        },
        "total_base64_bytes": bundle.total_base64_bytes,
        "provider_limit_bytes": bundle.provider_limit_bytes,
        "remaining_bytes": bundle.remaining_bytes,
        "render_paths": {
            "template": "/etc/secrets/aep_template.docx.b64",
            "manifest": "/etc/secrets/aep_template.manifest.json.b64",
            "compatibility_profile": (
                "/etc/secrets/aep_compatibility_profile.json.b64"
                if compatibility_artifact is not None
                else None
            ),
        },
    }
    _write_private_text(
        destination / METADATA_NAME,
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8"),
        trailing_newline=True,
    )
    return bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Valida e prepara arquivos Base64 privados para hospedagem."
        )
    )
    parser.add_argument("template", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--compatibility-profile", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--provider-limit-bytes",
        type=int,
        default=RENDER_SECRET_FILES_LIMIT_BYTES,
    )
    args = parser.parse_args(argv)
    try:
        bundle = prepare_hosted_template_secret(
            args.template,
            args.manifest,
            compatibility_profile_path=args.compatibility_profile,
            output_dir=args.output_dir,
            provider_limit_bytes=args.provider_limit_bytes,
        )
    except HostedTemplateSecretError as exc:
        parser.error(str(exc))

    print("Artefatos privados Base64 preparados e validados.")
    print(
        "Tamanho conjunto: "
        f"{bundle.total_base64_bytes} bytes; "
        f"margem: {bundle.remaining_bytes} bytes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
