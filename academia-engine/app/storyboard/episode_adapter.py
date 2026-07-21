import hashlib

from app.models import Camera, Character, Episode, Location, Metadata, Scene

from .contracts import CreativeStoryboard


class StoryboardEpisodeAdapterError(RuntimeError): pass


class StoryboardEpisodeAdapter:
    """Pure, deterministic projection of storyboard semantics into the legacy Episode contract."""
    def __init__(self,character_registry=None,series_registry=None):
        self._character_registry=character_registry; self._series_registry=series_registry
    def adapt(self, storyboard: CreativeStoryboard) -> Episode:
        try:
            storyboard = CreativeStoryboard.model_validate(storyboard)
            canonical = {value.character_id: value for value in storyboard.canonical_characters}
            profiles={}
            if storyboard.series_id and not canonical:
                from app.characters import CharacterRegistry
                from app.series import SeriesRegistry
                bible=(self._series_registry or SeriesRegistry()).load(storyboard.series_id)
                profiles={value.character_id:value for value in (self._character_registry or CharacterRegistry()).require_many(bible.resolved_character_ids)}
            character_names = tuple(dict.fromkeys(
                name for section in storyboard.sections for name in section.characters
            ))
            character_ids = {name: (name if name in canonical or name in profiles else self._character_id(storyboard.storyboard_id, name)) for name in character_names}
            characters = [self._character(storyboard, name, character_ids[name], canonical,profiles) for name in character_names]
            scenes = [Scene(number=section.order, narration=section.lyrics,
                visual_description=section.visual_goal,
                duration_seconds=max(1, round(section.estimated_duration_seconds)),
                character_ids=[character_ids[name] for name in section.characters],
                location=Location(name=section.environment[:150], description=section.environment[:1000], time_of_day="unspecified"),
                camera=Camera(shot_type="wide", angle="eye_level", movement="static",
                    description=section.camera_direction)) for section in storyboard.sections]
            return Episode(id=storyboard.storyboard_id, title=storyboard.title,
                lyrics="\n\n".join(section.lyrics for section in storyboard.sections),
                metadata=Metadata(topic=storyboard.educational_goal, language=storyboard.language,
                    target_age_min=storyboard.audience.target_age_min,
                    target_age_max=storyboard.audience.target_age_max,
                    tags=["educational", "storyboard-derived"]), characters=characters, scenes=scenes)
        except Exception as error:
            if isinstance(error, StoryboardEpisodeAdapterError):
                raise
            raise StoryboardEpisodeAdapterError("Storyboard could not be adapted to Episode.") from error

    @staticmethod
    def _character_id(storyboard_id: str, name: str) -> str:
        digest = hashlib.sha256(f"{storyboard_id}\0{name}".encode("utf-8")).hexdigest()[:12]
        return f"{storyboard_id}-character-{digest}"

    @staticmethod
    def _character(storyboard, name, character_id, canonical,profiles):
        sections = [section for section in storyboard.sections if name in section.characters]
        if name in canonical:
            value = canonical[name]
            appearance = "; ".join(filter(None, (value.age_description, value.appearance,
                "clothing: " + ", ".join(value.clothing) if value.clothing else "",
                "recurring accessories: " + ", ".join(value.recurring_accessories) if value.recurring_accessories else "")))
            return Character(id=value.character_id, name=value.name, role=value.character_type,
                description="; ".join(value.behavior_rules), appearance=appearance)
        if name in profiles:
            value=profiles[name]
            return Character(id=value.character_id,name=value.name,role=value.character_type or "recurring character",
                description="; ".join((*value.personality_traits,*value.behavior_rules,*value.negative_rules))[:1000],
                appearance=value.canonical_description[:1000])
        return Character(id=character_id, name=name, role=sections[0].educational_goal,
            description=sections[0].learning_focus, appearance=sections[0].visual_goal)


class EpisodeService:
    """Resolve an existing Episode or deterministically derive one from a storyboard."""
    def __init__(self, adapter: StoryboardEpisodeAdapter | None = None,character_registry=None,series_registry=None):
        self._adapter = adapter or StoryboardEpisodeAdapter(character_registry,series_registry)

    def resolve(self, source: Episode | CreativeStoryboard) -> Episode:
        if isinstance(source, Episode):
            return Episode.model_validate(source)
        if isinstance(source, CreativeStoryboard):
            return self._adapter.adapt(source)
        raise StoryboardEpisodeAdapterError("Episode source must be an Episode or CreativeStoryboard.")
