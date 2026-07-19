import argparse

from app.production import (EpisodeGenerationService, EpisodeProductionError,
                            EpisodeProductionPlanningError, ProductionRegistry, ProductionRegistryError)
from app.services import VideoPollingPolicy

from .episode_plan import add_planning_arguments, build_planner, load_director_plan, planning_configuration, print_plan
from .episode_produce import build_orchestrator


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan and produce an episode from semantic Story or Director input.")
    add_planning_arguments(parser)
    parser.add_argument("--interval", type=float, default=2); parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--max-attempts", type=int); parser.add_argument("--confirm", action="store_true"); args = parser.parse_args()
    try:
        if ProductionRegistry().exists(args.production_id):
            print("Production already exists. Use episode_resume to continue it."); return 1
        plan = load_director_plan(args.input); planner = build_planner(); configuration = planning_configuration(args)
        if not args.confirm:
            request = planner.plan(plan, **configuration); print_plan(request)
            print("Real provider generation may consume credits. Use --confirm to produce."); return 2
        policy = VideoPollingPolicy(interval_seconds=args.interval, timeout_seconds=args.timeout, max_attempts=args.max_attempts)
        result = EpisodeGenerationService(planner, build_orchestrator()).plan_and_produce(plan, policy, **configuration)
    except (EpisodeProductionPlanningError, EpisodeProductionError, ProductionRegistryError):
        print("Integrated episode generation failed at a safe production boundary."); return 1
    except Exception:
        print("Integrated episode generation failed due to invalid input or an unexpected local error."); return 1
    print(f"Production ID: {result.production_id}"); print(f"Status: {result.status.value}"); print(f"Scenes: {len(result.scenes)}")
    if result.final_artifact: print(f"Final path: {result.final_artifact.local_path}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
