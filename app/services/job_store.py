from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger("aep.jobs")


class JobNotFoundError(KeyError):
    pass


@dataclass(slots=True)
class JobRecord:
    id: str
    created_at: datetime
    updated_at: datetime
    status: str
    input_dir: Path
    work_dir: Path
    output_dir: Path
    stage: str = "recebendo"
    progress: int = 0
    files: dict[str, Path] = field(default_factory=dict)
    inspections: dict[str, dict[str, Any]] = field(default_factory=dict)
    normalized: Any | None = None
    validation_payload: dict[str, Any] | None = None
    document_path: Path | None = None
    validation_report_path: Path | None = None
    error: str | None = None

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "document_ready": bool(
                self.document_path and self.document_path.is_file()
            ),
            "validation_report_ready": bool(
                self.validation_report_path
                and self.validation_report_path.is_file()
            ),
            "error": self.error,
        }


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_directories()
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.RLock()
        self.cleanup_orphans()

    def create(self) -> JobRecord:
        job_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        record = JobRecord(
            id=job_id,
            created_at=now,
            updated_at=now,
            status="receiving",
            input_dir=self.settings.uploads_dir / job_id,
            work_dir=self.settings.generated_dir / job_id,
            output_dir=self.settings.outputs_dir / job_id,
        )
        for path in (record.input_dir, record.work_dir, record.output_dir):
            path.mkdir(parents=True, exist_ok=False)
        with self._lock:
            self._jobs[job_id] = record
        return record

    def get(self, job_id: str) -> JobRecord:
        try:
            uuid.UUID(job_id)
        except (ValueError, AttributeError) as exc:
            raise JobNotFoundError(job_id) from exc
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        progress: int | None = None,
        error: str | None = None,
    ) -> JobRecord:
        with self._lock:
            record = self.get(job_id)
            if status is not None:
                record.status = status
            if stage is not None:
                record.stage = stage
            if progress is not None:
                record.progress = max(0, min(100, progress))
            if error is not None:
                record.error = error
            record.touch()
            return record

    def remove_inputs(self, job_id: str) -> None:
        record = self.get(job_id)
        if not self._safe_rmtree(record.input_dir, self.settings.uploads_dir):
            logger.warning(
                "Falha ao remover entradas job=%s; limpeza será repetida.",
                job_id,
            )

    def cleanup_expired(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(minutes=self.settings.job_ttl_minutes)
        with self._lock:
            expired = [
                job_id
                for job_id, record in self._jobs.items()
                if record.updated_at < cutoff
            ]
        removed: list[str] = []
        for job_id in expired:
            with self._lock:
                record = self._jobs.get(job_id)
            if record is None:
                continue
            deleted = all(
                (
                    self._safe_rmtree(
                        record.input_dir, self.settings.uploads_dir
                    ),
                    self._safe_rmtree(
                        record.work_dir, self.settings.generated_dir
                    ),
                    self._safe_rmtree(
                        record.output_dir, self.settings.outputs_dir
                    ),
                )
            )
            if deleted:
                with self._lock:
                    if self._jobs.get(job_id) is record:
                        self._jobs.pop(job_id, None)
                removed.append(job_id)
            else:
                logger.warning(
                    "Falha ao limpar job expirado=%s; nova tentativa agendada.",
                    job_id,
                )
        removed.extend(
            job_id
            for job_id in self.cleanup_orphans(current)
            if job_id not in removed
        )
        return removed

    def cleanup_orphans(self, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(minutes=self.settings.job_ttl_minutes)
        with self._lock:
            active_ids = set(self._jobs)
        roots = (
            self.settings.uploads_dir,
            self.settings.generated_dir,
            self.settings.outputs_dir,
        )
        candidates: dict[str, list[Path]] = {}
        for root in roots:
            try:
                children = tuple(root.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink() or not child.is_dir():
                    continue
                try:
                    canonical = str(uuid.UUID(child.name))
                except (ValueError, AttributeError):
                    continue
                if canonical != child.name or canonical in active_ids:
                    continue
                candidates.setdefault(canonical, []).append(child)

        removed: list[str] = []
        for job_id, paths in sorted(candidates.items()):
            try:
                if any(
                    datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
                    >= cutoff
                    for path in paths
                ):
                    continue
            except OSError:
                continue
            deleted = True
            for root in roots:
                path = root / job_id
                if not self._safe_rmtree(path, root):
                    deleted = False
            if deleted:
                removed.append(job_id)
            else:
                logger.warning(
                    "Falha ao limpar diretório órfão=%s; nova tentativa agendada.",
                    job_id,
                )
        return removed

    @staticmethod
    def _safe_rmtree(
        path: Path,
        allowed_root: Path,
        *,
        attempts: int = 3,
    ) -> bool:
        if path.is_symlink():
            return False
        try:
            resolved_path = path.resolve()
            resolved_root = allowed_root.resolve()
        except OSError:
            return False
        if resolved_path == resolved_root or resolved_root not in resolved_path.parents:
            raise RuntimeError("Tentativa de limpeza fora do diretório permitido.")
        for attempt in range(max(1, attempts)):
            try:
                shutil.rmtree(resolved_path)
                return True
            except FileNotFoundError:
                return True
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.02 * (attempt + 1))
        return False
