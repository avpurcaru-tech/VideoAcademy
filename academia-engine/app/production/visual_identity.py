from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models import CharacterReferenceImage


@dataclass(frozen=True)
class VisualIdentityValidation:
    valid: bool
    safe_category: str | None = None


class VisualIdentityValidator(Protocol):
    def validate(self, generated_video: Path,
                 references: tuple[CharacterReferenceImage, ...]) -> VisualIdentityValidation: ...


@dataclass(frozen=True)
class VisualConsistencyRetryPolicy:
    max_identity_retries: int = 1

    def can_retry(self, completed_attempts: int) -> bool:
        return completed_attempts < self.max_identity_retries
