import argparse
from app.project import ProjectRegistry
from app.production import ProductionRegistry, VisualIdentityValidatorFactory, is_awaiting_identity_review
from app.services import TaskRegistry

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--project-id",required=True); args=parser.parse_args()
    project=ProjectRegistry().load(args.project_id)
    runtime=VisualIdentityValidatorFactory().construct_runtime(project.identity_validation_mode)
    production=ProductionRegistry().load(project.video_production_id)
    tasks=TaskRegistry()
    awaiting=sum(1 for scene in production.scenes if is_awaiting_identity_review(scene))
    reconciliation=0
    for scene in production.scenes:
        downloaded=scene.local_path is not None
        if not downloaded and scene.provider_task_id:
            try: downloaded=tasks.load(scene.provider_task_id).artifact is not None
            except Exception: downloaded=False
        if downloaded and scene.identity_validated is not True and scene.character_reference_images and not is_awaiting_identity_review(scene):
            reconciliation+=1
    implementation=getattr(runtime.validator,"implementation","none")
    print(f"Validation mode: {runtime.mode.value}")
    print(f"Validator implementation: {implementation}")
    print(f"Automatic validation available: {'yes' if runtime.automatic_available else 'no'}")
    print(f"Manual review available: {'yes' if runtime.manual_review_available else 'no'}")
    print(f"Downloaded scenes awaiting validation: {awaiting}")
    print(f"Downloaded scenes requiring reconciliation: {reconciliation}")
    print("Provider calls: 0"); print("Downloads: 0")
    return 0
if __name__ == "__main__": raise SystemExit(main())
