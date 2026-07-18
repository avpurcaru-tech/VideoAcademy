import argparse
from pathlib import Path

from app.media import (
    AudioLoudnessProfile,
    FFmpegLoudnessNormalizer,
    FFmpegVideoConcatenator,
    FFmpegVideoNormalizer,
    FFprobeAdapter,
    SubprocessProcessRunner,
    VideoAssemblyError,
    VideoAssemblyRequest,
    VideoAssemblyService,
    VideoNormalizationProfile,
)


def build_assembly_service() -> VideoAssemblyService:
    runner = SubprocessProcessRunner()
    probe = FFprobeAdapter(runner)
    return VideoAssemblyService(
        FFmpegVideoNormalizer(runner, probe),
        FFmpegVideoConcatenator(runner, probe),
        FFmpegLoudnessNormalizer(runner, probe),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Assemble ordered local scenes into one normalized final video."
    )
    parser.add_argument("--input", required=True, action="append", type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    request = VideoAssemblyRequest(
        sources=tuple(args.input),
        destination=args.output,
        workspace=args.workspace,
        normalization_profile=VideoNormalizationProfile.academia_default(),
        loudness_profile=AudioLoudnessProfile.academia_default(),
        overwrite=args.overwrite,
    )
    try:
        artifact = build_assembly_service().assemble(request)
    except VideoAssemblyError as error:
        print(f"Video assembly failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Video assembly failed due to an unexpected local error.")
        return 1

    media = artifact.media_info
    print(f"Saved path: {artifact.local_path}")
    print(f"Sources: {artifact.source_count}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    print(f"Has audio: {str(media.has_audio).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
