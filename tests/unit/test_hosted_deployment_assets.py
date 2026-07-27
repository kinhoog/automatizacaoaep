from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.prepare_hosted_template_secret import (
    COMPATIBILITY_PROFILE_SECRET_NAME,
    DEFAULT_TEMPLATE_PART_BYTES,
    MANIFEST_SECRET_NAME,
    METADATA_NAME,
    RENDER_SECRET_FILE_MAX_BYTES,
    TEMPLATE_SECRET_NAME,
    TEMPLATE_PART_GLOB,
    HostedTemplateSecretError,
    main,
    prepare_hosted_template_secret,
)
from scripts.prepare_private_template import prepare
from tests.unit.test_private_template_sanitization import (
    _build_synthetic_reference,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sanitized_template(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "referencia_sintetica_sem_validade.docx"
    template = tmp_path / "template_sintetico_sanitizado.docx"
    _build_synthetic_reference(source)
    return prepare(source, template)


def test_hosted_template_secret_round_trip_and_private_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    template, manifest = _sanitized_template(tmp_path)
    private_output = (
        PROJECT_ROOT
        / "private_templates"
        / "pytest-hosted-secret-synthetic"
    )
    compatibility_profile = tmp_path / "perfil_sintetico_sem_validade.json"
    compatibility_profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "pilot_reference",
                "analysis_mode": "integrated",
                "input_fingerprint": "a" * 64,
                "included_ergo_ordinals": [1, 2, 3],
                "omitted_ergo_ordinals": [4],
            }
        ),
        encoding="utf-8",
    )
    try:
        bundle = prepare_hosted_template_secret(
            template,
            manifest,
            compatibility_profile_path=compatibility_profile,
            output_dir=private_output,
        )
        template_parts = tuple(
            sorted(private_output.glob(TEMPLATE_PART_GLOB))
        )
        manifest_secret = private_output / MANIFEST_SECRET_NAME
        metadata_path = private_output / METADATA_NAME
        compatibility_secret = (
            private_output / COMPATIBILITY_PROFILE_SECRET_NAME
        )

        assert template_parts
        assert not (private_output / TEMPLATE_SECRET_NAME).exists()
        joined_template_base64 = b"".join(
            path.read_bytes() for path in template_parts
        )
        assert base64.b64decode(
            joined_template_base64, validate=True
        ) == template.read_bytes()
        assert all(
            0 < path.stat().st_size <= DEFAULT_TEMPLATE_PART_BYTES
            for path in template_parts
        )
        assert base64.b64decode(manifest_secret.read_text().strip()) == (
            manifest.read_bytes()
        )
        assert base64.b64decode(
            compatibility_secret.read_text().strip()
        ) == compatibility_profile.read_bytes()
        assert bundle.total_base64_bytes == (
            len(joined_template_base64)
            + len(manifest_secret.read_text().strip())
            + len(compatibility_secret.read_text().strip())
        )
        assert tuple(
            artifact.filename for artifact in bundle.template_parts
        ) == tuple(path.name for path in template_parts)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["remaining_bytes"] > 0
        assert metadata["render_paths"] == {
            "compatibility_profile": (
                "/etc/secrets/aep_compatibility_profile.json.b64"
            ),
            "manifest": "/etc/secrets/aep_template.manifest.json.b64",
            "template_parts": [
                f"/etc/secrets/{path.name}" for path in template_parts
            ],
        }

        assert (
            main(
                [
                    str(template),
                    "--manifest",
                    str(manifest),
                    "--compatibility-profile",
                    str(compatibility_profile),
                    "--output-dir",
                    str(private_output),
                ]
            )
            == 0
        )
        stdout = capsys.readouterr().out
        assert "preparados e validados" in stdout
        assert str(template) not in stdout
        assert str(manifest) not in stdout
        assert str(compatibility_profile) not in stdout
        assert "referencia_sintetica" not in stdout
        assert base64.b64encode(template.read_bytes())[:40].decode() not in stdout
    finally:
        shutil.rmtree(private_output, ignore_errors=True)


def test_hosted_template_secret_rejects_hash_mismatch_before_writing(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_template(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["template_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    private_output = (
        PROJECT_ROOT
        / "private_templates"
        / "pytest-hosted-secret-invalid"
    )
    try:
        with pytest.raises(
            HostedTemplateSecretError,
            match="não passou na validação",
        ):
            prepare_hosted_template_secret(
                template,
                manifest,
                output_dir=private_output,
            )
        assert not private_output.exists()
    finally:
        shutil.rmtree(private_output, ignore_errors=True)


def test_hosted_template_secret_enforces_provider_limit(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_template(tmp_path)
    private_output = (
        PROJECT_ROOT
        / "private_templates"
        / "pytest-hosted-secret-limit"
    )
    try:
        with pytest.raises(HostedTemplateSecretError, match="excedem"):
            prepare_hosted_template_secret(
                template,
                manifest,
                output_dir=private_output,
                provider_limit_bytes=100,
            )
        assert not private_output.exists()
    finally:
        shutil.rmtree(private_output, ignore_errors=True)


def test_hosted_template_secret_enforces_individual_file_limit(
    tmp_path: Path,
) -> None:
    template, manifest = _sanitized_template(tmp_path)
    private_output = (
        PROJECT_ROOT
        / "private_templates"
        / "pytest-hosted-secret-individual-limit"
    )
    try:
        with pytest.raises(
            HostedTemplateSecretError,
            match="limite individual",
        ):
            prepare_hosted_template_secret(
                template,
                manifest,
                output_dir=private_output,
                secret_file_limit_bytes=8 * 1024,
                template_part_bytes=8 * 1024,
            )
        assert not private_output.exists()
    finally:
        shutil.rmtree(private_output, ignore_errors=True)


def test_dockerfile_and_render_blueprint_are_hardened_for_hosting() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    blueprint = (PROJECT_ROOT / "render.yaml").read_text(encoding="utf-8")
    dockerignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")

    for package in (
        "libreoffice-writer",
        "fonts-crosextra-carlito",
        "fonts-crosextra-caladea",
        "fonts-liberation",
    ):
        assert package in dockerfile
    assert "USER aep" in dockerfile
    assert "groupadd --gid 1000 render-secrets" in dockerfile
    assert "--groups render-secrets" in dockerfile
    assert "/tmp/aep-jobs" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "pipeline_ready" in dockerfile
    assert "${PORT:-10000}" in dockerfile
    copy_lines = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.lstrip().startswith("COPY ")
    ]
    assert copy_lines == [
        "COPY pyproject.toml README.md ./",
        "COPY app ./app",
    ]

    assert "healthCheckPath: /api/health" in blueprint
    assert "AEP_ALLOWED_ORIGINS" in blueprint
    assert "https://kinhoog.github.io" in blueprint
    assert "AEP_REQUIRE_ORIGIN" in blueprint
    assert "AEP_JOB_TTL_SECONDS" in blueprint
    assert 'value: "900"' in blueprint
    assert "AEP_GENERATION_STALE_SECONDS" in blueprint
    assert 'value: "600"' in blueprint
    assert "AEP_HOSTED_TEMPLATE_BASE64_FILES" in blueprint
    assert "/etc/secrets/aep_template.docx.b64.part01" in blueprint
    assert "/etc/secrets/aep_template.docx.b64.part02" in blueprint
    assert "AEP_HOSTED_TEMPLATE_BASE64_FILE\n" not in blueprint
    assert DEFAULT_TEMPLATE_PART_BYTES < RENDER_SECRET_FILE_MAX_BYTES
    assert "/etc/secrets/aep_template.manifest.json.b64" in blueprint
    assert "/etc/secrets/aep_compatibility_profile.json.b64" in blueprint
    assert "numInstances: 1" in blueprint
    assert "AEP_TEMPLATE_BASE64=" not in blueprint

    for private_entry in (
        "local_samples",
        "private_templates",
        "uploads",
        "outputs",
        "generated",
        "*.docx",
        "*.xlsx",
    ):
        assert private_entry in dockerignore

    frontend_payload = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "frontend").rglob("*")
        if path.is_file()
        and path.suffix.casefold()
        in {".css", ".html", ".js", ".json", ".svg", ".txt"}
    )
    for private_marker in (
        "AEP_HOSTED_TEMPLATE_BASE64_FILE",
        "AEP_HOSTED_TEMPLATE_BASE64_FILES",
        "AEP_HOSTED_TEMPLATE_MANIFEST_BASE64_FILE",
        "AEP_HOSTED_COMPATIBILITY_PROFILE_BASE64_FILE",
        "aep_template.docx.b64",
        "aep_template.manifest.json.b64",
        "aep_compatibility_profile.json.b64",
        "private_templates/",
        "/etc/secrets/",
    ):
        assert private_marker not in frontend_payload


def test_no_private_documents_or_hosted_secrets_are_tracked() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    tracked = [
        Path(item.decode("utf-8"))
        for item in completed.stdout.split(b"\0")
        if item
    ]
    private_roots = {
        "local_samples",
        "private_templates",
        "uploads",
        "outputs",
        "generated",
    }
    assert not [
        path
        for path in tracked
        if path.parts and path.parts[0] in private_roots
    ]
    assert not [
        path
        for path in tracked
        if path.name
        in {
            TEMPLATE_SECRET_NAME,
            MANIFEST_SECRET_NAME,
            COMPATIBILITY_PROFILE_SECRET_NAME,
            METADATA_NAME,
        }
    ]
