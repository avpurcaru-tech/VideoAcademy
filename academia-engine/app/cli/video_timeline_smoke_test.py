import argparse
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

from app.media import (
    FFprobeAdapter,
    MediaToolAvailabilityChecker,
    MediaToolAvailabilityError,
    SubprocessProcessRunner,
)
from app.timeline import (
    FFmpegTimelineAudioCompatibilityError,
    FFmpegTimelineRenderer,
    TimelineMediaValidationError,
    TimelineMediaValidator,
    TimelineOutput,
    TimelineRenderCompilationError,
    TimelineRendererError,
    TimelineScene,
    TimelineTransition,
    TimelineTransitionKind,
    VideoTimeline,
    build_render_plan,
)


_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
SMOKE_TIMELINE_ID = "timeline-smoke-test"


class TimelineSmokeTestError(RuntimeError):
    """Base safe error for smoke-harness preflight failures."""


class TimelineSmokeInputError(TimelineSmokeTestError):
    """Raised when a supplied local input cannot be used."""


class TimelineSmokeHashError(TimelineSmokeTestError):
    """Raised when positional SHA-256 integrity verification fails."""


class TimelineSmokeTransitionError(TimelineSmokeTestError):
    """Raised when smoke transition arguments are inconsistent."""


@dataclass(frozen=True)
class TimelineSmokeRuntime:
    tool_checker: MediaToolAvailabilityChecker
    validator: TimelineMediaValidator
    renderer: FFmpegTimelineRenderer


def build_runtime() -> TimelineSmokeRuntime:
    """Wire only production runner, probe, validator, and renderer components."""
    runner = SubprocessProcessRunner()
    probe = FFprobeAdapter(runner)
    return TimelineSmokeRuntime(
        tool_checker=MediaToolAvailabilityChecker(runner),
        validator=TimelineMediaValidator(probe),
        renderer=FFmpegTimelineRenderer(runner, probe),
    )


def build_smoke_timeline(
    inputs: list[Path],
    workspace: Path,
    output: Path,
    transition_kind: str = "cut",
    transition_duration: float | None = None,
) -> VideoTimeline:
    if len(inputs) < 2:
        raise TimelineSmokeInputError("Timeline smoke test requires at least two input files.")
    try:
        kind = TimelineTransitionKind(transition_kind)
    except ValueError as error:
        raise TimelineSmokeTransitionError("Unsupported smoke-test transition kind.") from error
    if kind == TimelineTransitionKind.CUT:
        if transition_duration not in (None, 0):
            raise TimelineSmokeTransitionError("Cut transition duration must be zero or omitted.")
        transition = TimelineTransition(kind=kind, duration_seconds=0)
    else:
        if (
            transition_duration is None
            or not math.isfinite(transition_duration)
            or transition_duration <= 0
        ):
            raise TimelineSmokeTransitionError(
                "Fade and dissolve smoke tests require a positive transition duration."
            )
        transition = TimelineTransition(kind=kind, duration_seconds=transition_duration)
    scenes = tuple(
        TimelineScene(
            scene_id=f"scene-{index + 1:04d}",
            source_path=path,
            order=index,
            trim_start_seconds=None,
            trim_end_seconds=None,
            transition_to_next=transition if index < len(inputs) - 1 else None,
        )
        for index, path in enumerate(inputs)
    )
    return VideoTimeline(
        timeline_id=SMOKE_TIMELINE_ID,
        scenes=scenes,
        output=TimelineOutput(destination=output, workspace=workspace),
    )


def validate_input_files(inputs: list[Path]) -> None:
    for position, path in enumerate(inputs, start=1):
        if not path.exists():
            raise TimelineSmokeInputError(f"Smoke input {position} is missing: {path}")
        if not path.is_file():
            raise TimelineSmokeInputError(
                f"Smoke input {position} is not a regular file: {path}"
            )


def verify_input_hashes(inputs: list[Path], expected_hashes: list[str] | None) -> None:
    if not expected_hashes:
        return
    if len(expected_hashes) != len(inputs):
        raise TimelineSmokeHashError(
            "The number of --input-sha256 values must equal the number of --input values."
        )
    for position, (path, expected) in enumerate(zip(inputs, expected_hashes), start=1):
        if not _SHA256.fullmatch(expected):
            raise TimelineSmokeHashError(f"Smoke input {position} expected SHA-256 is invalid.")
        actual = _sha256(path)
        if actual.lower() != expected.lower():
            raise TimelineSmokeHashError(
                f"Smoke input {position} SHA-256 does not match: {path}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a manual real-media timeline smoke test.")
    parser.add_argument("--input", required=True, action="append", type=Path)
    parser.add_argument("--input-sha256", action="append")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--transition",
        choices=[kind.value for kind in TimelineTransitionKind],
        default="cut",
    )
    parser.add_argument("--transition-duration", type=float)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    try:
        validate_input_files(args.input)
        verify_input_hashes(args.input, args.input_sha256)
        timeline = build_smoke_timeline(
            args.input,
            args.workspace,
            args.output,
            args.transition,
            args.transition_duration,
        )
        runtime = build_runtime()
        runtime.tool_checker.require_available()
        validated = runtime.validator.validate(timeline)
        plan = build_render_plan(validated)
        _print_preflight(validated, plan.expected_duration_seconds, args.transition)
        artifact = runtime.renderer.render(plan, overwrite=args.overwrite)
    except MediaToolAvailabilityError as error:
        print(f"Timeline smoke test unavailable: {error}")
        return 1
    except TimelineSmokeTestError as error:
        print(f"Timeline smoke-test preflight failed: {error}")
        return 1
    except TimelineMediaValidationError as error:
        print(f"Timeline smoke-test media validation failed: {str(error)[:500]}")
        return 1
    except TimelineRenderCompilationError as error:
        if isinstance(error.__cause__, FFmpegTimelineAudioCompatibilityError):
            print("Timeline smoke test failed: mixed audio presence was detected.")
        else:
            print(f"Timeline smoke-test compilation failed: {str(error)[:500]}")
        return 1
    except TimelineRendererError as error:
        print(f"Timeline smoke-test render failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Timeline smoke test failed due to an invalid timeline or unexpected local error.")
        return 1

    media = artifact.media_info
    print(f"Timeline ID: {artifact.timeline_id}")
    print(f"Saved path: {artifact.local_path}")
    print(f"Sources: {artifact.source_count}")
    print(f"Transitions: {artifact.transition_count}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    print(f"Video codec: {media.video_codec}")
    print(f"Audio codec: {media.audio_codec or ''}")
    print(f"Has audio: {str(media.has_audio).lower()}")
    return 0


def _print_preflight(validated, expected_duration: float, transition: str) -> None:
    print(f"Inputs: {validated.source_count}")
    print(f"Transition: {transition}")
    print(f"Expected timeline duration: {expected_duration}")
    print(f"Output: {validated.destination}")
    for scene in validated.scenes:
        media = scene.source_media_info
        print(f"Scene: {scene.scene_id}")
        print(f"Path: {scene.source_path}")
        print(f"Duration: {media.duration_seconds}")
        print(f"Resolution: {media.width}x{media.height}")
        print(f"Frame rate: {media.frame_rate}")
        print(f"Has audio: {str(media.has_audio).lower()}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
