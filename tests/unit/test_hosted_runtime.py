from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings
from app.services.hosted_template import (
    HostedTemplateError,
    materialize_hosted_template,
    remove_materialized_template,
)
from app.services.pipeline import (
    CompatibilityProfileError,
    _load_compatibility_profile,
)
from scripts.prepare_private_template import prepare
from tests.unit.test_private_template_sanitization import (
    _build_synthetic_reference,
)


def _settings(root: Path) -> Settings:
    runtime = root / "runtime"
    return Settings(
        base_dir=root,
        host="0.0.0.0",
        port=8000,
        max_file_bytes=1024 * 1024,
        job_ttl_minutes=15,
        template_path=root / "private_templates" / "aep_template.docx",
        template_manifest_path=(
            root / "private_templates" / "aep_template.manifest.json"
        ),
        render_on_generate=False,
        libreoffice_path=None,
        uploads_dir=runtime / "_pipeline-inputs",
        generated_dir=runtime / "_pipeline-work",
        outputs_dir=runtime / "_pipeline-outputs",
        runtime_dir=runtime,
        job_ttl_seconds=900,
    )


def _secret(path: Path, payload: bytes) -> Path:
    path.write_bytes(base64.b64encode(payload))
    return path


def _sanitized_pair(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "referencia-sintetica.docx"
    template = tmp_path / "template-sintetico.docx"
    _build_synthetic_reference(source)
    return prepare(source, template)


def test_settings_prefers_render_port_and_temporary_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORT", "10443")
    monkeypatch.setenv("AEP_PORT", "8001")
    monkeypatch.setenv("AEP_RUNTIME_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("AEP_JOB_TTL_SECONDS", "900")
    monkeypatch.setenv(
        "AEP_ALLOWED_ORIGINS",
        "https://kinhoog.github.io",
    )

    settings = Settings.from_env(tmp_path)

    assert settings.host == "0.0.0.0"
    assert settings.port == 10443
    assert settings.runtime_dir == (tmp_path / "jobs").resolve()
    assert settings.job_ttl_seconds == 900
    assert settings.job_ttl_minutes == 15
    assert settings.allowed_origins == ("https://kinhoog.github.io",)


def test_settings_rejects_wildcard_cors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AEP_ALLOWED_ORIGINS", "*")

    with pytest.raises(ValueError, match="curinga"):
        Settings.from_env(tmp_path)


def test_hosted_template_and_optional_profile_are_materialized_privately(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_pair(tmp_path)
    stale_runtime = tmp_path / "runtime" / ".hosted-template-stale"
    stale_runtime.mkdir(parents=True)
    (stale_runtime / "private.bin").write_bytes(b"stale")
    profile = {
        "schema_version": 1,
        "mode": "pilot_reference",
        "analysis_mode": "integrated",
        "input_fingerprint": "0" * 64,
        "included_ergo_ordinals": [1],
        "omitted_ergo_ordinals": [],
    }
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_file=_secret(
            tmp_path / "template.b64", template.read_bytes()
        ),
        hosted_template_manifest_base64_file=_secret(
            tmp_path / "manifest.b64", manifest.read_bytes()
        ),
        hosted_compatibility_profile_base64_file=_secret(
            tmp_path / "compatibility.b64",
            json.dumps(profile).encode("utf-8"),
        ),
    )

    materialized = materialize_hosted_template(settings)
    try:
        assert not stale_runtime.exists()
        assert materialized.runtime_dir is not None
        assert materialized.settings.template_path.parent == (
            materialized.runtime_dir
        )
        assert materialized.settings.template_path.read_bytes() == (
            template.read_bytes()
        )
        assert materialized.settings.compatibility_profile_path is not None
        assert json.loads(
            materialized.settings.compatibility_profile_path.read_text(
                encoding="utf-8"
            )
        ) == profile
        assert materialized.settings.trusted_private_runtime_dir == (
            materialized.runtime_dir
        )
        assert _load_compatibility_profile(materialized.settings) == profile
    finally:
        runtime_dir = materialized.runtime_dir
        remove_materialized_template(runtime_dir)
    assert runtime_dir is not None and not runtime_dir.exists()


def test_hosted_template_with_invalid_manifest_hash_fails_closed(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_pair(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["template_sha256"] = "0" * 64
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_file=_secret(
            tmp_path / "template.b64", template.read_bytes()
        ),
        hosted_template_manifest_base64_file=_secret(
            tmp_path / "manifest.b64",
            json.dumps(payload).encode("utf-8"),
        ),
    )

    with pytest.raises(HostedTemplateError) as raised:
        materialize_hosted_template(settings)

    assert raised.value.code == "hosted_template_invalid"
    runtime = settings.runtime_dir
    assert runtime is not None
    assert list(runtime.glob(".hosted-template-*")) == []


def test_compatibility_profile_outside_trusted_roots_is_rejected(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "public" / "profile.json"
    outside.parent.mkdir()
    outside.write_text('{"schema_version": 1}', encoding="utf-8")
    settings = replace(
        _settings(tmp_path),
        compatibility_profile_path=outside,
        trusted_private_runtime_dir=tmp_path / "runtime-private",
    )

    with pytest.raises(CompatibilityProfileError) as raised:
        _load_compatibility_profile(settings)

    assert raised.value.code == "compatibility_profile_unavailable"
