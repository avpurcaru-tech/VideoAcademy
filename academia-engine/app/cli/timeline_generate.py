import argparse
from pathlib import Path

from app.cli.song_validate import configure_utf8_output
from app.config.environment import load_application_environment
from app.music_timeline import (MusicTimelineGenerationError, MusicTimelineGenerationService,
    MusicTimelineGeneratorRegistry, MusicTimelinePersistenceError, MusicTimelineRepository)
from app.song import LyricsPlan
from app.storyboard import CreativeStoryboard


def main() -> int:
    configure_utf8_output(); load_application_environment()
    parser=argparse.ArgumentParser(description="Generate provider-neutral storyboard-to-music timing metadata.")
    parser.add_argument("--storyboard",required=True,type=Path); parser.add_argument("--lyrics",required=True,type=Path)
    parser.add_argument("--music-duration",required=True,type=float); parser.add_argument("--confirm",action="store_true")
    parser.add_argument("--overwrite",action="store_true")
    parser.add_argument("--runtime-root",type=Path,default=Path(".runtime")/"music-timelines")
    args=parser.parse_args()
    try:
        storyboard=CreativeStoryboard.model_validate_json(args.storyboard.read_text(encoding="utf-8"))
        lyrics=LyricsPlan.model_validate_json(args.lyrics.read_text(encoding="utf-8"))
    except Exception: print("Timeline input validation failed."); return 1
    if not args.confirm:
        print("OpenAI music timeline generation may consume credits.")
        print("No timeline was generated. Use --confirm to proceed."); return 2
    try:
        generator=MusicTimelineGeneratorRegistry().resolve("openai")
        timeline=MusicTimelineGenerationService(generator).generate(storyboard,lyrics,args.music_duration)
        destination=MusicTimelineRepository(args.runtime_root).save(timeline,overwrite=args.overwrite)
    except (MusicTimelineGenerationError,MusicTimelinePersistenceError,Exception):
        print("Music timeline generation failed at a safe provider-neutral boundary."); return 1
    print(f"Timeline ID: {timeline.timeline_id}"); print(f"Segments: {len(timeline.segments)}")
    print(f"Saved path: {destination}"); return 0


if __name__=="__main__": raise SystemExit(main())
