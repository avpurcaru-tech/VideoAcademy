import argparse
from pathlib import Path

from app.song import (EducationalSongBrief, LyricsGenerationError, LyricsGenerationService,
                      LyricsGeneratorRegistry, LyricsGeneratorRegistryError, LyricsPersistenceError,
                      persist_lyrics_atomic, resolve_lyrics)
from .song_validate import SongInputError, configure_utf8_output, load_contract


def main() -> int:
    configure_utf8_output()
    parser=argparse.ArgumentParser(description="Generate lyrics locally through a provider-neutral generator abstraction.")
    parser.add_argument("--brief",required=True,type=Path); parser.add_argument("--generator",default="deterministic")
    parser.add_argument("--output",required=True,type=Path); parser.add_argument("--overwrite",action="store_true")
    parser.add_argument("--show",action="store_true",help="print the generated user-visible lyrics")
    parser.add_argument("--confirm",action="store_true",help="authorize external AI usage that may incur costs")
    args=parser.parse_args()
    try:
        brief=load_contract(args.brief,EducationalSongBrief,"Song brief")
        if args.generator=="openai" and not args.confirm:
            print("External AI lyrics generation may incur API costs. Use --confirm to continue.")
            return 2
        generator=LyricsGeneratorRegistry().resolve(args.generator)
        lyrics=LyricsGenerationService(generator).generate(brief)
        destination=persist_lyrics_atomic(lyrics,args.output,overwrite=args.overwrite)
    except SongInputError as error:
        for line in error.lines: print(line)
        return 1
    except LyricsGeneratorRegistryError:
        print("Lyrics generator is unsupported."); return 1
    except LyricsGenerationError:
        print("Lyrics generation failed at a safe provider-neutral boundary."); return 1
    except LyricsPersistenceError:
        print("Lyrics output could not be persisted safely."); return 1
    print(f"Song ID: {lyrics.song_id}"); print(f"Title: {lyrics.title}"); print(f"Language: {lyrics.language}")
    print(f"Sections: {len(lyrics.sections)}"); print(f"Saved path: {destination}")
    if args.show:
        for section in resolve_lyrics(lyrics).sections:
            print(""); print(f"{section.kind.value.title()}:")
            for line in section.lines: print(line.text)
    return 0


if __name__=="__main__": raise SystemExit(main())
