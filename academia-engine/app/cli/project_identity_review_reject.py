import argparse
from datetime import datetime,timezone
from app.project import ProjectRegistry
from app.project import ProjectStatus,ProjectFailureStage
from app.production import EpisodeSceneStatus
from app.production import IdentityReviewService,IdentityReviewError

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--project-id",required=True); parser.add_argument("--scene-id",required=True); parser.add_argument("--reason",required=True); args=parser.parse_args()
    projects=ProjectRegistry(); project=projects.load(args.project_id)
    try: production=IdentityReviewService().decide(project.video_production_id,args.scene_id,False,args.reason)
    except IdentityReviewError as error:
        print("Identity review rejection failed."); print(f"Scene: {error.scene_id or args.scene_id}")
        print(f"Current status: {error.current_status or 'unavailable'}"); print("Expected status: awaiting_identity_review"); return 1
    exhausted=next(s for s in production.scenes if s.scene_id==args.scene_id).production_status==EpisodeSceneStatus.FAILED
    projects.update(project.model_copy(update={"status":ProjectStatus.FAILED if exhausted else ProjectStatus.VIDEO_GENERATING,
        "failure_stage":ProjectFailureStage.VISUAL_IDENTITY_VALIDATION if exhausted else None,
        "failure_category":"visual_identity_retry_exhausted" if exhausted else None,
        "safe_message":"Visual identity retry limit was reached." if exhausted else None,
        "failed_scene_id":args.scene_id if exhausted else None,"updated_at":datetime.now(timezone.utc)}))
    canonical=production.scenes[IdentityReviewService.resolve_scene_index(production,args.scene_id)].scene_id
    print(f"Rejected scene: {canonical}"); return 0
if __name__ == "__main__": raise SystemExit(main())
