import argparse
from datetime import datetime,timezone
from app.project import ProjectRegistry
from app.project import ProjectStatus
from app.production import IdentityReviewService,IdentityReviewError

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--project-id",required=True); parser.add_argument("--scene-id",required=True); args=parser.parse_args()
    projects=ProjectRegistry(); project=projects.load(args.project_id)
    try: production=IdentityReviewService().decide(project.video_production_id,args.scene_id,True)
    except IdentityReviewError as error:
        print("Identity review approval failed."); print(f"Scene: {error.scene_id or args.scene_id}")
        print(f"Current status: {error.current_status or 'unavailable'}"); print("Expected status: awaiting_identity_review"); return 1
    projects.update(project.model_copy(update={"status":ProjectStatus.VIDEO_GENERATING,"failure_stage":None,
        "failure_category":None,"safe_message":None,"failed_scene_id":None,"updated_at":datetime.now(timezone.utc)}))
    canonical=next(s.scene_id for s in production.scenes if s.identity_review_status=="approved" and s.identity_validated)
    print(f"Approved scene: {canonical}"); return 0
if __name__ == "__main__": raise SystemExit(main())
