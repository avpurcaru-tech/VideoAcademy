import argparse
from pathlib import Path

from app.media import (
    AudioLoudnessProfile,
    FFmpegLoudnessNormalizer,
    FFprobeAdapter,
    LoudnessNormalizationError,
    SubprocessProcessRunner,
)


def build_normalizer() -> FFmpegLoudnessNormalizer:
    runner = SubprocessProcessRunner()
    return FFmpegLoudnessNormalizer(runner, FFprobeAdapter(runner))


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize existing video audio loudness.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        artifact = build_normalizer().normalize_loudness(
            args.input, args.output, AudioLoudnessProfile.academia_default()
        )
    except LoudnessNormalizationError as error:
        print(f"Video loudness normalization failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Video loudness normalization failed due to an unexpected local error.")
        return 1
    media = artifact.media_info
    print(f"Saved path: {artifact.local_path}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    print(f"Has audio: {str(media.has_audio).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
