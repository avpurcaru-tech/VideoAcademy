import argparse

from app.production import (EpisodeProductionError, EpisodeProductionPlanningError,
                            EpisodeProjectGenerationService, ProductionRegistry, ProductionRegistryError)
from app.services import VideoPollingPolicy

from .episode_plan import add_planning_arguments, planning_configuration, print_plan
from .episode_produce import build_orchestrator
from .episode_project_plan import build_project_planner, load_episode


def main() -> int:
    parser=argparse.ArgumentParser(description="Plan and produce directly from an existing Episode contract.")
    add_planning_arguments(parser); parser.add_argument("--interval",type=float,default=2); parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--max-attempts",type=int); parser.add_argument("--confirm",action="store_true"); args=parser.parse_args()
    try:
        if ProductionRegistry().exists(args.production_id):
            print("Production already exists. Use episode_resume to continue it."); return 1
        episode=load_episode(args.input); planner=build_project_planner(); configuration=planning_configuration(args)
        if not args.confirm:
            request=planner.preflight(episode,**configuration); print_plan(request,input_type="Episode",preflight=True)
            print("Real provider generation may consume credits. Use --confirm to produce."); return 2
        policy=VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
        request=planner.plan_episode(episode,**configuration)
        result=EpisodeProjectGenerationService(planner,build_orchestrator()).produce_planned(request,policy)
    except (EpisodeProductionPlanningError,EpisodeProductionError,ProductionRegistryError):
        print("Episode project generation failed at a safe production boundary."); return 1
    except Exception: print("Episode project generation failed due to invalid input or an unexpected local error."); return 1
    print(f"Production ID: {result.production_id}"); print(f"Status: {result.status.value}"); print(f"Scenes: {len(result.scenes)}")
    if result.final_artifact: print(f"Final path: {result.final_artifact.local_path}")
    return 0


if __name__=="__main__": raise SystemExit(main())
