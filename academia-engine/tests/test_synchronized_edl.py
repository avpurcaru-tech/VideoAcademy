import unittest
from types import SimpleNamespace
from app.sync_planning import *
from app.lyrics_alignment import AlignedLyricsLine,AlignedLyricsWord
from app.music_timeline import MusicTimeline,MusicTimelineSegment

class SynchronizedEdlTests(unittest.TestCase):
    def test_keyword_lead_time_and_clip_cuts_without_stretching(self):
        word=AlignedLyricsWord(word_id="w1",text="roșie",normalized_text="roșie",start_seconds=2,end_seconds=2.5,source_line_id="l1")
        line=AlignedLyricsLine(line_id="l1",source_lyrics_line_id="source",text="mingea roșie",normalized_text="mingea roșie",
            start_seconds=1,end_seconds=3,word_ids=("w1",),section_type="verse")
        alignment=SimpleNamespace(variant_id="variant-01",alignment_id="a1",audio_duration_seconds=12,words=(word,),lines=(line,))
        timeline=MusicTimeline(timeline_id="timeline",storyboard_id="story",music_duration_seconds=12,
            segments=(MusicTimelineSegment(start_seconds=0,end_seconds=12,storyboard_section_id="red",estimated_confidence=1),))
        coverage=SimpleNamespace(unique_shots=(SimpleNamespace(shot_id="shot-red",source_storyboard_section_id="red"),),
            provider_capabilities=SimpleNamespace(selected_clip_duration=10))
        plan=AudioSynchronizedVideoPlanner().plan(alignment,timeline,coverage,("roșie",))
        self.assertEqual(2,len(plan.decisions)); self.assertAlmostEqual(1.7,plan.shot_usages[0].visual_requirements[0].required_visual_onset)
        for decision in plan.decisions:
            self.assertAlmostEqual(decision.destination_end-decision.destination_start,decision.source_end-decision.source_start)

    def test_contract_rejects_stretching(self):
        with self.assertRaises(ValueError): EditDecision(destination_start=0,destination_end=2,source_scene_id="x",
            source_start=0,source_end=1,storyboard_section_id="red")

if __name__=="__main__": unittest.main()
