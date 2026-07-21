import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from app.composition import (MusicTimelineComposer,MusicTimelineCompositionRequest,
    StoryboardVideoClip)
from app.media import AudioVideoDurationPolicy
from app.music_timeline import MusicTimeline,MusicTimelineSegment


IDS=("section-a","section-b","section-c")


def timeline():
    bounds=((0,8),(8,20),(20,30))
    return MusicTimeline(timeline_id="story-music",storyboard_id="story",music_duration_seconds=30,
        segments=tuple(MusicTimelineSegment(start_seconds=start,end_seconds=end,
            storyboard_section_id=section_id,estimated_confidence=.9)
            for section_id,(start,end) in zip(IDS,bounds)))


class FakeVideoRenderer:
    def __init__(self): self.calls=[]
    def render(self,value):
        self.calls.append(value); value.output.destination.parent.mkdir(parents=True,exist_ok=True)
        value.output.destination.write_bytes(b"aligned-video")
        return SimpleNamespace(local_path=value.output.destination)


class AtomicFakeComposer:
    def __init__(self): self.calls=[]
    def compose(self,request):
        self.calls.append(request); request.destination.parent.mkdir(parents=True,exist_ok=True)
        part=request.destination.with_suffix(".part")
        part.write_bytes(b"final-composition"); os.replace(part,request.destination)
        raw=request.destination.read_bytes()
        return SimpleNamespace(local_path=request.destination,byte_size=len(raw),sha256=hashlib.sha256(raw).hexdigest())


class MusicTimelineCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.music=self.root/"music.mp3"; self.music.write_bytes(b"original-music-bytes")
        self.clips=[]
        for section_id in IDS:
            path=self.root/f"{section_id}.mp4"; path.write_bytes(section_id.encode())
            self.clips.append(StoryboardVideoClip(storyboard_section_id=section_id,local_path=path))
        self.video=FakeVideoRenderer(); self.mux=AtomicFakeComposer(); self.composer=MusicTimelineComposer(self.video,self.mux)
    def tearDown(self): self.temp.cleanup()

    def request(self,timing=timeline(),clips=None,**updates):
        values=dict(composition_id="final-story",timeline=timing,
            video_clips=tuple(clips or self.clips),music_source=self.music,
            destination=self.root/"final.mp4",workspace=self.root/"workspace")
        values.update(updates); return MusicTimelineCompositionRequest(**values)

    def test_timeline_controls_clip_order_and_segment_durations(self):
        shuffled=(self.clips[2],self.clips[0],self.clips[1])
        result=self.composer.compose(self.request(clips=shuffled))
        semantic=self.video.calls[0]
        self.assertEqual([scene.scene_id for scene in semantic.scenes],list(IDS))
        self.assertEqual([scene.trim_end_seconds for scene in semantic.scenes],[8,12,10])
        self.assertTrue(result.used_music_timeline)
        self.assertEqual(self.mux.calls[0].video_source,self.root/"workspace/timeline-aligned-video.mp4")

    def test_original_music_is_passed_unchanged_without_stretching(self):
        before=self.music.read_bytes(); self.composer.compose(self.request())
        mux_request=self.mux.calls[0]
        self.assertEqual(mux_request.audio_source,self.music)
        self.assertEqual(mux_request.duration_policy,AudioVideoDurationPolicy.TRIM_VIDEO_TO_AUDIO)
        self.assertEqual(self.music.read_bytes(),before)

    def test_backward_compatible_path_uses_preassembled_video_directly(self):
        request=self.request(timing=None,clips=(self.clips[0],))
        result=self.composer.compose(request)
        self.assertFalse(result.used_music_timeline); self.assertEqual(self.video.calls,[])
        self.assertEqual(self.mux.calls[0].video_source,self.clips[0].local_path)

    def test_final_publication_is_atomic_and_leaves_no_partial_file(self):
        with patch("tests.test_music_timeline_composition.os.replace",wraps=os.replace) as replace:
            result=self.composer.compose(self.request())
        self.assertTrue(result.local_path.is_file()); self.assertFalse(result.local_path.with_suffix(".part").exists())
        replace.assert_called_once()

    def test_resume_reuses_final_output_without_render_mux_or_provider(self):
        destination=self.root/"final.mp4"; destination.write_bytes(b"already-complete")
        with patch("app.providers.kling_provider.KlingProvider") as video_provider, \
             patch("app.music.engine.MusicEngine.submit") as music_provider:
            result=self.composer.compose(self.request(resume=True))
        self.assertTrue(result.resumed); self.assertEqual(self.video.calls,[]); self.assertEqual(self.mux.calls,[])
        video_provider.assert_not_called(); music_provider.assert_not_called()

    def test_resume_after_video_render_reuses_aligned_video(self):
        aligned=self.root/"workspace/timeline-aligned-video.mp4"; aligned.parent.mkdir(parents=True); aligned.write_bytes(b"aligned")
        result=self.composer.compose(self.request(resume=True))
        self.assertFalse(result.resumed); self.assertEqual(self.video.calls,[]); self.assertEqual(len(self.mux.calls),1)


if __name__=="__main__": unittest.main()
