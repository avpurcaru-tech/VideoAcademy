import argparse

from app.characters import CharacterRegistry
from app.config import (KlingGenerationSettings,KLING_PROMPT_MAX_CHARACTERS,
    KLING_PROMPT_RECOMMENDED_CHARACTERS)
from app.music_timeline import MusicTimeline
from app.production import SceneDurationPolicy,StoryboardVideoPlanner
from app.project import ProjectGenerationService,ProjectRegistry
from app.providers import KlingTextToVideoMapper
from app.series import SeriesRegistry
from app.storyboard import CreativeStoryboard


def build_preflight(project_id,registry=None):
    registry=registry or ProjectRegistry(); record=registry.load(project_id); root=record.lyrics_path.parent.parent
    storyboard=CreativeStoryboard.model_validate_json((root/"input"/"storyboard.json").read_text(encoding="utf-8"))
    timelines=tuple(MusicTimeline.model_validate_json((record.music_directory/f"timeline-variant-{index:02d}.json").read_text(encoding="utf-8"))
        for index in (1,2))
    ProjectGenerationService._validate_timeline_mapping(storyboard,timelines)
    settings=KlingGenerationSettings.from_environment(); series=SeriesRegistry(); characters=CharacterRegistry()
    requests=StoryboardVideoPlanner(SceneDurationPolicy(settings.duration),characters,series).build(storyboard,record.video_production_id)
    mapper=KlingTextToVideoMapper(settings); diagnostics=[]
    for index,(section,request) in enumerate(zip(storyboard.sections,requests,strict=True),start=1):
        mapped=sum(1 for timeline in timelines for segment in timeline.segments if segment.storyboard_section_id==section.section_id)
        prompt,diagnostic=mapper.prompt_with_diagnostic(request); mapper.validate_prompt(prompt)
        diagnostics.append((f"scene-{index:04d}",section.section_id,mapped,request.video_request.duration_seconds,
            tuple(value.id for value in request.video_request.characters),request.request_id,diagnostic))
    return record,diagnostics


def main():
    parser=argparse.ArgumentParser(description="Validate storyboard-first Kling requests without writes or HTTP.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try: record,diagnostics=build_preflight(args.project_id)
    except FileNotFoundError as error:
        category="video_storyboard_load_failed" if "storyboard.json" in str(error) else "video_timeline_load_failed"
        print("Video request preflight failed."); print(f"Failure category: {category}"); print("External calls: 0"); return 1
    except Exception:
        print("Video request preflight failed."); print("Failure category: video_request_build_failed"); print("External calls: 0"); return 1
    print("Video request preflight passed."); print(f"Project ID: {record.project_id}")
    for scene,section,mapped,duration,characters,reference,diagnostic in diagnostics:
        print(f"Scene: {scene}"); print(f"Storyboard section: {section}"); print(f"Timeline variants mapped: {mapped}")
        print(f"Requested duration: {duration}"); print("Canonical characters: " + ", ".join(characters))
        print(f"Request reference: {reference}"); print(f"Prompt characters before compaction: {diagnostic.before_characters}")
        print(f"Prompt characters after compaction: {diagnostic.after_characters}")
        print(f"Maximum allowed: {KLING_PROMPT_MAX_CHARACTERS}"); print(f"Recommended maximum: {KLING_PROMPT_RECOMMENDED_CHARACTERS}")
        print("Compaction applied: " + ("yes" if diagnostic.compaction_applied else "no")); print("Request contract: valid")
    print("External calls: 0"); return 0


if __name__=="__main__": raise SystemExit(main())
