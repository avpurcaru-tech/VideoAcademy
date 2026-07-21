from typing import Protocol,runtime_checkable

from app.models import Camera,Character,Episode,Location,Metadata,Scene

from .contracts import EducationalCreativeBrief


@runtime_checkable
class EpisodeGenerator(Protocol):
    def generate_episode(self,brief: EducationalCreativeBrief) -> Episode: ...


class DeterministicEpisodeGenerator:
    def generate_episode(self,brief):
        character_id=f"{brief.brief_id}-guide"
        character=Character(id=character_id,name="Lumi",role="friendly educational guide",
            description=f"An original friendly guide who helps preschool children learn about {brief.topic}.",
            appearance=f"A bright, simple cartoon character in {brief.visual_style} style.")
        base=max(1,round(brief.target_duration_seconds/brief.scene_count)); scenes=[]
        for index in range(1,brief.scene_count+1):
            objective=brief.learning_objectives[(index-1)%len(brief.learning_objectives)]
            scenes.append(Scene(number=index,narration=f"Lumi explores lesson {index}: {objective}.",
                visual_description=f"Lumi demonstrates {objective} in a clear, cheerful scene.",duration_seconds=base,
                character_ids=[character_id],location=Location(name=brief.location_hint or "learning garden",
                    description=f"An original safe preschool setting in {brief.visual_style} style.",time_of_day="morning"),
                camera=Camera(shot_type="wide" if index==1 else "medium",angle="eye_level",movement="pan",
                    description="A gentle stable camera view keeps the lesson easy to follow.")))
        return Episode(id=brief.brief_id,title=f"Lumi learns {brief.topic}",
            lyrics=f"An original cheerful educational song about {brief.topic}." if brief.song_required else "Original educational narration.",
            metadata=Metadata(topic=brief.topic,language=brief.language,target_age_min=brief.target_age_min,
                              target_age_max=brief.target_age_max,tags=["preschool","educational","original"]),
            characters=[character],scenes=scenes)
