from typing import Protocol, runtime_checkable

from app.creative import EducationalCreativeBrief

from .contracts import CreativeStoryboard, StoryboardAudience, StoryboardMusicDirection, StoryboardSection


@runtime_checkable
class StoryboardGenerator(Protocol):
    def generate_storyboard(self, brief: EducationalCreativeBrief, series_bible=None, character_profiles=()) -> CreativeStoryboard: ...


class DeterministicStoryboardGenerator:
    def generate_storyboard(self, brief, series_bible=None, character_profiles=()):
        base = brief.target_duration_seconds / brief.scene_count
        sections = []
        for index in range(1, brief.scene_count + 1):
            objective = brief.learning_objectives[(index - 1) % len(brief.learning_objectives)]
            duration = base if index < brief.scene_count else brief.target_duration_seconds - base * (brief.scene_count - 1)
            sections.append(StoryboardSection(section_id=f"{brief.brief_id}-section-{index:02d}", order=index,
                section_type="introduction" if index == 1 else "lesson",
                educational_goal=objective, learning_focus=objective,
                visual_goal=f"Show {objective} clearly in an original age-appropriate visual sequence.",
                lyrics=f"An original short educational line about {objective}.",
                characters=(series_bible.resolved_character_ids if series_bible else
                    (brief.main_character_hint or "friendly original guide",)), objects=(),
                environment=brief.location_hint or f"A safe cheerful setting in {brief.visual_style} style.",
                camera_direction="A stable eye-level view with gentle movement.", emotion=brief.tone,
                estimated_duration_seconds=duration))
        return CreativeStoryboard(storyboard_id=brief.brief_id, series_id=series_bible.series_id if series_bible else None,
            required_character_ids=series_bible.resolved_character_ids if series_bible else (), title=f"Learning {brief.topic}",
            language=brief.language, audience=StoryboardAudience(target_age_min=brief.target_age_min,
                target_age_max=brief.target_age_max), educational_goal="; ".join(brief.learning_objectives),
            music_direction=StoryboardMusicDirection(style="original educational song", mood=brief.tone,
                tempo_bpm=110, vocals="clear child-friendly vocals",
                instrumentation=("ukulele", "xylophone", "light percussion")),
            target_duration_seconds=brief.target_duration_seconds, sections=tuple(sections))
