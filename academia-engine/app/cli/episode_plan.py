import argparse
from pathlib import Path

from pydantic import ValidationError

from app.engines.director import DirectorEngine
from app.models import DirectorPlan, Episode
from app.production import (EpisodeProductionPlanner, EpisodeProductionPlanningError,
                            EpisodeTransitionPolicy, GenerationRequestStore)
from app.prompts import PromptBuilder
from app.prompts.adapters.kling import KlingPromptAdapter


def build_planner() -> EpisodeProductionPlanner:
    # The existing adapter maps only to the shared semantic VideoRequest contract.
    return EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()), GenerationRequestStore())


def load_director_plan(path: Path) -> DirectorPlan:
    text = path.read_text(encoding="utf-8")
    try:
        return DirectorPlan.model_validate_json(text)
    except ValidationError:
        episode = Episode.model_validate_json(text)
        return DirectorEngine().create_plan(episode)


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


def print_plan(request, *, emit=print) -> None:
    emit(f"Production ID: {request.production_id}")
    emit(f"Scenes: {len(request.generation_request_references)}")
    emit("References:")
    for index, reference in enumerate(request.generation_request_references, start=1):
        emit(f"- scene-{index:04d}: {reference.reference_id}")
    emit(f"Destination: {request.final_output_path}")
    emit(f"Workspace: {request.media_workspace}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan durable episode generation from a Story or DirectorPlan.")
    add_planning_arguments(parser); args = parser.parse_args()
    try:
        request = build_planner().plan(load_director_plan(args.input), **planning_configuration(args))
    except (OSError, ValidationError, EpisodeProductionPlanningError):
        print("Episode planning failed due to invalid semantic input or durable request state."); return 1
    except Exception:
        print("Episode planning failed due to an unexpected local error."); return 1
    print_plan(request); return 0


if __name__ == "__main__": raise SystemExit(main())
