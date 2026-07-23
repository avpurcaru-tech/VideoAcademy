from dataclasses import dataclass
from pathlib import Path

from app.media import DURATION_TOLERANCE_SECONDS
from app.music_timeline import MusicTimeline
from app.production import EpisodeProductionStatus, EpisodeSceneStatus, ProductionIntegrityService, ProductionRegistry
from app.project.registry import ProjectRegistry


@dataclass(frozen=True)
class CompositionVariantPreflight:
    variant_id: str
    master_path: Path
    master_present: bool
    master_duration: float | None
    audio_path: Path
    audio_present: bool
    audio_duration: float | None
    timeline_path: Path
    timeline_present: bool
    timeline_duration: float | None
    mapping_valid: bool
    duration_valid: bool
    expected_output_path: Path
    failure_category: str | None

    @property
    def valid(self) -> bool:
        return self.failure_category is None


@dataclass(frozen=True)
class CompositionPreflightReport:
    project_id: str
    variants: tuple[CompositionVariantPreflight, ...]

    @property
    def valid(self) -> bool:
        return all(variant.valid for variant in self.variants)


class CompositionPreflightService:
    """Read-only composition validation. The supplied probe may only inspect media."""

    def __init__(self, projects=None, productions=None, probe=None):
        self._projects = projects or ProjectRegistry()
        self._productions = productions or ProductionRegistry()
        self._probe = probe

    def inspect(self, project_id: str) -> CompositionPreflightReport:
        project = self._projects.load(project_id)
        production = self._productions.load(project.video_production_id)
        integrity = ProductionIntegrityService().verify_production(production)
        master_path = production.final_artifact.local_path if production.final_artifact else project.video_directory / "master.mp4"
        master_present = master_path.is_file()
        master_valid = (
            production.status == EpisodeProductionStatus.SUCCEEDED
            and all(scene.production_status == EpisodeSceneStatus.READY for scene in production.scenes)
            and integrity.final_artifact.valid
            and master_present
        )
        master_info = self._probe.probe_video(master_path) if master_valid else None
        expected_sections = tuple(scene.source_scene_id for scene in production.scenes)
        variants = tuple(
            self._inspect_variant(project, variant_id, master_path, master_valid, master_info, expected_sections)
            for variant_id in ("variant-01", "variant-02")
        )
        return CompositionPreflightReport(project_id=project_id, variants=variants)

    def _inspect_variant(self, project, variant_id, master_path, master_valid, master_info, expected_sections):
        audio_path = project.music_directory / f"{variant_id}.mp3"
        timeline_path = project.music_directory / f"timeline-{variant_id}.json"
        output_path = project.final_directory / f"final-{variant_id}.mp4"
        audio_present = audio_path.is_file()
        timeline_present = timeline_path.is_file()
        audio_info = timeline = None
        failure = None

        if not master_present_or_valid(master_path, master_valid):
            failure = "composition_master_video_missing" if not master_path.is_file() else "composition_master_video_invalid"
        elif not audio_present:
            failure = "composition_audio_variant_missing"
        else:
            try:
                audio_info = self._probe.probe_audio(audio_path)
            except Exception:
                failure = "composition_audio_invalid"

        if not timeline_present:
            failure = failure or "composition_timeline_missing"
        else:
            try:
                timeline = MusicTimeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
            except Exception:
                failure = failure or "composition_timeline_invalid"

        mapping_valid = bool(
            timeline
            and timeline.timeline_id.endswith(variant_id)
            and tuple(segment.storyboard_section_id for segment in timeline.segments) == expected_sections
        )
        if timeline and not mapping_valid:
            failure = failure or "composition_variant_mapping_failed"
        duration_valid = bool(
            timeline
            and audio_info
            and abs(audio_info.duration_seconds - timeline.music_duration_seconds) <= DURATION_TOLERANCE_SECONDS
        )
        if timeline and audio_info and not duration_valid:
            failure = failure or "composition_duration_mismatch"

        return CompositionVariantPreflight(
            variant_id=variant_id,
            master_path=master_path,
            master_present=master_path.is_file(),
            master_duration=getattr(master_info, "duration_seconds", None),
            audio_path=audio_path,
            audio_present=audio_present,
            audio_duration=getattr(audio_info, "duration_seconds", None),
            timeline_path=timeline_path,
            timeline_present=timeline_present,
            timeline_duration=getattr(timeline, "music_duration_seconds", None),
            mapping_valid=mapping_valid,
            duration_valid=duration_valid,
            expected_output_path=output_path,
            failure_category=failure,
        )


def master_present_or_valid(master_path: Path, master_valid: bool) -> bool:
    return master_path.is_file() and master_valid
