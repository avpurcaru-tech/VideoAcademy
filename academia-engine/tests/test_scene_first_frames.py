import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock

from app.scene_first_frames import *
from app.visual_references import PublishedVisualReference,VisualReferencePublicationRegistry


def plan(**updates):
    values=dict(first_frame_id="production-shot-0001-first-frame",shot_id="shot-0001",
        source_storyboard_section_id="colors-red",recurring_character_ids=("luca","max"),
        canonical_reference_sha256=("a"*64,"b"*64),background="Sunny park with a broad path",
        required_objects=("red ball","park bench"),character_positions="Max beside the ball; Luca behind Max",
        camera_framing="wide establishing",visual_style="stylized 3D preschool animation",width=1920,height=1080)
    values.update(updates); return SceneFirstFramePlan(**values)


class SceneFirstFrameTests(unittest.TestCase):
    def test_plan_contains_context_identity_inputs_objects_and_camera(self):
        value=plan(); prompt=value.prompt()
        self.assertIn("Sunny park",prompt); self.assertIn("red ball",prompt)
        self.assertIn("wide establishing",prompt); self.assertIn("supplied canonical",prompt)
        self.assertIn("No text",prompt)

    def test_exact_cast_and_aspect_ratio_are_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"frame.png"; path.write_bytes(b"opaque contextual frame")
            generator=Mock(); generator.generate.return_value=GeneratedSceneFirstFrame(local_path=path,
                content_type="image/png",width=1920,height=1080,character_ids=("luca",))
            workflow=SceneFirstFrameWorkflow(generator,SceneFirstFrameStore(Path(root)/"records"))
            with self.assertRaises(SceneFirstFrameCastMismatch): workflow.prepare(plan(),())
            generator.generate.return_value=GeneratedSceneFirstFrame(local_path=path,content_type="image/png",
                width=1024,height=1024,character_ids=("luca","max"))
            with self.assertRaises(SceneFirstFrameAspectRatioInvalid): workflow.prepare(plan(),())

    def test_transparent_and_character_sheet_outputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"frame.png"; path.write_bytes(b"frame")
            for changes in ({"opaque":False},{"character_sheet":True}):
                generator=Mock(); generator.generate.return_value=GeneratedSceneFirstFrame(local_path=path,
                    content_type="image/png",width=1920,height=1080,character_ids=("luca","max"),**changes)
                with self.assertRaises(SceneFirstFrameGenerationFailed):
                    SceneFirstFrameWorkflow(generator,SceneFirstFrameStore(Path(root)/str(len(changes)))).prepare(plan(),())

    def test_generation_publication_and_resume_are_each_reused(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); path=root/"frame.png"; path.write_bytes(b"contextual frame")
            generator=Mock(); generator.generate.return_value=GeneratedSceneFirstFrame(local_path=path,
                content_type="image/png",width=1920,height=1080,character_ids=("luca","max"))
            publisher=Mock(); digest=__import__("hashlib").sha256(path.read_bytes()).hexdigest()
            publisher.publish.return_value=PublishedVisualReference(sha256=digest,https_url="https://assets.example/shot.png")
            workflow=SceneFirstFrameWorkflow(generator,SceneFirstFrameStore(root/"records"),
                VisualReferencePublicationRegistry(root/"publications.json"),publisher)
            first=workflow.prepare(plan(),("luca-ref","max-ref")); second=workflow.prepare(plan(),("ignored",))
            self.assertEqual(first,second); self.assertEqual(first.generation_status,"published")
            generator.generate.assert_called_once(); publisher.publish.assert_called_once()


if __name__=="__main__": unittest.main()
