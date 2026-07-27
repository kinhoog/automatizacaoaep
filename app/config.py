from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


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

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        root = (base_dir or Path(__file__).resolve().parents[1]).resolve()
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
        return cls(
            base_dir=root,
            host=os.getenv("AEP_HOST", "127.0.0.1"),
            port=int(os.getenv("AEP_PORT", "8000")),
            max_file_bytes=int(os.getenv("AEP_MAX_FILE_MB", "25")) * 1024 * 1024,
            job_ttl_minutes=int(os.getenv("AEP_JOB_TTL_MINUTES", "60")),
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
            uploads_dir=(root / "uploads").resolve(),
            generated_dir=(root / "generated").resolve(),
            outputs_dir=(root / "outputs").resolve(),
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
        )

    def ensure_directories(self) -> None:
        for directory in (
            self.uploads_dir,
            self.generated_dir,
            self.outputs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
