import argparse
from app.project import ProjectRegistry,ProjectNotFoundError
from app.lyrics_alignment import LyricsAlignment,normalize_lexical

def main():
    parser=argparse.ArgumentParser(description="Read-only lyrics alignment preflight.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try: project=ProjectRegistry().load(args.project_id)
    except ProjectNotFoundError: print("Failure category: project_not_found"); print("HTTP calls: 0"); return 1
    paths=sorted(project.music_directory.glob("alignment-variant-*.json"))
    if not paths: print("Failure category: lyrics_alignment_missing"); print("HTTP calls: 0"); return 1
    valid=True
    for path in paths:
        try: value=LyricsAlignment.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception: print(f"Variant: {path.stem.removeprefix('alignment-')}"); print("Alignment status: invalid"); valid=False; continue
        mapped=sum(word.source_line_id is not None for word in value.words)
        coverage=len(value.lines)/max(1,len(value.lines)+len(value.unmatched_lyrics_tokens))
        gaps=max(0,len(value.sections)-1)+(1 if value.words and value.words[0].start_seconds>0 else 0)+(1 if value.words and value.words[-1].end_seconds<value.audio_duration_seconds else 0)
        print(f"Variant: {value.variant_id}"); print(f"Audio duration: {value.audio_duration_seconds}")
        print(f"Alignment source: {value.source}"); print(f"Aligned words: {len(value.words)}")
        print(f"Mapped words: {mapped}"); print(f"Unmatched words: {len(value.unmatched_provider_tokens)}")
        print(f"Aligned lines: {len(value.lines)}"); print(f"Mapped line coverage: {coverage:.3f}")
        print(f"First lyric timestamp: {value.words[0].start_seconds if value.words else 'unavailable'}")
        print(f"Last lyric timestamp: {value.words[-1].end_seconds if value.words else 'unavailable'}")
        print(f"Instrumental gaps: {gaps}"); print(f"Alignment status: {value.status.value}")
        valid=valid and value.status.value in ("valid","valid_with_warnings","instrumental")
    print("HTTP calls: 0"); return 0 if valid else 1

if __name__=="__main__": raise SystemExit(main())
