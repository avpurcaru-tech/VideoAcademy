import argparse

from app.config.environment import load_application_environment
from app.project import ProjectRegistry
from app.project.video_preflight import ProjectVideoPreflightError,ProjectVideoPreflightService


def main():
    load_application_environment()
    parser=argparse.ArgumentParser(description="Read-only video readiness check for an existing project.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try:
        project,production,scenes=ProjectVideoPreflightService(ProjectRegistry()).inspect(args.project_id)
    except ProjectVideoPreflightError as error:
        print("Video preflight failed.")
        print(f"Category: {error.category}")
        print(f"Failed scene: {error.scene_id or 'unavailable'}")
        print(f"Safe message: {error}")
        if hasattr(error,"field_diagnostics"):
            print("Kling configuration diagnostics:")
            for field,category in error.field_diagnostics: print(f"- {field}: {category}")
            for diagnostic in getattr(error,"generation_diagnostics",()): print(f"- {diagnostic}")
        return 1
    except Exception:
        print("Video preflight failed due to invalid durable local state."); return 1
    print("Video preflight passed.")
    print(f"Project ID: {project.project_id}")
    print(f"Production ID: {production.production_id}")
    for scene_id in scenes:
        print(f"Scene: {scene_id}"); print("Readiness: ready")
    print("HTTP calls: 0")
    return 0


if __name__=="__main__": raise SystemExit(main())
