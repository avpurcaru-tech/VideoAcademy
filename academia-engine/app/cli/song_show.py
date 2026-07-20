import argparse
from pathlib import Path

from app.song import LyricsPlan, resolve_lyrics
from .song_validate import SongInputError, configure_utf8_output, load_contract


def main() -> int:
    configure_utf8_output()
    parser=argparse.ArgumentParser(description="Show user-visible lyrics in deterministic structural order.")
    parser.add_argument("--lyrics",required=True,type=Path); args=parser.parse_args()
    try: lyrics=resolve_lyrics(load_contract(args.lyrics,LyricsPlan,"Lyrics plan"))
    except SongInputError as error:
        for line in error.lines: print(line)
        return 1
    print(f"Title: {lyrics.title}"); print(f"Language: {lyrics.language}")
    for section in lyrics.sections:
        print(""); print(f"{section.kind.value.title()}:")
        for line in section.lines: print(line.text)
    return 0


if __name__=="__main__": raise SystemExit(main())
