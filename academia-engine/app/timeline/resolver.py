from app.composition import VideoCompositionManifest

from .contracts import (
    ResolvedTimelineScene,
    ResolvedVideoTimeline,
    TimelineOutput,
    TimelineScene,
    TimelineTransition,
    TimelineTransitionKind,
    VideoTimeline,
)


def resolve_timeline(timeline: VideoTimeline) -> ResolvedVideoTimeline:
    """Resolve semantic order and implicit cuts without I/O or mutation."""
    ordered = sorted(timeline.scenes, key=lambda scene: scene.order)
    resolved_scenes = tuple(
        ResolvedTimelineScene(
            scene_id=scene.scene_id,
            source_path=scene.source_path,
            order=scene.order,
            trim_start_seconds=scene.trim_start_seconds,
            trim_end_seconds=scene.trim_end_seconds,
            transition_to_next=(
                scene.transition_to_next
                if scene.transition_to_next is not None
                else TimelineTransition(kind=TimelineTransitionKind.CUT, duration_seconds=0)
            )
            if index < len(ordered) - 1
            else None,
        )
        for index, scene in enumerate(ordered)
    )
    return ResolvedVideoTimeline(
        timeline_id=timeline.timeline_id,
        ordered_scenes=resolved_scenes,
        destination=timeline.output.destination,
        workspace=timeline.output.workspace,
        source_count=len(resolved_scenes),
    )


def timeline_from_composition_manifest(manifest: VideoCompositionManifest) -> VideoTimeline:
    """Map simple ordered composition semantics to an equivalent cut-only timeline."""
    last_order = max(scene.order for scene in manifest.scenes)
    scenes = tuple(
        TimelineScene(
            scene_id=scene.scene_id,
            source_path=scene.source_path,
            order=scene.order,
            trim_start_seconds=None,
            trim_end_seconds=None,
            transition_to_next=(
                None
                if scene.order == last_order
                else TimelineTransition(kind=TimelineTransitionKind.CUT, duration_seconds=0)
            ),
        )
        for scene in manifest.scenes
    )
    return VideoTimeline(
        timeline_id=manifest.composition_id,
        scenes=scenes,
        output=TimelineOutput(
            destination=manifest.output.destination,
            workspace=manifest.output.workspace,
        ),
    )
