import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.providers import (KlingImageToVideoProvider,KlingProviderCredentialsMissingError,
    KlingProviderRegistry,KlingProviderRegistryError)
from app.project import ProjectRecord,ProjectRegistry,ProjectResumeService,ProjectStatus
from app.storyboard import DeterministicStoryboardGenerator
from tests.test_creative_storyboard import brief
from datetime import datetime,timezone


class ImageProviderRuntimeWiringTests(unittest.TestCase):
    def test_authoritative_factory_resolves_image_adapter_under_exact_key(self):
        _configuration,runtime=KlingProviderRegistry().construct_runtime("kling_image_to_video",
            {"KLING_API_KEY":"test","KLING_BASE_URL":"https://api.example.test"})
        self.assertEqual("kling_image_to_video",runtime.provider_key)
        self.assertIsInstance(runtime.provider,KlingImageToVideoProvider)

    def test_known_adapter_missing_key_is_credentials_not_unavailable(self):
        with self.assertRaises(KlingProviderCredentialsMissingError):
            KlingProviderRegistry().construct_runtime("kling_image_to_video",{})

    def test_unknown_key_alone_is_provider_unavailable(self):
        with self.assertRaises(KlingProviderRegistryError): KlingProviderRegistry().construct_runtime("unknown",{})

    def test_persisted_provider_survives_resume_without_text_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/"project"; registry=ProjectRegistry(root.parent); now=datetime.now(timezone.utc)
            record=ProjectRecord(project_id="project",episode_id="story",status=ProjectStatus.FAILED,
                orchestration_version="storyboard_first",video_production_id="project-video",
                video_provider="kling_image_to_video",lyrics_path=root/"lyrics"/"lyrics.json",
                music_directory=root/"music",video_directory=root/"video",final_directory=root/"final",
                created_at=now,updated_at=now)
            registry.create(record); storyboard=DeterministicStoryboardGenerator().generate_storyboard(brief())
            path=root/"input"/"storyboard.json"; path.parent.mkdir(parents=True); path.write_text(storyboard.model_dump_json(),encoding="utf-8")
            generation=Mock(); generation._run_storyboard.return_value=record
            ProjectResumeService(generation,registry).resume("project",Mock(),Mock(),video_provider="kling")
            self.assertEqual("kling_image_to_video",generation._run_storyboard.call_args.args[4])


if __name__=="__main__": unittest.main()
