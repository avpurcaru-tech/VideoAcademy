from .contracts import (
    CompositionExecutionResult,
    ResolvedVideoComposition,
    VideoCompositionManifest,
    VideoCompositionOutput,
    VideoCompositionScene,
)
from .executor import (
    CompositionAssemblyError,
    CompositionDestinationConflictError,
    CompositionExecutionError,
    CompositionExecutionService,
    CompositionManifestResolutionError,
    CompositionSourceValidationError,
)
from .resolver import resolve_manifest, to_assembly_request
from app.sync_planning import *

_TIMELINE_EXPORTS = {"ExistingTimelineVideoRenderer", "MusicTimelineComposer",
    "MusicTimelineCompositionConflictError", "MusicTimelineCompositionError",
    "MusicTimelineCompositionRequest", "MusicTimelineCompositionResult",
    "MusicTimelineClipMismatchError", "StoryboardVideoClip"}


def __getattr__(name):
    # Avoid importing app.timeline again while app.timeline.contracts imports the
    # path-safety helpers from this package.
    if name in _TIMELINE_EXPORTS:
        from . import music_timeline
        return getattr(music_timeline, name)
    raise AttributeError(name)

__all__ = [
    "ResolvedVideoComposition",
    "CompositionExecutionResult",
    "CompositionAssemblyError",
    "CompositionDestinationConflictError",
    "CompositionExecutionError",
    "CompositionExecutionService",
    "CompositionManifestResolutionError",
    "CompositionSourceValidationError",
    "VideoCompositionManifest",
    "VideoCompositionOutput",
    "VideoCompositionScene",
    "resolve_manifest",
    "to_assembly_request",
    "ExistingTimelineVideoRenderer",
    "MusicTimelineComposer",
    "MusicTimelineCompositionConflictError",
    "MusicTimelineCompositionError",
    "MusicTimelineCompositionRequest",
    "MusicTimelineCompositionResult",
    "MusicTimelineClipMismatchError",
    "StoryboardVideoClip",
]
