import argparse
from pathlib import Path

from pydantic import ValidationError

from app.cli.episode_generate_creative import load_brief
from app.cli.song_validate import configure_utf8_output
from app.config.environment import load_application_environment
from app.storyboard import (StoryboardAlreadyExistsError, StoryboardGenerationError,
    StoryboardGenerationService, StoryboardGeneratorRegistry, StoryboardGeneratorRegistryError,
    StoryboardPersistenceError, StoryboardRepository)


def main() -> int:
    configure_utf8_output(); load_application_environment()
    parser = argparse.ArgumentParser(description="Generate one durable provider-neutral creative storyboard.")
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--generator", choices=("deterministic", "openai"), default="deterministic")
    parser.add_argument("--confirm", action="store_true", help="authorize external AI usage that may incur costs")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--runtime-root", type=Path, default=Path(".runtime") / "storyboards")
    args = parser.parse_args()
    try:
        brief = load_brief(args.brief)
    except (RuntimeError, ValidationError):
        print("Creative brief validation failed."); return 1
    if args.generator == "openai" and not args.confirm:
        print("OpenAI storyboard generation may consume credits.")
        print("No storyboard was generated. Use --confirm to proceed.")
        return 2
    try:
        generator = StoryboardGeneratorRegistry().resolve(args.generator)
        storyboard = StoryboardGenerationService(generator).generate(brief)
        destination = StoryboardRepository(args.runtime_root).save(storyboard, overwrite=args.overwrite)
    except StoryboardAlreadyExistsError:
        print("Storyboard already exists."); return 1
    except (StoryboardGenerationError, StoryboardGeneratorRegistryError, StoryboardPersistenceError):
        print("Storyboard generation failed at a safe provider-neutral boundary."); return 1
    except Exception:
        print("Storyboard generation failed at a safe provider-neutral boundary."); return 1
    print(f"Storyboard ID: {storyboard.storyboard_id}")
    print(f"Sections: {len(storyboard.sections)}")
    print(f"Saved path: {destination}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
