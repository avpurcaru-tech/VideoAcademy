import argparse
from app.project import ProjectRegistry,ProjectNotFoundError
from app.lyrics_alignment import LyricsAlignment
from app.sync_planning import SynchronizedEditPlan

def main():
    parser=argparse.ArgumentParser(description="Read-only synchronized edit plan preflight.")
    parser.add_argument("--project-id",required=True); args=parser.parse_args()
    try: project=ProjectRegistry().load(args.project_id)
    except ProjectNotFoundError: print("Failure category: project_not_found"); print("HTTP calls: 0"); print("FFmpeg calls: 0"); return 1
    paths=sorted(project.music_directory.glob("sync-plan-variant-*.json"))
    if not paths: print("Failure category: synchronized_shot_plan_failed"); print("HTTP calls: 0"); print("FFmpeg calls: 0"); return 1
    valid=True
    for path in paths:
        plan=SynchronizedEditPlan.model_validate_json(path.read_text(encoding="utf-8"))
        alignment=LyricsAlignment.model_validate_json((project.music_directory/f"alignment-{plan.variant_id}.json").read_text(encoding="utf-8"))
        lines={line.line_id:line for line in alignment.lines}; words={word.word_id:word for word in alignment.words}
        print(f"Variant: {plan.variant_id}")
        print(f"Alignment path: {project.music_directory/f'alignment-{plan.variant_id}.json'}")
        print(f"EDL path: {path}")
        print("Sync plan status: "+("valid" if plan.synchronization_valid else "invalid"))
        for decision in plan.decisions:
            lyric=" | ".join(lines[value].text for value in decision.alignment_line_ids if value in lines) or "instrumental"
            important=", ".join(f"{words[value].text}@{words[value].start_seconds:.2f}" for value in decision.alignment_word_ids if value in words)
            print(f"Time range: {decision.destination_start:.3f}-{decision.destination_end:.3f}")
            print(f"Lyrics line: {lyric}"); print(f"Important words and timestamps: {important or 'none'}")
            print(f"Storyboard section: {decision.storyboard_section_id}"); print(f"Planned visual: {decision.source_scene_id}")
            usage=next((value for value in plan.shot_usages if value.shot_id==decision.source_scene_id and value.required_visual_onset<=decision.destination_start+.001),None)
            print(f"Required visual onset: {usage.required_visual_onset if usage else decision.destination_start}")
            print(f"Source generated clip: {decision.source_scene_id}")
            print(f"Source in/out: {decision.source_start:.3f}-{decision.source_end:.3f}")
            print("Synchronization valid: "+("yes" if plan.synchronization_valid else "no"))
        valid=valid and plan.synchronization_valid
    print("HTTP calls: 0"); print("FFmpeg calls: 0"); return 0 if valid else 1

if __name__=="__main__": raise SystemExit(main())
