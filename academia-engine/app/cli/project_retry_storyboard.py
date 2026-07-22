import argparse

from app.cli.project_generate import _semantic_song_inputs
from app.cli.project_storyboard_preflight import _load
from app.characters import CharacterRegistry
from app.project import CreativeProjectGenerationService,ProjectGenerationService,ProjectRegistry
from app.series import SeriesRegistry
from app.storyboard import EpisodeService,StoryboardGenerationService,StoryboardGeneratorRegistry,StoryboardRepository


def retry_storyboard(project_id,registry=None,generator=None,repository=None):
    registry=registry or ProjectRegistry(); record=registry.load(project_id); brief=_load(record)
    series=SeriesRegistry(); characters=CharacterRegistry()
    service=StoryboardGenerationService(generator or StoryboardGeneratorRegistry().resolve("openai"),series,characters)
    storyboard=service.generate(brief); repository=repository or StoryboardRepository()
    try: repository.save(storyboard)
    except Exception:
        if repository.load(storyboard.storyboard_id)!=storyboard: raise
    episode=EpisodeService(character_registry=characters,series_registry=series).resolve(storyboard)
    song,music=_semantic_song_inputs(episode,project_id)
    ProjectGenerationService(None,registry)._persist_inputs(record,episode,song,music)
    return storyboard

def main():
    parser=argparse.ArgumentParser(description="Retry only the failed storyboard checkpoint.")
    parser.add_argument("--project-id",required=True); parser.add_argument("--confirm",action="store_true"); args=parser.parse_args()
    if not args.confirm: print("Storyboard retry may consume OpenAI credits. Use --confirm to proceed."); return 2
    registry=ProjectRegistry()
    try:
        retry_storyboard(args.project_id,registry)
    except Exception as error:
        try: CreativeProjectGenerationService(None,None,registry)._persist_early_failure(args.project_id,error)
        except Exception: pass
        print("Storyboard retry failed at a safe boundary."); return 1
    print("Storyboard retry completed and persisted."); print("No video or music provider was called.")
    print("Use project_resume to continue downstream generation."); return 0


if __name__=="__main__": raise SystemExit(main())
