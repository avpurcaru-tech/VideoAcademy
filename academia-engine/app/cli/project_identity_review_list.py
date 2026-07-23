import argparse
from app.project import ProjectRegistry
from app.production import IdentityReviewService

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--project-id",required=True); args=parser.parse_args()
    project=ProjectRegistry().load(args.project_id)
    print(f"Project ID: {project.project_id}")
    for scene in IdentityReviewService().pending(project.video_production_id):
        print(f"Canonical scene ID: {scene.scene_id}")
        print(f"Source storyboard section: {scene.source_scene_id or 'unavailable'}")
        print(f"Shot ID: {scene.scene_id if scene.scene_id.startswith('shot-') else 'unavailable'}")
        print(f"Production status: {scene.production_status.value}")
        print(f"Identity validation status: {scene.identity_validation_status}")
        print(f"Local artifact path: {scene.local_path}")
        print(f"Approve: python -m app.cli.project_identity_review_approve --project-id {project.project_id} --scene-id {scene.scene_id}")
    print("Provider calls: 0"); print("Downloads: 0")
    return 0
if __name__ == "__main__": raise SystemExit(main())
