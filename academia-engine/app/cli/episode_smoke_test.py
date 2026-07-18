import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.composition.paths import validate_local_path
from app.models import VideoGenerationRequest
from app.production import (
    EpisodeProductionError,
    EpisodeProductionRequest,
    EpisodeTransitionPolicy,
    GenerationRequestReference,
    GenerationRequestConflictError,
    GenerationRequestCorruptedError,
    GenerationRequestNotFoundError,
    GenerationRequestStore,
    ProductionRegistry,
    ProductionRegistryError,
)
from app.services import VideoPollingPolicy

from .episode_produce import build_orchestrator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an explicitly confirmed real two-scene episode production."
    )
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--request", action="append", type=Path, dest="requests")
    parser.add_argument("--provider", default="kling")
    parser.add_argument("--scene-output-dir", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--transition", choices=("cut", "fade"), default="cut")
    parser.add_argument("--transition-duration", type=float)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    return parser


@dataclass(frozen=True)
class _PreflightResult:
    request: EpisodeProductionRequest
    store: GenerationRequestStore


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    policy = VideoPollingPolicy(
        interval_seconds=args.interval,
        timeout_seconds=args.timeout,
        max_attempts=args.max_attempts,
    )

    if args.resume:
        if args.preflight:
            parser.error("--resume cannot be combined with --preflight")
        if args.requests:
            parser.error("--resume does not accept --request files")
        try:
            result = build_orchestrator().resume(args.production_id, policy)
        except EpisodeProductionError:
            print("Episode smoke-test resume failed at the production boundary.")
            return 1
        except Exception:
            print("Episode smoke-test resume failed due to an unexpected local error.")
            return 1
        _print_result(result)
        return 0

    if not args.confirm and not args.preflight:
        print("Real provider generation may consume credits. Use --confirm to continue.")
        return 2
    if len(args.requests or ()) != 2:
        parser.error("new smoke production requires exactly two --request files")
    if args.scene_output_dir is None or args.workspace is None or args.output is None:
        parser.error("new smoke production requires --scene-output-dir, --workspace, and --output")

    try:
        preflight = _run_preflight(args)
    except _SafePreflightError as error:
        error.print_safe()
        return 1
    if args.preflight:
        print("Preflight passed.")
        print(f"Production ID: {args.production_id}")
        print("Scenes: 2")
        print("References:")
        for reference in preflight.request.generation_request_references:
            print(f"- {reference.reference_id}")
        return 0

    print("Real provider generation may consume credits.")
    print(f"Production ID: {args.production_id}")
    print("Scenes to submit: 2")
    print(f"Provider: {args.provider}")
    try:
        for reference, generation_request in zip(
            preflight.request.generation_request_references,
            preflight.request.video_requests,
            strict=True,
        ):
            preflight.store.create(reference, generation_request)
        result = build_orchestrator().produce(preflight.request, policy)
    except EpisodeProductionError:
        print("Episode smoke test failed at the production boundary.")
        return 1
    except (GenerationRequestConflictError, GenerationRequestCorruptedError):
        print("Episode smoke test failed because durable request state changed after preflight.")
        return 1
    except Exception:
        print("Episode smoke test failed due to an unexpected local error.")
        return 1

    _print_result(result)
    return 0


class _SafePreflightError(RuntimeError):
    def __init__(self, lines: tuple[str, ...]) -> None:
        self.lines = lines

    def print_safe(self) -> None:
        for line in self.lines:
            print(line)


def _run_preflight(args) -> _PreflightResult:
    try:
        GenerationRequestReference(reference_id=args.production_id)
    except ValidationError as error:
        raise _validation_failure("Production ID is invalid:", error, prefix="production_id") from None

    try:
        registry = ProductionRegistry()
        exists = registry.exists(args.production_id)
    except ProductionRegistryError:
        raise _SafePreflightError(("Production registry could not be inspected.",)) from None
    if exists:
        raise _SafePreflightError(("Production already exists:", f"- production_id: {args.production_id}", "Use --resume to continue it."))

    generation_requests = tuple(_load_generation_request(path) for path in args.requests)
    references = []
    for index in range(2):
        reference_id = f"{args.production_id}-scene-{index + 1:04d}"
        try:
            references.append(GenerationRequestReference(reference_id=reference_id))
        except ValidationError as error:
            raise _validation_failure("Request reference is invalid:", error, prefix="reference_id") from None

    for label, value in (("scene output directory", args.scene_output_dir), ("workspace", args.workspace), ("final output path", args.output)):
        try:
            validate_local_path(value, label)
        except ValueError:
            raise _SafePreflightError((f"Invalid {label}: {value}",)) from None

    try:
        transition = EpisodeTransitionPolicy(kind=args.transition, duration_seconds=args.transition_duration)
    except ValidationError as error:
        raise _validation_failure("Transition policy is invalid:", error, prefix="transition_policy") from None

    store = GenerationRequestStore()
    for reference, expected in zip(references, generation_requests, strict=True):
        try:
            existing = store.resolve(reference)
        except GenerationRequestNotFoundError:
            continue
        except GenerationRequestCorruptedError:
            raise _SafePreflightError(("Request-store record is corrupted:", f"- reference_id: {reference.reference_id}")) from None
        except Exception:
            raise _SafePreflightError(("Request store could not be inspected:", f"- reference_id: {reference.reference_id}")) from None
        if existing != expected:
            raise _SafePreflightError(("Request reference conflict:", f"- reference_id: {reference.reference_id}"))

    try:
        request = EpisodeProductionRequest(
            production_id=args.production_id,
            video_requests=generation_requests,
            generation_request_references=tuple(references),
            provider=args.provider,
            scene_output_directory=args.scene_output_dir,
            final_output_path=args.output,
            media_workspace=args.workspace,
            transition_policy=transition,
        )
    except ValidationError as error:
        raise _validation_failure("Episode production request validation failed:", error) from None
    return _PreflightResult(request=request, store=store)


def _load_generation_request(path: Path) -> VideoGenerationRequest:
    if not path.exists():
        raise _SafePreflightError((f"Request file not found: {path}",))
    if not path.is_file():
        raise _SafePreflightError((f"Request file is not a regular file: {path}",))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise _SafePreflightError((f"Request file is unreadable: {path}",)) from None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise _SafePreflightError((f"Request JSON is invalid: {path}",)) from None
    try:
        return VideoGenerationRequest.model_validate(payload)
    except ValidationError as error:
        raise _validation_failure(f"Generation request validation failed: {path}", error) from None


def _validation_failure(title: str, error: ValidationError, prefix: str | None = None) -> _SafePreflightError:
    lines = [title]
    for detail in error.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in detail["loc"]) or "root"
        if prefix and location == "reference_id":
            location = prefix
        category = str(detail["type"]).replace("_", " ")
        lines.append(f"- {location}: {category}")
    return _SafePreflightError(tuple(lines))


def _print_result(result, *, emit=None) -> None:
    emit = emit or print
    emit(f"Production ID: {result.production_id}")
    emit(f"Status: {result.status.value}")
    emit(f"Scenes: {len(result.scenes)}")
    for scene in result.scenes:
        emit(f"Scene: {scene.scene_id}")
        emit(f"Scene status: {scene.normalized_status.value if scene.normalized_status else ''}")
        emit(f"Provider task ID: {scene.provider_task_id or ''}")
        emit(f"Local artifact: {scene.local_path or ''}")
    if result.final_artifact is None:
        return
    artifact = result.final_artifact
    media = artifact.media_info
    emit(f"Final path: {artifact.local_path}")
    emit(f"Bytes: {artifact.byte_size}")
    emit(f"SHA-256: {artifact.sha256}")
    emit(f"Duration: {media.duration_seconds}")
    emit(f"Resolution: {media.width}x{media.height}")
    emit(f"Frame rate: {media.frame_rate}")
    emit(f"Video codec: {media.video_codec}")
    emit(f"Audio codec: {media.audio_codec or ''}")
    emit(f"Has audio: {str(media.has_audio).lower()}")


if __name__ == "__main__":
    raise SystemExit(main())
