from pathlib import Path

from app.models import Episode


class JsonEpisodeWriter:
    def write(self, episode: Episode, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
