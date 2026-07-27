from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _allowed_origins(value: str | None) -> tuple[str, ...]:
    origins: list[str] = []
    for raw_origin in (value or "").split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        if origin == "*":
            raise ValueError("AEP_ALLOWED_ORIGINS não pode conter curinga.")
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "AEP_ALLOWED_ORIGINS deve conter somente origens HTTP(S)."
            )
        if origin not in origins:
            origins.append(origin)
    return tuple(origins)


def _hosted_secret_files(value: str | None) -> tuple[Path, ...]:
    if value is None:
        return ()
    raw_items = value.split(",")
    if (
        not raw_items
        or len(raw_items) > 64
        or any(not item.strip() for item in raw_items)
    ):
        raise ValueError(
            "AEP_HOSTED_TEMPLATE_BASE64_FILES contém uma lista inválida."
        )
    paths: list[Path] = []
    seen: set[Path] = set()
    for item in raw_items:
        source = Path(item.strip()).expanduser()
        if not source.is_absolute():
            raise ValueError(
                "AEP_HOSTED_TEMPLATE_BASE64_FILES exige caminhos absolutos."
            )
        if source.is_symlink():
            raise ValueError(
                "AEP_HOSTED_TEMPLATE_BASE64_FILES não aceita links simbólicos."
            )
        resolved = source.resolve()
        if resolved in seen:
            raise ValueError(
                "AEP_HOSTED_TEMPLATE_BASE64_FILES contém caminhos duplicados."
            )
        seen.add(resolved)
        paths.append(source)
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class Settings:
    base_dir: Path
    host: str
    port: int
    max_file_bytes: int
    job_ttl_minutes: int
    template_path: Path
    template_manifest_path: Path
    render_on_generate: bool
    libreoffice_path: Path | None
    uploads_dir: Path
    generated_dir: Path
    outputs_dir: Path
    allow_synthetic_template_fallback: bool = False
    compatibility_profile_path: Path | None = None
    runtime_dir: Path | None = None
    job_ttl_seconds: int = 900
    allowed_origins: tuple[str, ...] = ("https://kinhoog.github.io",)
    require_origin: bool = True
    hosted_template_base64_file: Path | None = None
    hosted_template_base64_files: tuple[Path, ...] = ()
    hosted_template_manifest_base64_file: Path | None = None
    hosted_compatibility_profile_base64_file: Path | None = None
    trusted_private_runtime_dir: Path | None = None

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
        runtime_value = os.getenv("AEP_RUNTIME_DIR", "").strip()
        runtime_dir = (
            Path(runtime_value).expanduser().resolve()
            if runtime_value
            else (Path(tempfile.gettempdir()) / "aep-jobs").resolve()
        )
        ttl_seconds_value = os.getenv("AEP_JOB_TTL_SECONDS", "").strip()
        if ttl_seconds_value:
            ttl_seconds = max(60, int(ttl_seconds_value))
        else:
            legacy_ttl_minutes = os.getenv("AEP_JOB_TTL_MINUTES", "").strip()
            ttl_seconds = max(
                60,
                int(legacy_ttl_minutes) * 60
                if legacy_ttl_minutes
                else 900,
            )
        template = Path(
            os.getenv("AEP_TEMPLATE_PATH", "private_templates/aep_template.docx")
        )
        manifest = Path(
            os.getenv(
                "AEP_TEMPLATE_MANIFEST_PATH",
                "private_templates/aep_template.manifest.json",
            )
        )
        libreoffice = os.getenv("AEP_LIBREOFFICE_PATH", "").strip()
        compatibility_profile_value = os.getenv(
            "AEP_COMPATIBILITY_PROFILE_PATH",
            "private_templates/aep_compatibility_profile.json",
        ).strip()
        compatibility_profile = (
            Path(compatibility_profile_value)
            if compatibility_profile_value
            else None
        )
        hosted_template_value = os.getenv(
            "AEP_HOSTED_TEMPLATE_BASE64_FILE", ""
        ).strip()
        hosted_template_files = _hosted_secret_files(
            os.getenv("AEP_HOSTED_TEMPLATE_BASE64_FILES")
        )
        if hosted_template_value and hosted_template_files:
            raise ValueError(
                "AEP_HOSTED_TEMPLATE_BASE64_FILE e "
                "AEP_HOSTED_TEMPLATE_BASE64_FILES não podem ser "
                "configuradas juntas."
            )
        hosted_manifest_value = os.getenv(
            "AEP_HOSTED_TEMPLATE_MANIFEST_BASE64_FILE", ""
        ).strip()
        hosted_compatibility_value = os.getenv(
            "AEP_HOSTED_COMPATIBILITY_PROFILE_BASE64_FILE", ""
        ).strip()
        return cls(
            base_dir=root,
            host=os.getenv("AEP_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT") or os.getenv("AEP_PORT", "8000")),
            max_file_bytes=int(os.getenv("AEP_MAX_FILE_MB", "25")) * 1024 * 1024,
            job_ttl_minutes=max(1, (ttl_seconds + 59) // 60),
            template_path=(root / template).resolve()
            if not template.is_absolute()
            else template.resolve(),
            template_manifest_path=(root / manifest).resolve()
            if not manifest.is_absolute()
            else manifest.resolve(),
            render_on_generate=_as_bool(
                os.getenv("AEP_RENDER_ON_GENERATE"), default=False
            ),
            libreoffice_path=Path(libreoffice).resolve() if libreoffice else None,
            uploads_dir=(runtime_dir / "_pipeline-inputs").resolve(),
            generated_dir=(runtime_dir / "_pipeline-work").resolve(),
            outputs_dir=(runtime_dir / "_pipeline-outputs").resolve(),
            allow_synthetic_template_fallback=_as_bool(
                os.getenv("AEP_ALLOW_SYNTHETIC_TEMPLATE_FALLBACK"),
                default=False,
            ),
            compatibility_profile_path=(
                (root / compatibility_profile).resolve()
                if compatibility_profile is not None
                and not compatibility_profile.is_absolute()
                else compatibility_profile.resolve()
                if compatibility_profile is not None
                else None
            ),
            runtime_dir=runtime_dir,
            job_ttl_seconds=ttl_seconds,
            allowed_origins=_allowed_origins(
                os.getenv(
                    "AEP_ALLOWED_ORIGINS",
                    "https://kinhoog.github.io",
                )
            ),
            require_origin=_as_bool(
                os.getenv("AEP_REQUIRE_ORIGIN"),
                default=True,
            ),
            hosted_template_base64_file=(
                Path(hosted_template_value).expanduser().resolve()
                if hosted_template_value
                else None
            ),
            hosted_template_base64_files=hosted_template_files,
            hosted_template_manifest_base64_file=(
                Path(hosted_manifest_value).expanduser().resolve()
                if hosted_manifest_value
                else None
            ),
            hosted_compatibility_profile_base64_file=(
                Path(hosted_compatibility_value).expanduser().resolve()
                if hosted_compatibility_value
                else None
            ),
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.uploads_dir,
            self.generated_dir,
            self.outputs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
