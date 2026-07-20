import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from app.song import EducationalSongBrief, LyricsPlan, MusicPlan, SongPlanner


class SongInputError(RuntimeError):
    def __init__(self, lines): self.lines=tuple(lines)


def configure_utf8_output() -> None:
    reconfigure=getattr(sys.stdout,"reconfigure",None)
    if reconfigure is not None: reconfigure(encoding="utf-8")


def load_contract(path: Path, contract, label: str):
    if not path.exists(): raise SongInputError((f"{label} file not found: {path}",))
    if not path.is_file(): raise SongInputError((f"{label} path is not a regular file: {path}",))
    try: payload=path.read_text(encoding="utf-8")
    except OSError: raise SongInputError((f"{label} file is unreadable: {path}",)) from None
    try: return contract.model_validate_json(payload)
    except ValidationError as error:
        lines=[f"{label} validation failed:"]
        for detail in error.errors(include_url=False,include_context=False,include_input=False):
            field=".".join(str(part) for part in detail["loc"]) or "root"
            lines.append(f"- {field}: {str(detail['type']).replace('_',' ')}")
        raise SongInputError(lines) from None


def main() -> int:
    configure_utf8_output()
    parser=argparse.ArgumentParser(description="Validate provider-neutral educational song planning contracts.")
    parser.add_argument("--brief",required=True,type=Path); parser.add_argument("--lyrics",required=True,type=Path)
    parser.add_argument("--music",required=True,type=Path); args=parser.parse_args()
    try:
        brief=load_contract(args.brief,EducationalSongBrief,"Song brief")
        lyrics=load_contract(args.lyrics,LyricsPlan,"Lyrics plan")
        music=load_contract(args.music,MusicPlan,"Music plan")
        plan=SongPlanner().plan(brief,lyrics,music)
    except SongInputError as error:
        for line in error.lines: print(line)
        return 1
    except ValidationError as error:
        print("Song production plan validation failed:")
        for detail in error.errors(include_url=False,include_context=False,include_input=False):
            field=".".join(str(part) for part in detail["loc"]) or "root"
            print(f"- {field}: {str(detail['type']).replace('_',' ')}")
        return 1
    print(f"Song ID: {plan.brief.song_id}"); print(f"Topic: {plan.brief.topic}")
    print(f"Language: {plan.brief.language}"); print(f"Target age: {plan.brief.target_age_min}-{plan.brief.target_age_max}")
    print(f"Target duration: {plan.brief.target_duration_seconds}"); print(f"Sections: {len(plan.lyrics.sections)}")
    print(f"Tempo: {plan.music.tempo_bpm}"); print(f"Style: {plan.music.musical_style}"); print(f"Mood: {plan.music.mood}")
    print("Validation: passed"); return 0


if __name__=="__main__": raise SystemExit(main())
