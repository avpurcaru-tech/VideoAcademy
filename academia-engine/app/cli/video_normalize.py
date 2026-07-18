import argparse
from pathlib import Path

from app.media import (
    FFmpegVideoNormalizer,
    FFprobeAdapter,
    SubprocessProcessRunner,
    VideoNormalizationError,
    VideoNormalizationProfile,
)


def build_normalizer() -> FFmpegVideoNormalizer:
    runner = SubprocessProcessRunner()
    return FFmpegVideoNormalizer(runner, FFprobeAdapter(runner))


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize one local video deterministically.")
    parser.add_argument("--input", required=True, type=Path, help="Source video path")
    parser.add_argument("--output", required=True, type=Path, help="Explicit final video path")
    args = parser.parse_args()

    try:
        artifact = build_normalizer().normalize_video(
            args.input,
            args.output,
            VideoNormalizationProfile.academia_default(),
        )
    except VideoNormalizationError as error:
        print(f"Video normalization failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Video normalization failed due to an unexpected local error.")
        return 1

    media = artifact.media_info
    print(f"Saved path: {artifact.local_path}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
