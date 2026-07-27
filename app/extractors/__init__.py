"""Source-document extractors.

Extractors receive backend-resolved local paths. They never accept or resolve
client-provided paths directly.
"""


class ExtractionError(ValueError):
    """A source could not be safely or consistently extracted."""


class UnsupportedSourceError(ExtractionError):
    """The real source type is not supported."""


class ConversionRequiredError(ExtractionError):
    """A legacy binary document must be converted before extraction."""


from .ergo_extractor import ErgoExtractor, extract_ergo
from .ghe_extractor import GHEExtractor, extract_ghes
from .psico_extractor import (
    PsychosocialExtractor,
    associate_psychosocial_images,
    extract_psychosocial,
)
from .technical_report_extractor import (
    TechnicalReportExtractor,
    extract_technical_report,
)

__all__ = [
    "ConversionRequiredError",
    "ErgoExtractor",
    "ExtractionError",
    "GHEExtractor",
    "PsychosocialExtractor",
    "TechnicalReportExtractor",
    "UnsupportedSourceError",
    "associate_psychosocial_images",
    "extract_ergo",
    "extract_ghes",
    "extract_psychosocial",
    "extract_technical_report",
]
