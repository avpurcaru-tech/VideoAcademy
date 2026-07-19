import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.engines.director import DirectorEngine
from app.composition.paths import validate_local_path
from app.models import DirectorPlan, Episode
from app.production import (EpisodeProductionContractError, EpisodeProductionDuplicateSceneOrderError,
                            EpisodeProductionNonContiguousSceneOrderError, EpisodeProductionPlanner,
                            EpisodeProductionPlanningError, EpisodeProductionPromptBuilderError,
                            EpisodeProductionReferenceError, EpisodeProductionRequestConflictError,
                            EpisodeProductionRequestStoreCorruptedError, EpisodeProductionVideoRequestError,
                            EpisodeProductionSemanticVideoRequestError,
                            EpisodeTransitionPolicy, GenerationRequestReference, GenerationRequestStore)
from app.prompts import PromptBuilder
from app.prompts.adapters.kling import KlingPromptAdapter


def build_planner() -> EpisodeProductionPlanner:
    # The existing adapter maps only to the shared semantic VideoRequest contract.
    return EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()), GenerationRequestStore())


@dataclass(frozen=True)
class LoadedSemanticInput:
    director_plan: DirectorPlan
    input_type: str


class SafePlanningDiagnostic(RuntimeError):
    def __init__(self, lines): self.lines=tuple(lines)
    def print_safe(self):
        for line in self.lines: print(line)


def load_semantic_input(path: Path) -> LoadedSemanticInput:
    if not path.exists(): raise SafePlanningDiagnostic((f"Input file not found: {path}",))
    if not path.is_file(): raise SafePlanningDiagnostic((f"Input path is not a regular file: {path}",))
    try:
        text=path.read_text(encoding="utf-8")
    except OSError:
        raise SafePlanningDiagnostic((f"Input file is unreadable: {path}",)) from None
    try: payload=json.loads(text)
    except json.JSONDecodeError: raise SafePlanningDiagnostic((f"Input JSON is malformed: {path}",)) from None
    if not isinstance(payload,dict): raise SafePlanningDiagnostic(("Unsupported semantic input type.",))
    if "episode_id" in payload:
        try: return LoadedSemanticInput(DirectorPlan.model_validate(payload),"DirectorPlan")
        except ValidationError as error: raise _validation_diagnostic("DirectorPlan validation failed:",error) from None
    if "id" in payload:
        try: episode=Episode.model_validate(payload)
        except ValidationError as error: raise _validation_diagnostic("Episode validation failed:",error) from None
        try: return LoadedSemanticInput(DirectorEngine().create_plan(episode),"Episode")
        except Exception: raise SafePlanningDiagnostic(("Episode could not be directed safely.",)) from None
    raise SafePlanningDiagnostic(("Unsupported semantic input type.",))


def load_director_plan(path: Path) -> DirectorPlan:
    return load_semantic_input(path).director_plan


def add_planning_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--production-id", required=True)
    parser.add_argument("--provider", default="kling")
    parser.add_argument("--scene-output-dir", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--transition", choices=("cut", "fade", "dissolve"), default="cut")
    parser.add_argument("--transition-duration", type=float)


def planning_configuration(args) -> dict:
    return dict(production_id=args.production_id, scene_output_directory=args.scene_output_dir,
                workspace=args.workspace, destination=args.output, provider=args.provider,
                transition=EpisodeTransitionPolicy(kind=args.transition, duration_seconds=args.transition_duration))


def print_plan(request, *, input_type=None, preflight=False, emit=None) -> None:
    emit = emit or print
    if preflight: emit("Planning preflight passed.")
    if input_type: emit(f"Semantic input: {input_type}")
    emit(f"Production ID: {request.production_id}")
    emit(f"Scenes: {len(request.generation_request_references)}")
    emit("References:")
    for index, reference in enumerate(request.generation_request_references, start=1):
        emit(f"- scene-{index:04d}: {reference.reference_id}")
    emit(f"Destination: {request.final_output_path}")
    emit(f"Workspace: {request.media_workspace}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan durable episode generation from a Story or DirectorPlan.")
    add_planning_arguments(parser); parser.add_argument("--preflight", action="store_true"); args = parser.parse_args()
    try:
        loaded=load_semantic_input(args.input)
        _validate_cli_configuration(args)
        planner=build_planner()
        request=planner.preflight(loaded.director_plan,**planning_configuration(args))
        if not args.preflight: planner.persist(request)
    except SafePlanningDiagnostic as error:
        error.print_safe(); return 1
    except EpisodeProductionDuplicateSceneOrderError:
        print("Director scene numbering is duplicated."); return 1
    except EpisodeProductionNonContiguousSceneOrderError:
        print("Director scene numbering is non-contiguous."); return 1
    except EpisodeProductionPromptBuilderError:
        print("PromptBuilder failed while constructing provider-neutral video requests."); return 1
    except EpisodeProductionSemanticVideoRequestError as error:
        _validation_diagnostic("VideoRequest construction failed:",error.validation_error).print_safe(); return 1
    except EpisodeProductionVideoRequestError as error:
        _validation_diagnostic("VideoGenerationRequest validation failed:",error.validation_error).print_safe(); return 1
    except EpisodeProductionReferenceError:
        print("Deterministic generation request reference is invalid."); return 1
    except EpisodeProductionRequestConflictError:
        print("GenerationRequestStore conflict detected."); return 1
    except EpisodeProductionRequestStoreCorruptedError:
        print("GenerationRequestStore contains a corrupted request record."); return 1
    except EpisodeProductionContractError as error:
        _validation_diagnostic("EpisodeProductionRequest validation failed:",error.validation_error).print_safe(); return 1
    except EpisodeProductionPlanningError:
        print("Episode planning failed at a safe semantic boundary."); return 1
    except Exception:
        print("Episode planning failed due to an unexpected local error."); return 1
    print_plan(request,input_type=loaded.input_type,preflight=args.preflight); return 0


def _validate_cli_configuration(args) -> None:
    try: GenerationRequestReference(reference_id=args.production_id)
    except ValidationError as error: raise _validation_diagnostic("Production ID is invalid:",error) from None
    for label,value in (("scene output directory",args.scene_output_dir),("workspace",args.workspace),("output path",args.output)):
        try: validate_local_path(value,label)
        except ValueError: raise SafePlanningDiagnostic((f"Invalid {label}: {value}",)) from None
    try: EpisodeTransitionPolicy(kind=args.transition,duration_seconds=args.transition_duration)
    except ValidationError as error: raise _validation_diagnostic("Transition policy is invalid:",error) from None


def _validation_diagnostic(title,error):
    lines=[title]
    for detail in error.errors(include_url=False,include_context=False,include_input=False):
        location=".".join(str(part) for part in detail["loc"]) or "root"
        lines.append(f"- {location}: {str(detail['type']).replace('_',' ')}")
    return SafePlanningDiagnostic(lines)


if __name__ == "__main__": raise SystemExit(main())
