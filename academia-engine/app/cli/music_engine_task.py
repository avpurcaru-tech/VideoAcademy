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
    parser.add_argument("--download",type=Path); parser.add_argument("--interval",type=float,default=2)
    parser.add_argument("--timeout",type=float,default=900); parser.add_argument("--max-attempts",type=int)
    args=parser.parse_args()
    if not (args.refresh or args.wait or args.resume or args.download): parser.error("select --refresh, --wait, --resume, or --download")
    if args.refresh and args.download: parser.error("--refresh cannot be combined with --download")
    if args.resume and args.download is None: parser.error("--resume requires --download")
    try:
        engine=build_music_engine(args.provider); policy=MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
        if args.resume: record=engine.resume(args.task_id,args.download,policy)
        elif args.wait: record=engine.wait_and_download(args.task_id,args.download,policy) if args.download else engine.wait_until_terminal(args.task_id,policy)
        elif args.download: record=engine.download(args.task_id,args.download)
        else: record=engine.refresh(args.task_id)
        if record.provider!=args.provider: raise MusicEngineError("Music task provider does not match.")
    except (MusicEngineError,MusicProviderRegistryError):
        print("Music engine task operation failed at a safe provider-neutral boundary."); return 1
    except Exception:
        print("Music engine task operation failed due to an unexpected local error."); return 1
    artifact=record.artifact
    print(f"Provider: {record.provider}"); print(f"Provider task ID: {record.provider_task_id}")
    print(f"External correlation ID: {record.external_correlation_id or ''}"); print(f"Status: {record.normalized_status.value}")
    print(f"Local artifact: {artifact.local_path if artifact else ''}"); print(f"Bytes: {artifact.byte_size if artifact else ''}")
    print(f"SHA-256: {artifact.sha256 if artifact else ''}"); print(f"Content type: {artifact.content_type if artifact else ''}")
    return 0


if __name__=="__main__": raise SystemExit(main())
