import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from app.models import CharacterReferenceImage


@dataclass(frozen=True)
class VisualIdentityValidation:
    valid: bool
    safe_category: str | None = None
    validated_character_ids: tuple[str, ...] = ()
    confidence: float | None = None
    threshold: float | None = None
    safe_reasons: tuple[str, ...] = ()
    implementation: str = "unknown"
    version: str = "unknown"
    automatic: bool = False
    review_required: bool = False


class VisualIdentityValidationMode(str, Enum):
    REQUIRED = "required"
    ADVISORY = "advisory"
    DISABLED = "disabled"


class VisualIdentityValidator(Protocol):
    def validate(self, generated_video: Path,
                 references: tuple[CharacterReferenceImage, ...]) -> VisualIdentityValidation: ...


class ManualReviewVisualIdentityValidator:
    """Truthful fallback: it requests review and never claims an automatic pass."""
    implementation = "manual_review"
    version = "1"

    def validate(self, generated_video: Path,
                 references: tuple[CharacterReferenceImage, ...]) -> VisualIdentityValidation:
        ids = tuple(reference.character_id for reference in references)
        return VisualIdentityValidation(False, "visual_identity_review_required", ids,
            safe_reasons=("automatic_visual_identity_backend_unavailable",),
            implementation=self.implementation, version=self.version,
            automatic=False, review_required=True)


@dataclass(frozen=True)
class VisualIdentityValidatorRuntime:
    mode: VisualIdentityValidationMode
    validator: VisualIdentityValidator | None
    automatic_available: bool
    manual_review_available: bool


class VisualIdentityValidatorFactory:
    """The single construction point used by production and read-only preflight."""
    ENVIRONMENT_KEY = "VISUAL_IDENTITY_VALIDATION_MODE"

    def construct_runtime(self, mode: str | VisualIdentityValidationMode | None = None):
        configured = mode if mode is not None else os.getenv(self.ENVIRONMENT_KEY)
        selected = VisualIdentityValidationMode(configured or VisualIdentityValidationMode.REQUIRED)
        if selected == VisualIdentityValidationMode.DISABLED and configured is None:
            raise ValueError("Disabled visual identity validation must be explicitly configured.")
        if selected == VisualIdentityValidationMode.DISABLED:
            return VisualIdentityValidatorRuntime(selected, None, False, True)
        # No production CV backend is bundled. Manual review is deliberately explicit.
        return VisualIdentityValidatorRuntime(selected, ManualReviewVisualIdentityValidator(), False, True)


@dataclass(frozen=True)
class VisualConsistencyRetryPolicy:
    max_identity_retries: int = 1

    def can_retry(self, completed_attempts: int) -> bool:
        return completed_attempts < self.max_identity_retries
