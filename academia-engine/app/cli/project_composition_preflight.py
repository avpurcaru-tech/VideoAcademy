import argparse

from app.cli.video_probe import build_probe
from app.project.composition_preflight import CompositionPreflightService


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect both composition variants without HTTP or FFmpeg execution.")
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    try:
        report = CompositionPreflightService(probe=build_probe()).inspect(args.project_id)
    except Exception as exc:
        category = getattr(exc, "failure_category", "composition_variant_mapping_failed")
        print(f"Project ID: {args.project_id}")
        print(f"Failure category: {category}")
        print("Provider calls: 0")
        print("FFmpeg calls: 0")
        return 1

    print(f"Project ID: {report.project_id}")
    for variant in report.variants:
        print()
        print(f"Variant: {variant.variant_id}")
        print("Master video: " + ("present" if variant.master_present else "missing"))
        print(f"Master path: {variant.master_path}")
        print(f"Master duration: {_duration(variant.master_duration)}")
        print("Audio: " + ("present" if variant.audio_present else "missing"))
        print(f"Audio path: {variant.audio_path}")
        print(f"Audio duration: {_duration(variant.audio_duration)}")
        print("Timeline: " + ("present" if variant.timeline_present else "missing"))
        print(f"Timeline path: {variant.timeline_path}")
        print(f"Timeline duration: {_duration(variant.timeline_duration)}")
        print("Timeline-to-variant mapping: " + ("valid" if variant.mapping_valid else "invalid"))
        print("Duration policy: " + ("valid" if variant.duration_valid else "invalid"))
        print(f"Expected output: {variant.expected_output_path}")
        print("Composition contract: " + ("valid" if variant.valid else "invalid"))
        if variant.failure_category:
            print(f"Failure category: {variant.failure_category}")
            print(f"Failed variant: {variant.variant_id}")
    print()
    print("Provider calls: 0")
    print("FFmpeg calls: 0")
    return 0 if report.valid else 1


def _duration(value: float | None) -> str:
    return str(value) if value is not None else "unavailable"


if __name__ == "__main__":
    raise SystemExit(main())
