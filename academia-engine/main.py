import argparse
import json
from pathlib import Path

from app.engines.story import StoryEngine, StoryRequest
from app.engines.story.openai_generator import OpenAIStoryGenerator
from app.models import Character


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an educational episode JSON.")
    parser.add_argument("topic")
    parser.add_argument("--language", required=True)
    parser.add_argument("--duration", required=True, type=int, metavar="SECONDS")
    parser.add_argument(
        "--characters",
        required=True,
        type=Path,
        help="Path to a JSON array of Character objects",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("storage/projects/episode.json"),
    )
    args = parser.parse_args()

    characters_data = json.loads(args.characters.read_text(encoding="utf-8"))
    request = StoryRequest(
        topic=args.topic,
        language=args.language,
        duration_seconds=args.duration,
        characters=[Character.model_validate(item) for item in characters_data],
    )
    StoryEngine(OpenAIStoryGenerator()).create_episode(request, args.output)


if __name__ == "__main__":
    main()
