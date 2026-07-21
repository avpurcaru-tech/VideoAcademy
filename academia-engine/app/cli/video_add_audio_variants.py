import argparse
from pathlib import Path

from app.cli.video_add_audio import build_composer
from app.media import (AudioVariantCompositionPartialError,AudioVariantVideoComposer,AudioVideoCompositionError,
                       AudioVideoDurationPolicy)


def main() -> int:
    parser=argparse.ArgumentParser(description="Create one final video for each ordered local audio variant.")
    parser.add_argument("--video",required=True,type=Path); parser.add_argument("--audio",required=True,type=Path,action="append")
    parser.add_argument("--workspace",required=True,type=Path); parser.add_argument("--output-dir",required=True,type=Path)
    parser.add_argument("--duration-policy",required=True,choices=[value.value for value in AudioVideoDurationPolicy])
    parser.add_argument("--overwrite",action="store_true"); parser.add_argument("--timeout",type=float,default=900)
    args=parser.parse_args()
    try: artifacts=AudioVariantVideoComposer(build_composer(args.timeout)).compose_variants(args.video,args.audio,
        args.output_dir,args.workspace,AudioVideoDurationPolicy(args.duration_policy),args.overwrite)
    except AudioVariantCompositionPartialError as error:
        print("Audio variant composition stopped after a partial failure.")
        print(f"Variants completed: {error.completed_count}"); print(f"Failed variant: {error.failed_variant_index}"); return 1
    except AudioVideoCompositionError:
        print("Audio variant composition failed at a safe local media boundary."); return 1
    except Exception:
        print("Audio variant composition failed due to an unexpected local error."); return 1
    print(f"Variants composed: {len(artifacts)}")
    for index,artifact in enumerate(artifacts,start=1):
        print(f"Variant: {index}"); print(f"Saved path: {artifact.local_path}")
        print(f"Bytes: {artifact.byte_size}"); print(f"SHA-256: {artifact.sha256}")
    return 0


if __name__=="__main__": raise SystemExit(main())
