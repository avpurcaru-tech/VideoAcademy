import argparse
from pathlib import Path

from app.media import (AudioVideoCompositionError,AudioVideoCompositionRequest,AudioVideoDurationPolicy,
                       FFmpegAudioVideoComposer,FFprobeAdapter,SubprocessProcessRunner)


def build_composer(timeout: float|None=None):
    runner=SubprocessProcessRunner(); return FFmpegAudioVideoComposer(runner,FFprobeAdapter(runner),timeout_seconds=timeout)


def main() -> int:
    parser=argparse.ArgumentParser(description="Replace a local video's audio with one local audio artifact.")
    parser.add_argument("--video",required=True,type=Path); parser.add_argument("--audio",required=True,type=Path)
    parser.add_argument("--workspace",required=True,type=Path); parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--duration-policy",required=True,choices=[value.value for value in AudioVideoDurationPolicy])
    parser.add_argument("--overwrite",action="store_true"); parser.add_argument("--timeout",type=float,default=900)
    args=parser.parse_args()
    try:
        artifact=build_composer(args.timeout).compose(AudioVideoCompositionRequest(video_source=args.video,
            audio_source=args.audio,destination=args.output,workspace=args.workspace,
            duration_policy=args.duration_policy,overwrite=args.overwrite))
    except AudioVideoCompositionError:
        print("Audio/video composition failed at a safe local media boundary."); return 1
    except Exception:
        print("Audio/video composition failed due to an unexpected local error."); return 1
    _print_artifact(artifact); return 0


def _print_artifact(artifact):
    info=artifact.media_info
    print(f"Video source: {artifact.video_source_path}"); print(f"Audio source: {artifact.audio_source_path}")
    print(f"Duration policy: {artifact.duration_policy.value}"); print(f"Saved path: {artifact.local_path}")
    print(f"Bytes: {artifact.byte_size}"); print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {info.duration_seconds}"); print(f"Resolution: {info.width}x{info.height}")
    print(f"Frame rate: {info.frame_rate}"); print(f"Video codec: {info.video_codec}")
    print(f"Audio codec: {info.audio_codec}"); print(f"Has audio: {'yes' if info.has_audio else 'no'}")


if __name__=="__main__": raise SystemExit(main())
