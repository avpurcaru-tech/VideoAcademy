import argparse

from app.production import SceneDurationPolicy,StoryboardVideoPlanner
from app.project import ProjectRegistry
from app.storyboard import CreativeStoryboard
from app.providers import KlingProviderRegistry
from app.visual_references import (CanonicalReferenceUrlUnavailableError,
    VisualReferencePublicationRegistry)


def main():
    parser=argparse.ArgumentParser(description="Read-only Kling image-to-video request preflight.")
    parser.add_argument("--project-id",required=True)
    parser.add_argument("--video-provider",required=True,choices=("kling_image_to_video",))
    args=parser.parse_args(); print(f"Project ID: {args.project_id}")
    try:
        project=ProjectRegistry().load(args.project_id)
        storyboard_path=project.music_directory.parent/"input"/"storyboard.json"
        storyboard=CreativeStoryboard.model_validate_json(storyboard_path.read_text(encoding="utf-8"))
        requests=StoryboardVideoPlanner(SceneDurationPolicy(10)).build(storyboard,project.video_production_id)
        publications=VisualReferencePublicationRegistry(); mapper=KlingProviderRegistry.request_mapper(args.video_provider,publications)
    except Exception as error:
        print(f"Failure category: {getattr(error,'failure_category','image_to_video_preflight_failed')}")
        print("Kling calls: 0"); return 1
    supported=True
    for index,request in enumerate(requests,1):
        reference=request.scene_visual_reference; url=None; failure=None
        if reference is None: failure="canonical_scene_reference_missing"
        else:
            try: url=publications.resolve(reference)
            except CanonicalReferenceUrlUnavailableError: failure="canonical_reference_url_unavailable"
            except Exception: failure="canonical_reference_invalid"
        prompt=mapper._prompt(request)
        print(f"Scene: scene-{index:04d}")
        print("Characters: " + ", ".join(value.id for value in request.video_request.characters))
        print(f"Composite reference: {reference.reference_id if reference else 'unavailable'}")
        print(f"Reference SHA-256: {reference.sha256 if reference else 'unavailable'}")
        print("Reference URL available: " + ("yes" if url else "no"))
        print("Provider adapter: KlingImageToVideoProvider")
        print("Endpoint: /image-to-video/kling-3.0")
        print(f"Prompt characters: {len(prompt)}")
        print(f"Duration: {request.video_request.duration_seconds}")
        print("Request supported: " + ("yes" if failure is None else "no"))
        print(f"Failure category: {failure or 'none'}")
        supported=supported and failure is None
    print("Kling calls: 0")
    return 0 if supported else 1


if __name__=="__main__": raise SystemExit(main())
