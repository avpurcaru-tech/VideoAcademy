from .contracts import SeriesBible
from .registry import SeriesRegistry


class SeriesService:
    def __init__(self, registry: SeriesRegistry | None = None): self._registry = registry or SeriesRegistry()
    def resolve(self, series_id: str | None) -> SeriesBible | None:
        return None if series_id is None else self._registry.load(series_id)
