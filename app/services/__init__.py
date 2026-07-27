"""Core normalization, validation and reconciliation services."""

from .normalization import (
    canonical_ghe_code,
    clean_text,
    extract_percentage,
    normalize_key,
    parse_ghe_reference,
    parse_population,
)
from .reconciliation import (
    ReconciliationDecision,
    apply_reconciliation_decisions,
    build_reconciliation_plan,
    select_ergo_blocks_for_generation,
)
from .validation import (
    UploadPolicy,
    detect_file_kind,
    validate_normalized_aep,
    validate_upload,
)

__all__ = [
    "ReconciliationDecision",
    "UploadPolicy",
    "apply_reconciliation_decisions",
    "build_reconciliation_plan",
    "canonical_ghe_code",
    "clean_text",
    "detect_file_kind",
    "extract_percentage",
    "normalize_key",
    "parse_ghe_reference",
    "parse_population",
    "select_ergo_blocks_for_generation",
    "validate_normalized_aep",
    "validate_upload",
]
