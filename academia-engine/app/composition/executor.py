from pathlib import Path

from app.media import (
    AudioLoudnessProfile,
    VideoAssemblyService,
    VideoNormalizationProfile,
)

from .contracts import CompositionExecutionResult, VideoCompositionManifest
from .resolver import resolve_manifest, to_assembly_request


class CompositionExecutionError(RuntimeError):
    """Base safe error for composition execution orchestration."""


class CompositionManifestResolutionError(CompositionExecutionError):
    """Raised when a manifest cannot be deterministically resolved."""


class CompositionSourceValidationError(CompositionExecutionError):
    """Raised when a resolved local source is not a regular file."""


class CompositionDestinationConflictError(CompositionExecutionError):
    """Raised before assembly when final output already exists."""


class CompositionAssemblyError(CompositionExecutionError):
    """Raised when the lower-level media assembly stage fails."""


class CompositionExecutionService:
    """Resolve, preflight, bridge, and execute one provider-neutral composition."""

    def __init__(self, assembly_service: VideoAssemblyService) -> None:
        self._assembly_service = assembly_service

    def execute(
        self,
        manifest: VideoCompositionManifest,
        normalization_profile: VideoNormalizationProfile | None = None,
        loudness_profile: AudioLoudnessProfile | None = None,
        overwrite: bool = False,
    ) -> CompositionExecutionResult:
        try:
            resolved = resolve_manifest(manifest)
        except Exception as error:
            raise CompositionManifestResolutionError(
                f"Composition manifest could not be resolved: {manifest.composition_id}"
            ) from error

        self._validate_sources(resolved.composition_id, resolved.ordered_sources)
        if resolved.destination.exists() and not overwrite:
            raise CompositionDestinationConflictError(
                f"Composition destination already exists: {resolved.destination}"
            )

        assembly_request = to_assembly_request(
            resolved,
            normalization_profile or VideoNormalizationProfile.academia_default(),
            loudness_profile or AudioLoudnessProfile.academia_default(),
            overwrite=overwrite,
        )
        try:
            assembled = self._assembly_service.assemble(assembly_request)
        except Exception as error:
            raise CompositionAssemblyError(
                f"Composition assembly failed: {resolved.composition_id}"
            ) from error
        return CompositionExecutionResult(
            composition_id=resolved.composition_id,
            local_path=assembled.local_path,
            byte_size=assembled.byte_size,
            sha256=assembled.sha256,
            media_info=assembled.media_info,
            source_count=assembled.source_count,
        )

    @staticmethod
    def _validate_sources(composition_id: str, sources: tuple[Path, ...]) -> None:
        for position, source in enumerate(sources, start=1):
            if not source.is_file():
                raise CompositionSourceValidationError(
                    f"Composition {composition_id} source {position} is not a regular file: {source}"
                )
