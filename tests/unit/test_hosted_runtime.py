from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config import Settings
from app.services.hosted_template import (
    HostedTemplateError,
    MAX_TEMPLATE_PART_BASE64_BYTES,
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


def test_settings_preserves_ordered_template_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = (tmp_path / "template.part-001.b64").resolve()
    second = (tmp_path / "template.part-002.b64").resolve()
    monkeypatch.setenv(
        "AEP_HOSTED_TEMPLATE_BASE64_FILES",
        f"{first},{second}",
    )

    settings = Settings.from_env(tmp_path)

    assert settings.hosted_template_base64_files == (first, second)
    assert settings.hosted_template_base64_file is None


def test_settings_rejects_ambiguous_legacy_and_split_template_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    part = (tmp_path / "template.part-001.b64").resolve()
    legacy = (tmp_path / "template-legacy.b64").resolve()
    monkeypatch.setenv("AEP_HOSTED_TEMPLATE_BASE64_FILES", str(part))
    monkeypatch.setenv("AEP_HOSTED_TEMPLATE_BASE64_FILE", str(legacy))

    with pytest.raises(ValueError, match="não podem ser configuradas juntas"):
        Settings.from_env(tmp_path)


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "/tmp/parte-1.b64,,/tmp/parte-2.b64",
        "parte-relativa.b64",
    ],
)
def test_settings_rejects_unsafe_template_part_lists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: str,
) -> None:
    monkeypatch.setenv("AEP_HOSTED_TEMPLATE_BASE64_FILES", configured)

    with pytest.raises(ValueError, match="AEP_HOSTED_TEMPLATE_BASE64_FILES"):
        Settings.from_env(tmp_path)


def test_settings_rejects_duplicate_absolute_template_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = (tmp_path / "repetida.b64").resolve()
    monkeypatch.setenv(
        "AEP_HOSTED_TEMPLATE_BASE64_FILES",
        f"{repeated},{repeated}",
    )

    with pytest.raises(ValueError, match="duplicados"):
        Settings.from_env(tmp_path)


def test_split_hosted_template_is_concatenated_in_declared_order(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_pair(tmp_path)
    encoded = base64.b64encode(template.read_bytes())
    first_cut = len(encoded) // 3 + 1
    second_cut = (2 * len(encoded)) // 3 + 2
    parts = (
        tmp_path / "template.part-001.b64",
        tmp_path / "template.part-002.b64",
        tmp_path / "template.part-003.b64",
    )
    for path, payload in zip(
        parts,
        (
            encoded[:first_cut],
            encoded[first_cut:second_cut],
            encoded[second_cut:],
        ),
        strict=True,
    ):
        path.write_bytes(payload)
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_files=parts,
        hosted_template_manifest_base64_file=_secret(
            tmp_path / "manifest.b64", manifest.read_bytes()
        ),
    )

    materialized = materialize_hosted_template(settings)
    try:
        assert materialized.settings.template_path.read_bytes() == (
            template.read_bytes()
        )
    finally:
        remove_materialized_template(materialized.runtime_dir)


def test_provider_managed_secret_symlinks_are_resolved_and_validated(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_pair(tmp_path)
    template_target = _secret(
        tmp_path / "template-target.b64", template.read_bytes()
    )
    manifest_target = _secret(
        tmp_path / "manifest-target.b64", manifest.read_bytes()
    )
    template_link = tmp_path / "template-link.b64"
    manifest_link = tmp_path / "manifest-link.b64"
    try:
        template_link.symlink_to(template_target)
        manifest_link.symlink_to(manifest_target)
    except OSError:
        pytest.skip("O host local não permite criar links simbólicos.")
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_files=(template_link,),
        hosted_template_manifest_base64_file=manifest_link,
    )

    materialized = materialize_hosted_template(settings)
    try:
        assert materialized.settings.template_path.read_bytes() == (
            template.read_bytes()
        )
    finally:
        remove_materialized_template(materialized.runtime_dir)


def test_materializer_rejects_ambiguous_legacy_and_split_template_secrets(
    tmp_path: Path,
) -> None:
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_file=(tmp_path / "legacy.b64").resolve(),
        hosted_template_base64_files=(
            (tmp_path / "template.part-001.b64").resolve(),
        ),
    )

    with pytest.raises(HostedTemplateError) as raised:
        materialize_hosted_template(settings)

    assert raised.value.code == "hosted_template_secret_invalid"


@pytest.mark.parametrize("failure", ["missing", "empty", "duplicate"])
def test_split_hosted_template_rejects_invalid_part_sets(
    tmp_path: Path,
    failure: str,
) -> None:
    template, manifest = _sanitized_pair(tmp_path)
    encoded = base64.b64encode(template.read_bytes())
    first = (tmp_path / "template.part-001.b64").resolve()
    second = (tmp_path / "template.part-002.b64").resolve()
    first.write_bytes(encoded[: len(encoded) // 2])
    if failure == "empty":
        second.write_bytes(b"")
    elif failure != "missing":
        second.write_bytes(encoded[len(encoded) // 2 :])
    parts = (first, first) if failure == "duplicate" else (first, second)
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_files=parts,
        hosted_template_manifest_base64_file=_secret(
            tmp_path / "manifest.b64", manifest.read_bytes()
        ),
    )

    with pytest.raises(HostedTemplateError) as raised:
        materialize_hosted_template(settings)

    assert raised.value.code in {
        "hosted_template_secret_invalid",
        "hosted_template_secret_unavailable",
    }


def test_split_hosted_template_rejects_part_above_provider_limit(
    tmp_path: Path,
) -> None:
    oversized = (tmp_path / "template.part-001.b64").resolve()
    oversized.write_bytes(b"A" * (MAX_TEMPLATE_PART_BASE64_BYTES + 1))
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_files=(oversized,),
        hosted_template_manifest_base64_file=(
            tmp_path / "manifest-not-read.b64"
        ).resolve(),
    )

    with pytest.raises(HostedTemplateError) as raised:
        materialize_hosted_template(settings)

    assert raised.value.code == "hosted_template_secret_size"


def test_split_hosted_template_rejects_aggregate_above_runtime_limit(
    tmp_path: Path,
) -> None:
    parts: list[Path] = []
    for index in range(33):
        part = (tmp_path / f"template.part-{index:03d}.b64").resolve()
        part.write_bytes(b"A" * MAX_TEMPLATE_PART_BASE64_BYTES)
        parts.append(part)
    settings = replace(
        _settings(tmp_path),
        hosted_template_base64_files=tuple(parts),
        hosted_template_manifest_base64_file=(
            tmp_path / "manifest-not-read.b64"
        ).resolve(),
    )

    with pytest.raises(HostedTemplateError) as raised:
        materialize_hosted_template(settings)

    assert raised.value.code == "hosted_template_secret_size"


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
