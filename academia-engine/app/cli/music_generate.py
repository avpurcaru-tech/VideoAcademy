import argparse
from pathlib import Path

from pydantic import ValidationError

from app.cli.song_validate import SongInputError,configure_utf8_output,load_contract
from app.music import (MusicEngine,MusicEngineError,MusicGenerationRequest,MusicPollingPolicy,
                       MusicProviderRegistry,MusicProviderRegistryError,MusicTaskRegistry)
from app.song import LyricsPlan,MusicPlan


def build_music_engine(provider_name: str) -> MusicEngine:
    runtime=MusicProviderRegistry().resolve(provider_name)
    return MusicEngine({provider_name:runtime.provider},MusicTaskRegistry(),runtime.downloader,default_provider=provider_name)


def main() -> int:
    configure_utf8_output(); parser=argparse.ArgumentParser(description="Generate one song through a real music provider.")
    parser.add_argument("--lyrics",required=True,type=Path); parser.add_argument("--music-plan",required=True,type=Path)
    parser.add_argument("--provider",required=True); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--interval",type=float,default=5); parser.add_argument("--timeout",type=float,default=900)
    parser.add_argument("--max-attempts",type=int); parser.add_argument("--confirm",action="store_true"); args=parser.parse_args()
    try:
        lyrics=load_contract(args.lyrics,LyricsPlan,"Lyrics plan"); plan=load_contract(args.music_plan,MusicPlan,"Music plan")
        request=MusicGenerationRequest(song_id=lyrics.song_id,title=lyrics.title,lyrics=lyrics,music_plan=plan)
        policy=MusicPollingPolicy(interval_seconds=args.interval,timeout_seconds=args.timeout,max_attempts=args.max_attempts)
    except SongInputError as error:
        for line in error.lines: print(line)
        return 1
    except ValidationError as error:
        print("Music generation request validation failed:")
        for detail in error.errors(include_url=False,include_context=False,include_input=False):
            field=".".join(str(part) for part in detail["loc"]) or "root"
            print(f"- {field}: {str(detail['type']).replace('_',' ')}")
        return 1
    print("Real music generation may consume provider credits."); print(f"Provider: {args.provider}"); print(f"Song ID: {lyrics.song_id}")
    if not args.confirm:
        print("No task was submitted. Re-run with --confirm to proceed."); return 2
    try:
        record=build_music_engine(args.provider).generate(request,args.output,policy,args.provider)
    except (MusicEngineError,MusicProviderRegistryError):
        print("Music generation failed at a safe provider boundary. Do not automatically resubmit; inspect durable task state first."); return 1
    except Exception:
        print("Music generation failed due to an unexpected local error. Do not automatically resubmit."); return 1
    artifact=record.artifact
    print(f"Provider task ID: {record.provider_task_id}"); print(f"Status: {record.normalized_status.value}")
    print(f"Saved path: {artifact.local_path}"); print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}"); print(f"Content type: {artifact.content_type}"); return 0


if __name__=="__main__": raise SystemExit(main())
