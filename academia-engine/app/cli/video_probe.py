import argparse
from pathlib import Path

from app.media import FFprobeAdapter, MediaProbeError, SubprocessProcessRunner


def build_probe() -> FFprobeAdapter:
    return FFprobeAdapter(SubprocessProcessRunner())


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect one local video with FFprobe.")
    parser.add_argument("--input", required=True, type=Path, help="Local video path")
    args = parser.parse_args()

    try:
        result = build_probe().probe_video(args.input)
    except MediaProbeError as error:
        print(f"Video probe failed: {str(error)[:500]}")
        return 1
    except Exception:
        print("Video probe failed due to an unexpected local error.")
        return 1

    print(f"Path: {result.local_path}")
    print(f"Duration: {result.duration_seconds}")
    print(f"Resolution: {result.width}x{result.height}")
    print(f"Frame rate: {result.frame_rate}")
    print(f"Video codec: {result.video_codec}")
    print(f"Audio codec: {result.audio_codec or ''}")
    print(f"Has audio: {str(result.has_audio).lower()}")
    print(f"Container: {result.container_format}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
