"""Normalized, auditable domain model for the AEP compiler.

Binary payloads, private filenames and local paths are runtime-only fields and
are always excluded from ``model_dump`` / ``model_dump_json``.  This keeps the
normal form useful for local audits without accidentally copying source
documents into JSON reports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


class AEPBaseModel(BaseModel):
    """Strict base model shared by all public contracts."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        use_enum_values=False,
    )

    def audit_dict(self) -> dict[str, Any]:
        """Return the JSON-safe audit representation.

        Runtime-only fields use ``exclude=True`` at field definition time, so
        callers cannot accidentally re-enable them through a shallow exclude.
        """

        return self.model_dump(mode="json", exclude_none=True)

    def audit_json(self, *, indent: int = 2) -> str:
        """Serialize the audit representation as UTF-8-friendly JSON."""

        return self.model_dump_json(exclude_none=True, indent=indent)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AnalysisMode(str, Enum):
    INTEGRATED = "integrated"
    SEPARATE = "separate"


class FileKind(str, Enum):
    XLSX = "xlsx"
    DOCX = "docx"
    HTML_DOC = "html_doc"
    OLE_DOC = "ole_doc"
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    UNKNOWN = "unknown"


class ContentKind(str, Enum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    IMAGE = "image"
    LIST_ITEM = "list_item"


class ImageRole(str, Enum):
    GENERAL_PANEL = "general_panel"
    GHE_PANEL = "ghe_panel"
    CHART = "chart"
    RADAR = "radar"
    RISK_MATRIX = "risk_matrix"
    DOMAIN_SUMMARY = "domain_summary"
    FAVORABILITY = "favorability"
    OTHER = "other"


class TechnicalCategory(str, Enum):
    OVERVIEW = "overview"
    POSITIVE_POINTS = "positive_points"
    CRITICAL_POINTS = "critical_points"
    IMPROVEMENTS = "improvements"
    RELEVANT_QUESTIONS = "relevant_questions"
    FAVORABILITY = "favorability"
    CLASSIFICATION = "classification"
    TECHNICAL_READING = "technical_reading"
    PRIORITIZATION = "prioritization"
    ACTION_PLAN = "action_plan"
    CONCLUSION = "conclusion"
    OTHER = "other"


class ReconciliationStatus(str, Enum):
    AUTO_MATCHED = "auto_matched"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"
    NOT_APPLICABLE = "not_applicable"


class ArtifactMetadata(AEPBaseModel):
    """Safe metadata for an uploaded artifact.

    ``original_filename`` and ``runtime_path`` are needed locally but may carry
    confidential identifiers, so they never appear in audit serialization.
    """

    artifact_id: str
    kind: FileKind = FileKind.UNKNOWN
    extension: str
    media_type: str | None = None
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    original_filename: str | None = Field(default=None, exclude=True)
    runtime_path: Path | None = Field(default=None, exclude=True)

    @field_validator("extension")
    @classmethod
    def normalize_extension(cls, value: str) -> str:
        value = value.lower().strip()
        return value if value.startswith(".") else f".{value}"


class ImageAsset(AEPBaseModel):
    """Image occurrence and its semantic placement information."""

    image_id: str
    order: int = Field(ge=0)
    role: ImageRole = ImageRole.OTHER
    sha256: str
    width_px: int | None = Field(default=None, ge=1)
    height_px: int | None = Field(default=None, ge=1)
    image_format: str | None = None
    content_type: str | None = None
    caption: str | None = None
    context: str | None = None
    ghe_code_hint: str | None = None
    ghe_name_hint: str | None = None
    official_ghe_code: str | None = None
    source_part: str | None = Field(default=None, exclude=True)
    blob: bytes | None = Field(default=None, exclude=True, repr=False)
    runtime_path: Path | None = Field(default=None, exclude=True, repr=False)


class GHE(AEPBaseModel):
    """Official homogeneous exposure group.

    No employee/person field exists by design. ``source_rows`` contains only
    spreadsheet row numbers and is omitted from audit output.
    """

    code: str
    name: str
    sectors: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    population: int = Field(ge=0)
    source_rows: list[int] = Field(default_factory=list, exclude=True)

    @computed_field
    @property
    def canonical_code(self) -> str:
        import re

        match = re.search(r"(?i)\bGHE\s*[-_:.]?\s*0*(\d+)\b", self.code)
        if not match:
            return self.code.strip().upper()
        return f"GHE {int(match.group(1)):02d}"

    @field_validator("sectors", "roles")
    @classmethod
    def stable_unique_values(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join(str(value).split())
            key = cleaned.casefold()
            if cleaned and key not in seen:
                result.append(cleaned)
                seen.add(key)
        return result


class GHEExtractionResult(AEPBaseModel):
    ghes: list[GHE]
    source_sheet: str
    header_row: int = Field(ge=1)
    ignored_person_columns: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def total_population(self) -> int:
        return sum(ghe.population for ghe in self.ghes)


class CompanyData(AEPBaseModel):
    legal_name: str
    registration_number: str | None = None
    registration_card: ImageAsset | None = None
    logo: ImageAsset | None = None

    @field_validator("legal_name")
    @classmethod
    def require_legal_name(cls, value: str) -> str:
        if not value:
            raise ValueError("legal_name must not be empty")
        return value


class CompatibilityException(AEPBaseModel):
    """Explicit, private-only exception used to reproduce a reference pilot."""

    mode: str
    reason: str
    included_ergo_source_ids: list[str] = Field(default_factory=list)
    omitted_ergo_source_ids: list[str] = Field(default_factory=list)
    private_only: bool = True
    acknowledged: bool = False

    @model_validator(mode="after")
    def ensure_traceability(self) -> "CompatibilityException":
        overlap = set(self.included_ergo_source_ids) & set(
            self.omitted_ergo_source_ids
        )
        if overlap:
            raise ValueError("an Ergo block cannot be both included and omitted")
        if not self.reason:
            raise ValueError("compatibility exceptions require a reason")
        return self


class DocumentData(AEPBaseModel):
    competence: str
    ergo_base_date: str
    psychosocial_base_date: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    compatibility: CompatibilityException | None = None


class ErgoContentElement(AEPBaseModel):
    order: int = Field(ge=0)
    kind: ContentKind
    text: str | None = None
    rows: list[list[str]] = Field(default_factory=list)
    image: ImageAsset | None = None

    @model_validator(mode="after")
    def require_matching_payload(self) -> "ErgoContentElement":
        if self.kind == ContentKind.TABLE and not self.rows:
            raise ValueError("table elements require rows")
        if self.kind == ContentKind.IMAGE and self.image is None:
            raise ValueError("image elements require image metadata")
        if (
            self.kind not in {ContentKind.TABLE, ContentKind.IMAGE}
            and not self.text
        ):
            raise ValueError("textual elements require text")
        return self


class ErgoBlock(AEPBaseModel):
    source_id: str
    order: int = Field(ge=0)
    title: str
    source_code: str | None = None
    source_name: str | None = None
    elements: list[ErgoContentElement] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    answers: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)


class ErgoReport(AEPBaseModel):
    detected_format: FileKind
    preamble: list[ErgoContentElement] = Field(default_factory=list)
    blocks: list[ErgoBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PsychosocialBlock(AEPBaseModel):
    block_id: str
    order: int = Field(ge=0)
    title: str
    ghe_code_hint: str | None = None
    ghe_name_hint: str | None = None
    official_ghe_code: str | None = None
    headings: list[str] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)


class PsychosocialReport(AEPBaseModel):
    headings: list[str] = Field(default_factory=list)
    images: list[ImageAsset] = Field(default_factory=list)
    blocks: list[PsychosocialBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TechnicalSection(AEPBaseModel):
    section_id: str
    order: int = Field(ge=0)
    title: str
    category: TechnicalCategory = TechnicalCategory.OTHER
    source_role: str
    ghe_code_hint: str | None = None
    ghe_name_hint: str | None = None
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)


class PriorityItem(AEPBaseModel):
    order: int = Field(ge=0)
    text: str
    ghe_code_hint: str | None = None
    level: str | None = None
    source_section_id: str | None = None


class ActionPlanItem(AEPBaseModel):
    order: int = Field(ge=0)
    action: str
    ghe_code_hint: str | None = None
    responsible: str | None = None
    deadline: str | None = None
    indicator: str | None = None
    priority: str | None = None
    source_section_id: str | None = None
    evolution_records: str = ""

    @field_validator("evolution_records")
    @classmethod
    def keep_evolution_field_empty(cls, value: str) -> str:
        # The generated AEP intentionally leaves this editable field blank.
        return ""


class TechnicalAnalysis(AEPBaseModel):
    analysis_id: str
    order: int = Field(ge=0)
    ghe_code_hint: str | None = None
    ghe_name_hint: str | None = None
    official_ghe_code: str | None = None
    sections: list[TechnicalSection] = Field(default_factory=list)
    favorable_percentage: str | None = None
    classification: str | None = None
    technical_reading: list[str] = Field(default_factory=list)


class TechnicalReport(AEPBaseModel):
    mode: AnalysisMode
    sections: list[TechnicalSection] = Field(default_factory=list)
    analyses: list[TechnicalAnalysis] = Field(default_factory=list)
    priorities: list[PriorityItem] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)
    conclusion: list[str] = Field(default_factory=list)
    source_roles: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReconciliationCandidate(AEPBaseModel):
    official_ghe_code: str
    official_ghe_name: str
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class ReconciliationItem(AEPBaseModel):
    source_id: str
    source_code: str | None = None
    source_name: str | None = None
    candidates: list[ReconciliationCandidate] = Field(default_factory=list)
    status: ReconciliationStatus = ReconciliationStatus.NEEDS_REVIEW
    official_ghe_code: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> "ReconciliationItem":
        if self.status in {
            ReconciliationStatus.AUTO_MATCHED,
            ReconciliationStatus.CONFIRMED,
        } and not self.official_ghe_code:
            raise ValueError("matched reconciliation requires an official GHE")
        if (
            self.status == ReconciliationStatus.NOT_APPLICABLE
            and self.official_ghe_code is not None
        ):
            raise ValueError("not-applicable reconciliation cannot target a GHE")
        if self.status in {
            ReconciliationStatus.CONFIRMED,
            ReconciliationStatus.NOT_APPLICABLE,
        } and not self.decision_reason:
            raise ValueError("explicit decisions require a reason")
        return self


class ReconciliationPlan(AEPBaseModel):
    items: list[ReconciliationItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def complete(self) -> bool:
        return all(
            item.status
            in {
                ReconciliationStatus.AUTO_MATCHED,
                ReconciliationStatus.CONFIRMED,
                ReconciliationStatus.NOT_APPLICABLE,
            }
            for item in self.items
        )


class ValidationIssue(AEPBaseModel):
    severity: Severity
    code: str
    message: str
    field: str | None = None
    source_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(AEPBaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def valid(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    @computed_field
    @property
    def errors(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity == Severity.ERROR
        ]

    @computed_field
    @property
    def warnings(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity == Severity.WARNING
        ]

    def add(
        self,
        severity: Severity,
        code: str,
        message: str,
        *,
        field: str | None = None,
        source_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                severity=severity,
                code=code,
                message=message,
                field=field,
                source_id=source_id,
                details=details or {},
            )
        )


class NormalizedAEP(AEPBaseModel):
    company: CompanyData
    document: DocumentData
    official_ghes: list[GHE]
    ergo: ErgoReport
    psychosocial: PsychosocialReport
    technical: TechnicalReport
    reconciliation: ReconciliationPlan
    validation: ValidationReport = Field(default_factory=ValidationReport)

    @computed_field
    @property
    def total_population(self) -> int:
        return sum(ghe.population for ghe in self.official_ghes)

    @model_validator(mode="after")
    def ensure_unique_official_codes(self) -> "NormalizedAEP":
        codes = [ghe.canonical_code for ghe in self.official_ghes]
        if len(codes) != len(set(codes)):
            raise ValueError("official GHE codes must be unique")
        return self
