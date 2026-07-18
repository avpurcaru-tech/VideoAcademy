import argparse
from pathlib import Path

from app.media import (
    FFmpegVideoConcatenator,
    FFprobeAdapter,
    SubprocessProcessRunner,
    VideoConcatenationError,
)


def build_concatenator() -> FFmpegVideoConcatenator:
    runner = SubprocessProcessRunner()
    return FFmpegVideoConcatenator(runner, FFprobeAdapter(runner))


def main() -> int:
    parser = argparse.ArgumentParser(description="Concatenate normalized local video scenes.")
    parser.add_argument("--input", required=True, action="append", type=Path, help="Ordered scene path")
    parser.add_argument("--output", required=True, type=Path, help="Explicit final video path")
    args = parser.parse_args()

    try:
        artifact = build_concatenator().concatenate_videos(args.input, args.output)
    except VideoConcatenationError as error:
        print(f"Video concatenation failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Video concatenation failed due to an unexpected local error.")
        return 1

    media = artifact.media_info
    print(f"Saved path: {artifact.local_path}")
    print(f"Sources: {artifact.source_count}")
    print(f"Bytes: {artifact.byte_size}")
    print(f"SHA-256: {artifact.sha256}")
    print(f"Duration: {media.duration_seconds}")
    print(f"Resolution: {media.width}x{media.height}")
    print(f"Frame rate: {media.frame_rate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
