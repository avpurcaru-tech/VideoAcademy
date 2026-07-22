from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectServices:
    director_engine: object
    episode_planner: object
    episode_generation_service: object
    video_resumer: object
    lyrics_generation_service: object
    music_engine: object
    audio_variant_video_composer: object
    music_registry: object
    audio_probe: object=None
    music_timeline_service: object=None
    music_timeline_composer: object=None
