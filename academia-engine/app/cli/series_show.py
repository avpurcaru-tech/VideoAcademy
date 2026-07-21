import argparse
from pathlib import Path

from app.cli.song_validate import configure_utf8_output
from app.series import SeriesRegistry, SeriesRegistryError


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description="Inspect safe provider-neutral Series Bible information.")
    parser.add_argument("--series-id", required=True)
    parser.add_argument("--runtime-root", type=Path, default=Path(".runtime") / "series")
    args = parser.parse_args()
    try: bible = SeriesRegistry(args.runtime_root).load(args.series_id)
    except SeriesRegistryError:
        print("Series lookup failed safely."); return 1
    print(f"Series ID: {bible.series_id}")
    print(f"Title: {bible.title}")
    print(f"Language: {bible.language}")
    print(f"Visual style: {bible.visual_style}")
    print("Characters:")
    for value in bible.resolved_character_ids: print(f"- {value}")
    print("Continuity rules:")
    for value in bible.continuity_rules: print(f"- {value}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
