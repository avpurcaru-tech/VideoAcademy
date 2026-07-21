import argparse

from app.config.environment import load_application_environment
from app.music import (MusicEngine,MusicEngineError,MusicProviderConfigurationError,MusicProviderRegistry,
                       MusicProviderRegistryError,MusicTaskRegistry)


def build_music_engine(provider_name: str) -> MusicEngine:
    runtime=MusicProviderRegistry().resolve(provider_name)
    return MusicEngine({provider_name:runtime.provider},MusicTaskRegistry(),runtime.downloader,
                       default_provider=provider_name)


def main() -> int:
    load_application_environment()
    parser=argparse.ArgumentParser(description="Adopt one known music provider task without submitting generation.")
    parser.add_argument("--provider",required=True); parser.add_argument("--task-id",required=True)
    args=parser.parse_args()
    try: record=build_music_engine(args.provider).reconcile_existing_task(args.provider,args.task_id)
    except MusicProviderConfigurationError:
        print("Third-party music provider configuration is missing." if args.provider=="sunoapi_org"
              else "Music provider configuration is missing."); return 1
    except (MusicEngineError,MusicProviderRegistryError):
        print("Music task reconciliation failed at a safe provider-neutral boundary."); return 1
    except Exception:
        print("Music task reconciliation failed due to an unexpected local error."); return 1
    print(f"Provider: {record.provider}"); print(f"Provider task ID: {record.provider_task_id}")
    print(f"Status: {record.normalized_status.value}"); print("Task reconciliation: succeeded")
    return 0


if __name__=="__main__": raise SystemExit(main())
