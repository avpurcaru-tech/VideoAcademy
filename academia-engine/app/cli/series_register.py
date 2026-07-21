import argparse
from pathlib import Path

from pydantic import ValidationError

from app.cli.song_validate import configure_utf8_output
from app.series import SeriesBible, SeriesRegistry, SeriesRegistryError
from app.characters import CharacterRegistry,CharacterRegistryError


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description="Register a provider-neutral Series Bible.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--runtime-root", type=Path, default=Path(".runtime") / "series")
    parser.add_argument("--character-runtime-root",type=Path,default=Path(".runtime")/"characters")
    args = parser.parse_args()
    try:
        bible = SeriesBible.model_validate_json(args.input.read_text(encoding="utf-8"))
        path = SeriesRegistry(args.runtime_root,CharacterRegistry(args.character_runtime_root)).register(bible)
    except (OSError, ValidationError, SeriesRegistryError,CharacterRegistryError):
        print("Series registration failed safely."); return 1
    print(f"Series ID: {bible.series_id}")
    print(f"Title: {bible.title}")
    print("Characters: " + ", ".join(bible.resolved_character_ids))
    print(f"Saved path: {path}")
    print("Registration: succeeded")
    return 0


if __name__ == "__main__": raise SystemExit(main())
