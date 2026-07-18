from app.media import (
    AudioLoudnessProfile,
    VideoAssemblyRequest,
    VideoNormalizationProfile,
)

from .contracts import ResolvedVideoComposition, VideoCompositionManifest


def resolve_manifest(manifest: VideoCompositionManifest) -> ResolvedVideoComposition:
    """Resolve scene order without I/O, media inspection, mutation, or provider calls."""
    ordered = tuple(
        scene.source_path
        for scene in sorted(manifest.scenes, key=lambda scene: scene.order)
    )
    return ResolvedVideoComposition(
        composition_id=manifest.composition_id,
        ordered_sources=ordered,
        destination=manifest.output.destination,
        workspace=manifest.output.workspace,
        source_count=len(ordered),
    )


def to_assembly_request(
    resolved: ResolvedVideoComposition,
    normalization_profile: VideoNormalizationProfile,
    loudness_profile: AudioLoudnessProfile,
    overwrite: bool = False,
) -> VideoAssemblyRequest:
    """Bridge resolved durable paths into media assembly without executing it."""
    return VideoAssemblyRequest(
        sources=resolved.ordered_sources,
        destination=resolved.destination,
        workspace=resolved.workspace,
        normalization_profile=normalization_profile,
        loudness_profile=loudness_profile,
        overwrite=overwrite,
    )
