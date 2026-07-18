from __future__ import annotations

import argparse
from pathlib import Path

from src.engines.story import OpenAIStoryModel, StoryEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an educational episode.")
    parser.add_argument("topic", help="Educational topic for the episode")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/episodes/episode.json"),
        help="Path of the generated episode JSON",
    )
    args = parser.parse_args()

    StoryEngine(OpenAIStoryModel()).create_episode(args.topic, args.output)


if __name__ == "__main__":
    main()
