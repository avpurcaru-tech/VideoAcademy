import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from app.cli.video_add_audio import main as single_cli
from app.cli.video_add_audio_variants import main as variants_cli
from app.media import (AudioProbeResult,AudioVariantCompositionPartialError,AudioVariantVideoComposer,
    AudioVideoComposedArtifact,AudioVideoCompositionRequest,AudioVideoDurationPolicy,
    CompositionDestinationConflictError,CompositionDurationIncompatibleError,CompositionFFmpegError,
    FFmpegAudioVideoComposer,FFprobeAdapter,MediaProbeResult,ProcessResult)


CONTENT=b"composed-mp4"


def video_info(path,duration=30,has_audio=True):
    return MediaProbeResult(local_path=path,duration_seconds=duration,width=1280,height=720,frame_rate=30,
        video_codec="h264",audio_codec="aac" if has_audio else None,has_audio=has_audio,container_format="mov,mp4")


def audio_info(path,duration=20):
    return AudioProbeResult(local_path=path,duration_seconds=duration,audio_codec="mp3",container_format="mp3")


class FakeProbe:
    def __init__(self,video,audio,output): self.video=video; self.audio=audio; self.output=output; self.calls=[]
    def probe_video(self,path):
        self.calls.append(("video",Path(path)))
        return self.video if Path(path)==self.video.local_path else self.output.model_copy(update={"local_path":Path(path)})
    def probe_audio(self,path): self.calls.append(("audio",Path(path))); return self.audio


class FakeRunner:
    def __init__(self,exit_code=0,content=CONTENT): self.exit_code=exit_code; self.content=content; self.calls=[]
    def run(self,args,timeout_seconds=None):
        self.calls.append((list(args),timeout_seconds))
        if self.exit_code==0: Path(args[-1]).write_bytes(self.content)
        return ProcessResult(exit_code=self.exit_code,stdout="",stderr="SECRET raw ffmpeg")


class AudioVideoCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.video=self.root/"video.mp4"; self.audio=self.root/"song.mp3"
        self.video.write_bytes(b"video"); self.audio.write_bytes(b"audio")
    def tearDown(self): self.temp.cleanup()

    def compose(self,video_duration=30,audio_duration=20,policy=AudioVideoDurationPolicy.TRIM_VIDEO_TO_AUDIO,
                runner=None,destination=None,overwrite=False,source_audio=True):
        destination=destination or self.root/"final.mp4"; runner=runner or FakeRunner()
        probe=FakeProbe(video_info(self.video,video_duration,source_audio),audio_info(self.audio,audio_duration),
            video_info(destination,audio_duration,True))
        service=FFmpegAudioVideoComposer(runner,probe,timeout_seconds=900)
        artifact=service.compose(AudioVideoCompositionRequest(video_source=self.video,audio_source=self.audio,
            destination=destination,workspace=self.root/"workspace",duration_policy=policy,overwrite=overwrite))
        return artifact,runner,probe

    def test_trim_maps_generated_audio_discards_source_audio_and_publishes_atomically(self):
        artifact,runner,probe=self.compose(source_audio=True)
        args=runner.calls[0][0]
        self.assertEqual(args.count("ffmpeg"),1); self.assertNotIn("-stream_loop",args)
        self.assertEqual(args[args.index("-map")+1],"0:v:0")
        second=args.index("-map",args.index("-map")+1); self.assertEqual(args[second+1],"1:a:0")
        self.assertNotIn("0:a:0",args); self.assertEqual(args[args.index("-t")+1],"20")
        self.assertEqual(artifact.local_path,self.root/"final.mp4"); self.assertEqual(artifact.byte_size,len(CONTENT))
        self.assertEqual(artifact.sha256,hashlib.sha256(CONTENT).hexdigest())
        self.assertEqual(probe.calls,[("video",self.video),("audio",self.audio),("video",probe.calls[2][1])])
        self.assertFalse(any(self.root.glob("*.part.mp4")))

    def test_trim_rejects_short_video_before_ffmpeg(self):
        runner=FakeRunner(); probe=FakeProbe(video_info(self.video,10),audio_info(self.audio,20),video_info(self.root/"x",20))
        with self.assertRaises(CompositionDurationIncompatibleError): FFmpegAudioVideoComposer(runner,probe).compose(
            AudioVideoCompositionRequest(video_source=self.video,audio_source=self.audio,destination=self.root/"out.mp4",
                workspace=self.root/"work",duration_policy="trim_video_to_audio"))
        self.assertEqual(runner.calls,[])

    def test_extend_loops_only_when_video_is_shorter_and_always_trims_to_audio(self):
        _,short_runner,_=self.compose(10,20,AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO)
        args=short_runner.calls[0][0]; self.assertEqual(args[args.index("-stream_loop")+1],"-1")
        self.assertLess(args.index("-stream_loop"),args.index("-i")); self.assertEqual(args[args.index("-t")+1],"20")
        (self.root/"final.mp4").unlink()
        _,long_runner,_=self.compose(30,20,AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO)
        self.assertNotIn("-stream_loop",long_runner.calls[0][0])

    def test_destination_conflict_and_failure_preserve_existing_destination_and_clean_temp(self):
        destination=self.root/"existing.mp4"; destination.write_bytes(b"original")
        probe=FakeProbe(video_info(self.video),audio_info(self.audio),video_info(destination,20))
        with self.assertRaises(CompositionDestinationConflictError): FFmpegAudioVideoComposer(FakeRunner(),probe).compose(
            AudioVideoCompositionRequest(video_source=self.video,audio_source=self.audio,destination=destination,
                workspace=self.root/"work",duration_policy="extend_video_to_audio"))
        runner=FakeRunner(exit_code=1)
        with self.assertRaises(CompositionFFmpegError): FFmpegAudioVideoComposer(runner,probe).compose(
            AudioVideoCompositionRequest(video_source=self.video,audio_source=self.audio,destination=destination,
                workspace=self.root/"work",duration_policy="extend_video_to_audio",overwrite=True))
        self.assertEqual(destination.read_bytes(),b"original"); self.assertFalse(any(self.root.glob("*.part.mp4")))

    def test_output_profile_is_h264_aac_with_source_resolution_and_rate(self):
        artifact,runner,_=self.compose()
        args=runner.calls[0][0]
        self.assertEqual(args[args.index("-c:v")+1],"libx264"); self.assertEqual(args[args.index("-c:a")+1],"aac")
        self.assertEqual(args[args.index("-pix_fmt")+1],"yuv420p")
        self.assertEqual((artifact.media_info.width,artifact.media_info.height,artifact.media_info.frame_rate),(1280,720,30))
        self.assertTrue(artifact.media_info.has_audio)

    def test_batch_order_names_and_partial_failure_preserves_success(self):
        first=self.root/"one.mp3"; second=self.root/"two.mp3"; first.write_bytes(b"1"); second.write_bytes(b"2")
        composer=Mock()
        successful=AudioVideoComposedArtifact(local_path=self.root/"final-variant-01.mp4",byte_size=1,sha256="a"*64,
            media_info=video_info(self.root/"final-variant-01.mp4",20),video_source_path=self.video,
            audio_source_path=first,duration_policy="extend_video_to_audio")
        composer.compose.side_effect=[successful,RuntimeError("SECRET")]
        batch=AudioVariantVideoComposer(composer)
        with self.assertRaises(AudioVariantCompositionPartialError) as raised:
            batch.compose_variants(self.video,[first,second],self.root/"out",self.root/"work",
                                   AudioVideoDurationPolicy.EXTEND_VIDEO_TO_AUDIO)
        self.assertEqual(raised.exception.completed_count,1); self.assertEqual(raised.exception.failed_variant_index,2)
        requests=[call.args[0] for call in composer.compose.call_args_list]
        self.assertEqual([value.destination.name for value in requests],["final-variant-01.mp4","final-variant-02.mp4"])
        self.assertEqual([value.audio_source for value in requests],[first,second])

    def test_single_cli_output_is_sanitized(self):
        artifact=AudioVideoComposedArtifact(local_path=self.root/"final.mp4",byte_size=10,sha256="a"*64,
            media_info=video_info(self.root/"final.mp4",20),video_source_path=self.video,audio_source_path=self.audio,
            duration_policy="extend_video_to_audio")
        service=Mock(); service.compose.return_value=artifact
        argv=["video_add_audio","--video",str(self.video),"--audio",str(self.audio),"--workspace",str(self.root/"w"),
              "--output",str(self.root/"final.mp4"),"--duration-policy","extend_video_to_audio"]
        with patch("sys.argv",argv),patch("app.cli.video_add_audio.build_composer",return_value=service),patch("builtins.print") as emit:
            self.assertEqual(single_cli(),0)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertIn("Video codec: h264",output); self.assertIn("Audio codec: aac",output)
        for forbidden in ("ffmpeg","-map","probe JSON","SECRET"): self.assertNotIn(forbidden,output)

    def test_variants_cli_reports_safe_partial_metadata(self):
        batch=Mock(); batch.compose_variants.side_effect=AudioVariantCompositionPartialError((),1)
        argv=["video_add_audio_variants","--video",str(self.video),"--audio",str(self.audio),"--workspace",str(self.root/"w"),
              "--output-dir",str(self.root/"out"),"--duration-policy","extend_video_to_audio"]
        with patch("sys.argv",argv),patch("app.cli.video_add_audio_variants.build_composer"),patch(
                "app.cli.video_add_audio_variants.AudioVariantVideoComposer",return_value=batch),patch("builtins.print") as emit:
            self.assertEqual(variants_cli(),1)
        output="\n".join(str(call.args[0]) for call in emit.call_args_list)
        self.assertEqual(output,"Audio variant composition stopped after a partial failure.\nVariants completed: 0\nFailed variant: 1")

    def test_production_probe_adapter_supports_audio_only_mp3(self):
        runner=Mock(); runner.run.return_value=ProcessResult(exit_code=0,stderr="",stdout=json.dumps({
            "streams":[{"codec_type":"audio","codec_name":"mp3"}],
            "format":{"duration":"118.76","format_name":"mp3"}}))
        result=FFprobeAdapter(runner).probe_audio(self.audio)
        self.assertEqual((result.duration_seconds,result.audio_codec,result.container_format),(118.76,"mp3","mp3"))
        self.assertEqual(runner.run.call_count,1)


if __name__=="__main__": unittest.main()
