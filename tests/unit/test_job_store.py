from __future__ import annotations

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.services.job_store import JobNotFoundError, JobStore


def _settings(root: Path, *, ttl_minutes: int = 5) -> Settings:
    return Settings(
        base_dir=root,
        host="127.0.0.1",
        port=8000,
        max_file_bytes=1024 * 1024,
        job_ttl_minutes=ttl_minutes,
        template_path=root / "private_templates" / "aep_template.docx",
        template_manifest_path=(
            root / "private_templates" / "aep_template.manifest.json"
        ),
        render_on_generate=False,
        libreoffice_path=None,
        uploads_dir=root / "uploads",
        generated_dir=root / "generated",
        outputs_dir=root / "outputs",
    )


def test_ttl_cleanup_removes_only_expired_job_directories(
    tmp_path: Path,
) -> None:
    store = JobStore(_settings(tmp_path))
    expired = store.create()
    active = store.create()
    for record in (expired, active):
        (record.input_dir / "entrada.tmp").write_text(
            "DADO SINTETICO",
            encoding="utf-8",
        )
        (record.work_dir / "trabalho.tmp").write_text(
            "DADO SINTETICO",
            encoding="utf-8",
        )
        (record.output_dir / "saida.tmp").write_text(
            "DADO SINTETICO",
            encoding="utf-8",
        )

    now = datetime.now(UTC)
    expired.updated_at = now - timedelta(minutes=6)
    active.updated_at = now - timedelta(minutes=4)
    removed = store.cleanup_expired(now)

    assert removed == [expired.id]
    assert not expired.input_dir.exists()
    assert not expired.work_dir.exists()
    assert not expired.output_dir.exists()
    assert active.input_dir.is_dir()
    assert active.work_dir.is_dir()
    assert active.output_dir.is_dir()
    assert store.get(active.id) is active


def test_cleanup_keeps_job_for_retry_when_windows_handle_blocks_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = JobStore(_settings(tmp_path))
    expired = store.create()
    expired.updated_at = datetime.now(UTC) - timedelta(minutes=6)
    original_rmtree = shutil.rmtree

    def blocked_rmtree(path: Path) -> None:
        if Path(path).resolve() == expired.input_dir.resolve():
            raise PermissionError("arquivo sintético em uso")
        original_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", blocked_rmtree)
    first = store.cleanup_expired(datetime.now(UTC))

    assert first == []
    assert store.get(expired.id) is expired
    assert expired.input_dir.is_dir()

    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    second = store.cleanup_expired(datetime.now(UTC))

    assert second == [expired.id]
    with pytest.raises(JobNotFoundError):
        store.get(expired.id)


def test_startup_scan_removes_only_old_uuid_orphans(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_directories()
    orphan_id = str(uuid.uuid4())
    recent_id = str(uuid.uuid4())
    old_timestamp = (
        datetime.now(UTC) - timedelta(minutes=6)
    ).timestamp()
    for root in (
        settings.uploads_dir,
        settings.generated_dir,
        settings.outputs_dir,
    ):
        old = root / orphan_id
        old.mkdir()
        (old / "temporario.txt").write_text("SINTÉTICO", encoding="utf-8")
        os.utime(old, (old_timestamp, old_timestamp))
        (root / recent_id).mkdir()
        (root / "nao-e-job").mkdir()

    JobStore(settings)

    for root in (
        settings.uploads_dir,
        settings.generated_dir,
        settings.outputs_dir,
    ):
        assert not (root / orphan_id).exists()
        assert (root / recent_id).is_dir()
        assert (root / "nao-e-job").is_dir()
