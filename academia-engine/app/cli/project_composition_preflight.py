import argparse

from app.production import EpisodeProductionStatus,EpisodeSceneStatus,ProductionIntegrityService,ProductionRegistry
from app.project import ProjectRegistry


def inspect(project_id,projects=None,productions=None):
    project=(projects or ProjectRegistry()).load(project_id)
    production=(productions or ProductionRegistry()).load(project.video_production_id)
    report=ProductionIntegrityService().verify_production(production)
    ready=sum(1 for scene,item in zip(production.scenes,report.scenes,strict=True)
        if scene.production_status==EpisodeSceneStatus.READY and item.artifact.valid)
    master_present=production.final_artifact is not None and production.final_artifact.local_path.is_file()
    master_valid=report.final_artifact.valid
    names=("failed_scene_id","failure_stage","failure_category","safe_message","submit_http_status","submit_provider_code",
        "submit_provider_message","submit_request_id","submit_provider_task_id","submit_response_shape","query_http_status",
        "query_provider_code","query_provider_task_id","query_response_shape")
    stale=production.status==EpisodeProductionStatus.SUCCEEDED and any(getattr(production,name) not in (None,()) for name in names)
    return production,ready,master_present,master_valid,stale


def main():
    parser=argparse.ArgumentParser(description="Inspect composition readiness without provider or FFmpeg calls.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try: production,ready,present,valid,stale=inspect(args.project_id)
    except Exception:
        print("Composition preflight failed at a safe boundary."); print("Provider calls: 0"); print("FFmpeg calls: 0"); return 1
    print(f"Video production status: {production.status.value}"); print(f"Ready scenes: {ready}/{len(production.scenes)}")
    print("Master video present: " + ("yes" if present else "no")); print("Master video valid: " + ("yes" if valid else "no"))
    print("Stale video diagnostics detected: " + ("yes" if stale else "no"))
    print("Resume reconciliation required: " + ("yes" if stale else "no"))
    print("Provider calls: 0"); print("FFmpeg calls: 0"); return 0 if valid and ready==len(production.scenes) else 1


if __name__=="__main__": raise SystemExit(main())
