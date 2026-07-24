import hashlib,io,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from app.cli.project_lyrics_alignment_preflight import main as alignment_preflight
from app.cli.project_sync_plan_preflight import main as sync_preflight
from app.composition.music_timeline import MusicTimelineComposer,MusicTimelineCompositionRequest,StoryboardVideoClip
from app.lyrics_alignment import *
from app.project import ProjectGenerationService,ProjectRegistry
from app.song import LyricsLine,LyricsPlan,LyricsSection
from app.sync_planning import AudioSynchronizedVideoPlanner,SynchronizedEditPlanStore


def source_lyrics():
    return LyricsPlan(song_id="project-008",title="Culorile",language="ro",sections=(
        LyricsSection(section_id="verse",kind="verse",order=0,lines=(LyricsLine(line_id="v1",text="Mărul roșu, câinele și țânțarul învață împreună"),)),
        LyricsSection(section_id="chorus-a",kind="chorus",order=1,lines=(LyricsLine(line_id="c1",text="Roșu galben verde albastru"),)),
        LyricsSection(section_id="bridge",kind="bridge",order=2,lines=(LyricsLine(line_id="missing",text="Această linie nu este cântată deloc"),)),
        LyricsSection(section_id="chorus-b",kind="chorus",order=3,lines=(LyricsLine(line_id="c2",text="Roșu galben verde albastru"),))))


def provider_words(offset=0,warning=False):
    groups=((2,"Mărul roșuuu câinele și țânțarul învață împreună"),(7,"Roșu galben verde albastru"),(13,"Roșu galben verde albastru"))
    values=[]
    for start,text in groups:
        for index,word in enumerate(text.split()): values.append(ProviderAlignedWord(text=word,start_seconds=offset+start+index*.4,end_seconds=offset+start+index*.4+.3))
    if warning:
        values.extend(ProviderAlignedWord(text=f"extra{i}",start_seconds=offset+18+i*.2,end_seconds=offset+18.1+i*.2) for i in range(7))
    return tuple(values)


class Fixture008:
    def __init__(self,root):
        self.root=Path(root)/"008"; self.music=self.root/"music"; self.final=self.root/"final"; self.clips=self.root/"clips"
        for path in (self.music,self.final,self.clips): path.mkdir(parents=True,exist_ok=True)
        self.lyrics=source_lyrics(); self.storyboard=SimpleNamespace(storyboard_id="project-008",
            sections=tuple(SimpleNamespace(section_id=value) for value in ("red","yellow","green","blue")))
        self.alignments=[]; self.timelines=[]; self.edls=[]
        for index,(duration,warning) in enumerate(((20.0,False),(22.0,True)),1):
            variant=f"variant-{index:02d}"; audio=f"audio-{index:02d}"; data=f"mp3-{index}".encode(); mp3=self.music/f"{variant}.mp3"; mp3.write_bytes(data)
            alignment=LyricsAlignmentNormalizer().build(variant_id=variant,audio_artifact_id=audio,
                audio_sha256=hashlib.sha256(data).hexdigest(),provider_task_id="task-008",provider_audio_id=audio,
                audio_duration_seconds=duration,language="ro",source="suno_timestamped_lyrics",
                provider_words=provider_words(0,warning),lyrics=self.lyrics)
            LyricsAlignmentStore(self.music).save(alignment); self.alignments.append(alignment)
            timeline=build_aligned_music_timeline(self.storyboard,alignment); (self.music/f"timeline-{variant}.json").write_text(timeline.model_dump_json(indent=2),encoding="utf-8"); self.timelines.append(timeline)
        shots=tuple(SimpleNamespace(shot_id=f"shot-{name}",source_storyboard_section_id=name) for name in ("red","yellow","green","blue"))
        self.coverage=SimpleNamespace(unique_shots=shots,provider_capabilities=SimpleNamespace(selected_clip_duration=10))
        for alignment,timeline in zip(self.alignments,self.timelines):
            edl=AudioSynchronizedVideoPlanner().plan(alignment,timeline,self.coverage,("roșu","galben","verde","albastru"))
            SynchronizedEditPlanStore(self.music).save(edl); self.edls.append(edl)
        self.clip_paths={}
        for shot in shots:
            path=self.clips/f"{shot.shot_id}.mp4"; path.write_bytes(b"shared-clip"); self.clip_paths[shot.shot_id]=path
        registry=ProjectRegistry(self.root.parent); record=ProjectGenerationService.create_planned(registry,"008",self.root,"project-008")
        registry.update(record.model_copy(update={"music_task_id":"task-008"})); self.registry=registry


class Project008ValidationTests(unittest.TestCase):
    def setUp(self): self.temporary=tempfile.TemporaryDirectory(); self.fx=Fixture008(self.temporary.name)
    def tearDown(self): self.temporary.cleanup()

    def test_project_008_builds_independent_alignment_per_audio_variant(self):
        first,second=self.fx.alignments
        self.assertNotEqual(first.audio_sha256,second.audio_sha256); self.assertNotEqual(first.provider_audio_id,second.provider_audio_id)
        self.assertEqual("task-008",first.provider_task_id); self.assertNotEqual(first.audio_duration_seconds,second.audio_duration_seconds)
        self.assertTrue((self.fx.music/"alignment-variant-01.json").is_file()); self.assertTrue((self.fx.music/"alignment-variant-02.json").is_file())

    def test_project_008_repeated_chorus_maps_sequentially(self):
        lines={line.source_lyrics_line_id:line for line in self.fx.alignments[0].lines}
        self.assertLess(lines["c1"].start_seconds,lines["c2"].start_seconds)
        self.assertLessEqual(lines["c1"].end_seconds,lines["c2"].start_seconds)

    def test_project_008_unmapped_line_receives_no_synthetic_timing(self):
        value=self.fx.alignments[0]
        self.assertIn("missing",value.unmatched_lyrics_line_ids)
        self.assertNotIn("missing",{line.source_lyrics_line_id for line in value.lines})
        self.assertLess(value.mapping_confidence,1)

    def test_project_008_builds_instrumental_intro_break_and_outro(self):
        sections=self.fx.alignments[0].sections; kinds={value.section_type for value in sections}
        self.assertTrue({"instrumental_intro","instrumental_break","instrumental_outro"}<=kinds)
        ordered=sorted(sections,key=lambda value:value.start_seconds)
        self.assertTrue(all(a.end_seconds<=b.start_seconds for a,b in zip(ordered,ordered[1:])))
        self.assertTrue(all(0<=value.start_seconds<value.end_seconds<=20 for value in sections))

    def test_project_008_builds_independent_edl_per_variant(self):
        first,second=self.fx.edls; self.assertNotEqual(first.variant_id,second.variant_id)
        self.assertNotEqual(first.audio_duration_seconds,second.audio_duration_seconds)
        self.assertNotEqual([(x.destination_start,x.destination_end) for x in first.decisions],[(x.destination_start,x.destination_end) for x in second.decisions])
        self.assertEqual({x.source_scene_id for x in first.decisions},{x.source_scene_id for x in second.decisions})

    def test_project_008_composition_consumes_exact_edl_trims(self):
        renderer=Mock(); mux=Mock(); output=self.fx.final/"variant-01.mp4"; output.write_bytes(b"final")
        mux.compose.return_value=SimpleNamespace(local_path=output,byte_size=5,sha256=hashlib.sha256(b"final").hexdigest())
        clips=tuple(StoryboardVideoClip(storyboard_section_id=name,scene_id=f"shot-{name}",local_path=self.fx.clip_paths[f"shot-{name}"]) for name in ("red","yellow","green","blue"))
        request=MusicTimelineCompositionRequest(composition_id="composition-variant-01",edit_plan=self.fx.edls[0],video_clips=clips,
            music_source=self.fx.music/"variant-01.mp3",destination=output,workspace=self.fx.root/"workspace",overwrite=True)
        MusicTimelineComposer(renderer,mux).compose(request)
        semantic=renderer.render.call_args.args[0]
        self.assertEqual([(x.source_start,x.source_end) for x in self.fx.edls[0].decisions],[(x.trim_start_seconds,x.trim_end_seconds) for x in semantic.scenes])

    def test_project_008_mp3_sha_change_invalidates_only_dependent_artifacts(self):
        store=LyricsAlignmentStore(self.fx.music); old=self.fx.alignments[0]
        self.assertIsNone(store.load_valid("variant-01","f"*64)); self.assertEqual(self.fx.alignments[1],store.load_valid("variant-02",self.fx.alignments[1].audio_sha256))
        self.assertNotEqual("f"*64,old.audio_sha256)

    def test_project_008_sha_change_does_not_invalidate_shared_clip_pool(self):
        before={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.fx.clip_paths.values()}
        LyricsAlignmentStore(self.fx.music).load_valid("variant-01","f"*64)
        self.assertEqual(before,{path:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.fx.clip_paths.values()})

    def test_project_008_sha_change_does_not_invalidate_other_audio_variant(self):
        before=(self.fx.music/"alignment-variant-02.json").read_bytes(),(self.fx.music/"sync-plan-variant-02.json").read_bytes()
        LyricsAlignmentStore(self.fx.music).load_valid("variant-01","f"*64)
        self.assertEqual(before,((self.fx.music/"alignment-variant-02.json").read_bytes(),(self.fx.music/"sync-plan-variant-02.json").read_bytes()))

    def _snapshot(self):
        return {path:(hashlib.sha256(path.read_bytes()).hexdigest(),path.stat().st_mtime_ns) for path in self.fx.root.rglob("*") if path.is_file()}

    def test_lyrics_alignment_preflight_is_strictly_read_only(self):
        before=self._snapshot(); output=io.StringIO()
        with patch("sys.argv",["preflight","--project-id","008"]),patch("app.cli.project_lyrics_alignment_preflight.ProjectRegistry",return_value=self.fx.registry),redirect_stdout(output):
            alignment_preflight()
        self.assertEqual(before,self._snapshot()); self.assertIn("HTTP calls: 0",output.getvalue()); self.assertIn("Unmatched lines:",output.getvalue())

    def test_sync_plan_preflight_is_strictly_read_only(self):
        before=self._snapshot(); output=io.StringIO()
        with patch("sys.argv",["preflight","--project-id","008"]),patch("app.cli.project_sync_plan_preflight.ProjectRegistry",return_value=self.fx.registry),redirect_stdout(output): sync_preflight()
        self.assertEqual(before,self._snapshot()); self.assertIn("EDL path:",output.getvalue())

    def test_sync_plan_preflight_reports_zero_http_and_zero_ffmpeg_calls(self):
        output=io.StringIO()
        with patch("sys.argv",["preflight","--project-id","008"]),patch("app.cli.project_sync_plan_preflight.ProjectRegistry",return_value=self.fx.registry),redirect_stdout(output): sync_preflight()
        self.assertIn("HTTP calls: 0",output.getvalue()); self.assertIn("FFmpeg calls: 0",output.getvalue())

    def test_instrumental_variant_is_accepted_without_aligned_words(self):
        value=LyricsAlignmentNormalizer().build(variant_id="instrumental",audio_artifact_id="audio-i",audio_sha256="a"*64,
            provider_task_id="task-008",provider_audio_id="audio-i",audio_duration_seconds=10,language="ro",source="suno",
            provider_words=(),lyrics=self.fx.lyrics,instrumental=True)
        self.assertEqual("instrumental",value.status)

    def test_review_required_alignment_does_not_trigger_whisperx_automatically(self):
        sparse=provider_words()[:3]; fallback=Mock()
        value=LyricsAlignmentNormalizer().build(variant_id="review",audio_artifact_id="audio-r",audio_sha256="a"*64,
            provider_task_id="task",provider_audio_id="audio-r",audio_duration_seconds=20,language="ro",source="suno",
            provider_words=sparse,lyrics=self.fx.lyrics)
        self.assertEqual("review_required",value.status); fallback.assert_not_called()


if __name__=="__main__": unittest.main()
