import argparse
from pathlib import Path

from app.music import (MusicEngine, MusicEngineError, MusicPollingPolicy, MusicProviderRegistry,
                       MusicProviderRegistryError, MusicTaskRegistry)


def build_music_engine(provider_name: str) -> MusicEngine:
    runtime=MusicProviderRegistry().resolve(provider_name)
    return MusicEngine({provider_name:runtime.provider},MusicTaskRegistry(),runtime.downloader,default_provider=provider_name)


def main() -> int:
    parser=argparse.ArgumentParser(description="Inspect or continue one durable provider-neutral music task.")
    parser.add_argument("--provider",required=True); parser.add_argument("--task-id",required=True)
    operations=parser.add_mutually_exclusive_group()
    operations.add_argument("--refresh",action="store_true"); operations.add_argument("--wait",action="store_true")
    operations.add_argument("--resume",action="store_true")
    operations.add_argument("--variants",action="store_true"); operations.add_argument("--select-variant",type=int)
    operations.add_argument("--download-all",action="store_true")
    parser.add_argument("--download",type=Path); parser.add_argument("--interval",type=float,default=2)
    parser.add_argument("--output-dir",type=Path)
    parser.add_argument("--timeout",type=float,default=900); parser.add_argument("--max-attempts",type=int)
    args=parser.parse_args()
    if not (args.refresh or args.wait or args.resume or args.variants or args.download_all or args.select_variant is not None or args.download):
        parser.error("select --refresh, --wait, --resume, --variants, --select-variant, or --download")
    if args.refresh and args.download: parser.error("--refresh cannot be combined with --download")
    if args.resume and args.download is None: parser.error("--resume requires --download")
    if args.select_variant is not None and args.download is None: parser.error("--select-variant requires --download")
    if args.variants and args.download is not None: parser.error("--variants cannot be combined with --download")
    if args.download_all and args.output_dir is None: parser.error("--download-all requires --output-dir")
    if args.download_all and args.download is not None: parser.error("--download cannot be combined with --download-all")
    if not args.download_all and args.output_dir is not None: parser.error("--output-dir requires --download-all")
    try:
        engine=build_music_engine(args.provider); policy=MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
        if args.download_all:
            record=engine.download_all_variants(args.task_id,args.output_dir)
        elif args.variants:
            variants=engine.list_variants(args.task_id)
            print(f"Provider: {args.provider}"); print(f"Provider task ID: {args.task_id}"); print("Status: succeeded")
            print(f"Variants: {len(variants)}")
            for variant in variants:
                print(f"Variant: {variant.variant_index}"); print(f"Artifact ID: {variant.artifact_id}"); print(f"Content type: {variant.content_type}")
            return 0
        elif args.select_variant is not None: record=engine.download_variant(args.task_id,args.select_variant,args.download)
        elif args.resume: record=engine.resume(args.task_id,args.download,policy)
        elif args.wait: record=engine.wait_and_download(args.task_id,args.download,policy) if args.download else engine.wait_until_terminal(args.task_id,policy)
        elif args.download: record=engine.download(args.task_id,args.download)
        else: record=engine.refresh(args.task_id)
        if record.provider!=args.provider: raise MusicEngineError("Music task provider does not match.")
    except (MusicEngineError,MusicProviderRegistryError):
        print("Music engine task operation failed at a safe provider-neutral boundary."); return 1
    except Exception:
        print("Music engine task operation failed due to an unexpected local error."); return 1
    artifact=record.artifact
    if args.download_all:
        print(f"Provider: {record.provider}"); print(f"Provider task ID: {record.provider_task_id}")
        print(f"Status: {record.normalized_status.value}"); print(f"Variants downloaded: {len(record.artifact_set.artifacts)}")
        for durable in record.artifact_set.artifacts:
            print(f"Variant: {durable.variant_index}"); print(f"Artifact ID: {durable.artifact_id}")
            print(f"Saved path: {durable.local_path}"); print(f"Bytes: {durable.byte_size}")
            print(f"SHA-256: {durable.sha256}"); print(f"Content type: {durable.content_type}")
        return 0
    if args.select_variant is not None:
        print(f"Provider: {record.provider}"); print(f"Provider task ID: {record.provider_task_id}")
        print(f"Selected variant: {args.select_variant}"); print(f"Artifact ID: {artifact.artifact_id}")
        print(f"Saved path: {artifact.local_path}"); print(f"Bytes: {artifact.byte_size}")
        print(f"SHA-256: {artifact.sha256}"); print(f"Content type: {artifact.content_type}"); return 0
    print(f"Provider: {record.provider}"); print(f"Provider task ID: {record.provider_task_id}")
    print(f"External correlation ID: {record.external_correlation_id or ''}"); print(f"Status: {record.normalized_status.value}")
    print(f"Local artifact: {artifact.local_path if artifact else ''}"); print(f"Bytes: {artifact.byte_size if artifact else ''}")
    print(f"SHA-256: {artifact.sha256 if artifact else ''}"); print(f"Content type: {artifact.content_type if artifact else ''}")
    return 0


if __name__=="__main__": raise SystemExit(main())
