import argparse,json

from app.characters import CharacterRegistry
from app.creative import EducationalCreativeBrief
from app.project import CreativeProjectGenerationService,ProjectRegistry
from app.providers.openai_storyboard_provider import OpenAIStoryboardGenerator,_input
from app.series import SeriesRegistry


def _load(record):
    path=record.lyrics_path.parent.parent/"input"/"creative-brief.json"
    if not path.is_file(): raise RuntimeError("non-resumable-input-missing")
    payload=json.loads(path.read_text(encoding="utf-8")); payload.pop("resolved_character_ids",None)
    return EducationalCreativeBrief.model_validate(payload)


def main():
    parser=argparse.ArgumentParser(description="Validate durable storyboard inputs without making external calls.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try:
        record=ProjectRegistry().load(args.project_id); brief=_load(record)
        bible=SeriesRegistry().load(brief.series_id) if brief.series_id else None
        profiles=CharacterRegistry().require_many(bible.resolved_character_ids) if bible else ()
        generator=OpenAIStoryboardGenerator()
        _input(brief,bible,profiles)
    except Exception as error:
        if str(error)=="non-resumable-input-missing": category="non-resumable-input-missing"
        else:
            try: category=CreativeProjectGenerationService._storyboard_failure(error)[0]
            except Exception: category="storyboard_preflight_failed"
        print("Storyboard preflight failed."); print(f"Failure category: {category}"); print("External calls: 0"); return 1
    print("Storyboard preflight passed."); print(f"Project ID: {record.project_id}")
    print(f"Series ID: {brief.series_id or 'none'}"); print("Required characters:")
    for profile in profiles: print(f"- {profile.character_id}: ready")
    print("External calls: 0"); return 0


if __name__=="__main__": raise SystemExit(main())
