import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from app.engines.director import DirectorEngine
from app.models import Episode
from app.production import (EpisodeProjectDirectionError, EpisodeProjectPlanner,
                            EpisodeProductionPlanningError, GenerationRequestStore)
from app.prompts import PromptBuilder
from app.prompts.adapters import KlingPromptAdapter

from .episode_plan import (SafePlanningDiagnostic, _validate_cli_configuration, _validation_diagnostic,
                           add_planning_arguments, planning_configuration, print_plan)


def build_project_planner() -> EpisodeProjectPlanner:
    from app.production import EpisodeProductionPlanner
    production_planner=EpisodeProductionPlanner(PromptBuilder(KlingPromptAdapter()),GenerationRequestStore())
    return EpisodeProjectPlanner(DirectorEngine(),production_planner)


def load_episode(path: Path) -> Episode:
    if not path.exists(): raise SafePlanningDiagnostic((f"Input file not found: {path}",))
    if not path.is_file(): raise SafePlanningDiagnostic((f"Input path is not a regular file: {path}",))
    try: text=path.read_text(encoding="utf-8")
    except OSError: raise SafePlanningDiagnostic((f"Input file is unreadable: {path}",)) from None
    try: payload=json.loads(text)
    except json.JSONDecodeError: raise SafePlanningDiagnostic((f"Input JSON is malformed: {path}",)) from None
    try: return Episode.model_validate(payload)
    except ValidationError as error: raise _validation_diagnostic("Episode validation failed:",error) from None


def main() -> int:
    parser=argparse.ArgumentParser(description="Plan production directly from an existing Episode contract.")
    add_planning_arguments(parser); parser.add_argument("--preflight",action="store_true"); args=parser.parse_args()
    try:
        episode=load_episode(args.input); _validate_cli_configuration(args); planner=build_project_planner()
        request=planner.preflight(episode,**planning_configuration(args))
        if not args.preflight: planner.persist(request)
    except SafePlanningDiagnostic as error: error.print_safe(); return 1
    except EpisodeProjectDirectionError: print("DirectorEngine failed to create a valid DirectorPlan."); return 1
    except EpisodeProductionPlanningError: print("Episode project planning failed at a safe planning boundary."); return 1
    except Exception: print("Episode project planning failed due to an unexpected local error."); return 1
    print_plan(request,input_type="Episode",preflight=args.preflight); return 0


if __name__=="__main__": raise SystemExit(main())
