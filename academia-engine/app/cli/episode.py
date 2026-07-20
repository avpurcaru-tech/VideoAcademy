import argparse
from pathlib import Path

from app.production import (
    EpisodeFinalRenderError, EpisodeProductionConflictError, EpisodeProductionError,
    EpisodeProductionNotFoundError, EpisodeProductionPlanningError, EpisodeProductionRegistryError,
    EpisodeProductionRequestConflictError,
    EpisodeProductionSummaryService, EpisodeProviderSceneFailedError, EpisodeSceneArtifactMissingError,
    EpisodeSceneDownloadError, EpisodeScenePollingError, EpisodeSceneSubmissionError,
    EpisodeTimelineValidationError, ProductionRegistry, ProductionRegistryError,
    ProductionRegistryNotFoundError,
    ProductionIntegrityService, ProductionArtifactIntegrityError,
    ProductionArtifactMetadataReconciler, ArtifactMetadataReconciliationError,
    RuntimeCleanupService, RuntimeCleanupError, CleanupConfirmationError, CleanupCandidateSafetyError,
)
from app.services import VideoEngineTimeoutError, VideoPollingPolicy

from .episode_plan import SafePlanningDiagnostic, planning_configuration, print_plan
from .episode_produce import build_orchestrator
from .episode_project_plan import build_project_planner, load_episode


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(
        description="Safely operate the provider-neutral episode production lifecycle.",
        epilog=("Credit safety: --generate submits only with --confirm. "
                "Use --plan --preflight for a non-persistent validation, --resume for an existing "
                "production, and --cleanup without --confirm for a dry run."))
    operations=parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--plan",action="store_true",help="validate and optionally persist an episode production plan")
    operations.add_argument("--generate",action="store_true",help="plan and generate; provider submission requires --confirm")
    operations.add_argument("--status",action="store_true",help="read durable production status without provider calls")
    operations.add_argument("--resume",action="store_true",help="continue an existing production without resubmitting durable tasks")
    operations.add_argument("--verify",action="store_true",help="read-only verification of durable artifact integrity")
    operations.add_argument("--repair-metadata",action="store_true",help="reconstruct metadata for one existing local scene artifact")
    operations.add_argument("--cleanup",action="store_true",help="scan disposable runtime data; dry-run unless --confirm is supplied")
    parser.add_argument("--input",type=Path,help="Episode JSON input for planning or generation")
    parser.add_argument("--production-id",help="unique durable production identifier")
    parser.add_argument("--provider",default="kling",help="generation provider for a new plan (default: kling)")
    parser.add_argument("--scene-output-dir",type=Path,help="durable scene artifact directory")
    parser.add_argument("--workspace",type=Path,help="disposable media workspace")
    parser.add_argument("--output",type=Path,help="durable final video path")
    parser.add_argument("--transition",choices=("cut","fade","dissolve"),default="cut")
    parser.add_argument("--transition-duration",type=float); parser.add_argument("--interval",type=float,default=2)
    parser.add_argument("--timeout",type=float,default=900); parser.add_argument("--max-attempts",type=int)
    parser.add_argument("--preflight",action="store_true",help="validate planning without writing request or production state")
    parser.add_argument("--confirm",action="store_true",help="explicitly authorize generation cost or eligible cleanup deletion")
    parser.add_argument("--scene-id",help="scene targeted by --repair-metadata")
    parser.add_argument("--older-than-hours",type=float,help="minimum disposable-path age for cleanup; required for deletion")
    return parser


def main() -> int:
    parser=_parser(); args=parser.parse_args(); _validate_arguments(parser,args)
    if args.cleanup: return _cleanup(args)
    if args.status: return _status(args.production_id)
    if args.verify: return _verify(args.production_id)
    if args.repair_metadata: return _repair_metadata(args.production_id,args.scene_id,parser)
    if args.resume: return _resume(args)
    if args.generate: return _generate(args)
    return _plan(args)


def _validate_arguments(parser,args) -> None:
    if not args.cleanup and not args.production_id: parser.error("--production-id is required")
    planning=args.plan or args.generate
    if planning:
        missing=[name for name,value in (("--input",args.input),("--scene-output-dir",args.scene_output_dir),("--workspace",args.workspace),("--output",args.output)) if value is None]
        if missing: parser.error(f"planning requires {', '.join(missing)}")
    elif args.input is not None: parser.error("--input is only valid with --plan or --generate")
    if args.preflight and not planning: parser.error("--preflight is only valid with --plan or --generate")
    if args.confirm and not (args.generate or args.cleanup): parser.error("--confirm is only valid with --generate or --cleanup")
    if args.older_than_hours is not None and not args.cleanup: parser.error("--older-than-hours is only valid with --cleanup")
    if args.cleanup and any(value is not None for value in (args.input,args.scene_output_dir,args.workspace,args.output,args.scene_id)):
        parser.error("--cleanup does not accept planning, artifact, or media paths")
    if (args.status or args.verify) and any(value is not None for value in (args.scene_output_dir,args.workspace,args.output)):
        parser.error("read-only operations accept only durable production identity")
    if args.repair_metadata and not args.scene_id: parser.error("--repair-metadata requires --scene-id")
    if args.scene_id and not args.repair_metadata: parser.error("--scene-id is only valid with --repair-metadata")


def _plan(args) -> int:
    try:
        episode=load_episode(args.input); planner=build_project_planner()
        request=planner.preflight(episode,**planning_configuration(args))
        if not args.preflight: planner.persist(request)
    except Exception as error:
        return _safe_error(args.production_id,error,"planning")
    print("Operation: plan"); print_plan(request,preflight=args.preflight); return 0


def _generate(args) -> int:
    try:
        registry=ProductionRegistry()
        if registry.exists(args.production_id):
            print("Production already exists."); print("Use --status to inspect it or --resume to continue it."); return 1
        episode=load_episode(args.input); planner=build_project_planner(); configuration=planning_configuration(args)
        request=planner.preflight(episode,**configuration)
        if not args.confirm:
            print("Operation: generate"); print_plan(request,preflight=True)
            print("Real provider generation may consume credits. Use --confirm to generate."); return 2
        planner.persist(request)
        policy=VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
        result=build_orchestrator().produce(request,policy)
    except Exception as error:
        return _safe_error(args.production_id,error,"generation")
    _print_result("generate",result); return 0


def _status(production_id: str) -> int:
    try: summary=EpisodeProductionSummaryService(ProductionRegistry()).load(production_id)
    except ProductionRegistryNotFoundError:
        print("Production not found."); return 1
    except ProductionRegistryError:
        print("Registry failure while reading production status."); return 1
    print("Operation: status"); print(f"Production ID: {summary.production_id}"); print(f"Status: {summary.status.value}")
    print(f"Scenes: {len(summary.scenes)}")
    for scene in summary.scenes:
        print(f"Scene: {scene.scene_id}"); print(f"Production scene status: {scene.production_status.value}")
        print(f"Provider status: {scene.provider_status or '-'}"); print(f"Provider task ID: {scene.provider_task_id or '-'}")
        print(f"Local artifact: {scene.local_artifact or '-'}")
    print(f"Final artifact present: {'yes' if summary.final_artifact_present else 'no'}")
    if summary.final_path: print(f"Final path: {summary.final_path}")
    return 0


def _resume(args) -> int:
    try:
        policy=VideoPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
        result=build_orchestrator().resume(args.production_id,policy)
    except Exception as error: return _safe_error(args.production_id,error,"resume")
    _print_result("resume",result); return 0


def _verify(production_id: str) -> int:
    try:
        record=ProductionRegistry().load(production_id)
        report=ProductionIntegrityService().verify_production(record)
    except ProductionRegistryNotFoundError:
        print("Production not found."); return 1
    except ProductionRegistryError:
        print("Registry failure while verifying production."); return 1
    print("Operation: verify"); print(f"Production ID: {report.production_id}"); print(f"Status: {report.status.value}")
    for scene in report.scenes:
        print(f"Scene: {scene.scene_id}"); print(f"Artifact integrity: {scene.artifact.state.value}")
    print(f"Final artifact integrity: {report.final_artifact.state.value}")
    return 0 if report.valid else 1


def _repair_metadata(production_id: str, scene_id: str, parser) -> int:
    try:
        record=ProductionArtifactMetadataReconciler(ProductionRegistry()).reconcile_scene(production_id,scene_id)
        scene=next(scene for scene in record.scenes if scene.scene_id==scene_id)
    except ArtifactMetadataReconciliationError:
        print("Artifact metadata reconciliation failed at a safe local boundary."); return 1
    print("Operation: repair-metadata"); print(f"Production ID: {record.production_id}"); print(f"Scene: {scene.scene_id}")
    print(f"Artifact ID: {scene.artifact_id}"); print(f"Local artifact: {scene.local_path}")
    print(f"Bytes: {scene.byte_size}"); print(f"SHA-256: {scene.sha256}"); print("Metadata reconciliation: succeeded")
    return 0


def _cleanup(args) -> int:
    service=RuntimeCleanupService()
    try:
        seconds=None if args.older_than_hours is None else args.older_than_hours*3600
        plan=service.scan(Path.cwd()/".runtime",seconds)
        print("Operation: cleanup"); print(f"Mode: {'confirmed' if args.confirm else 'dry-run'}")
        print(f"Candidates: {plan.candidate_count}"); print(f"Recoverable bytes: {plan.recoverable_bytes}")
        for entry in plan.entries:
            print(f"Candidate: {entry.path}"); print(f"Category: {entry.category.value}"); print(f"Reason: {entry.reason}")
        if not args.confirm: return 0
        if args.older_than_hours is None:
            print("Cleanup confirmation requires an age threshold."); return 1
        result=service.execute(plan)
        print(f"Deleted: {result.deleted_count}"); print(f"Failed: {result.failed_count}")
        print(f"Recovered bytes: {result.recovered_bytes}")
        if result.failed_count:
            print("Cleanup completed with one or more failures."); return 1
        return 0
    except CleanupConfirmationError:
        print("Cleanup confirmation requires an age threshold."); return 1
    except CleanupCandidateSafetyError:
        print("Cleanup candidate is outside approved runtime roots."); return 1
    except RuntimeCleanupError:
        print("Cleanup root is invalid."); return 1


def _print_result(operation,result) -> None:
    print(f"Operation: {operation}"); print(f"Production ID: {result.production_id}")
    print(f"Status: {result.status.value}"); print(f"Scenes: {len(result.scenes)}")
    if result.final_artifact: print(f"Final path: {result.final_artifact.local_path}")


def _safe_error(production_id,error,operation) -> int:
    categories=((SafePlanningDiagnostic,"Semantic input error."),(EpisodeProductionRequestConflictError,"Request-store conflict."),
        (EpisodeProductionConflictError,"Production conflict."),(EpisodeProductionNotFoundError,"Production not found."),
        (EpisodeSceneSubmissionError,"Provider submission failure."),(EpisodeProviderSceneFailedError,"Provider task failure."),
        (VideoEngineTimeoutError,"Provider polling timeout."),(EpisodeScenePollingError,"Provider polling failure."),
        (EpisodeSceneDownloadError,"Scene download failure."),(EpisodeSceneArtifactMissingError,"Local artifact failure."),
        (EpisodeTimelineValidationError,"Timeline validation failure."),(EpisodeFinalRenderError,"Final render failure."),
        (ProductionArtifactIntegrityError,"Artifact integrity failure."),
        (EpisodeProductionRegistryError,"Registry failure."),(EpisodeProductionPlanningError,"Planning failure."))
    print(next((message for kind,message in categories if isinstance(error,kind)),f"Safe {operation} failure."))
    try: exists=ProductionRegistry().exists(production_id)
    except ProductionRegistryError: exists=False
    if exists:
        print("Durable production state exists."); print("Use --status to inspect it or --resume to continue it.")
    elif isinstance(error,EpisodeSceneSubmissionError):
        print("A provider task may require orphan reconciliation before retrying.")
    return 1


if __name__=="__main__": raise SystemExit(main())
