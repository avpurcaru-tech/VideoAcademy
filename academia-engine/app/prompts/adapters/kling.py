from app.models import DirectorScene, VideoCharacter, VideoEnvironment, VideoRequest


class KlingPromptAdapter:
    """Maps a director scene to the shared, provider-neutral video contract."""

    def create_video_request(self, scene: DirectorScene) -> VideoRequest:
        return VideoRequest(
            scene_number=scene.scene_number,
            duration_seconds=scene.duration_seconds,
            environment=VideoEnvironment(
                location_name=scene.location.name,
                location_description=scene.location.description,
                time_of_day=scene.location.time_of_day,
                lighting_description=scene.lighting.description,
                lighting_intensity=scene.lighting.intensity,
            ),
            characters=[
                VideoCharacter(
                    id=character.id,
                    name=character.name,
                    role=character.role,
                    appearance=character.appearance,
                )
                for character in scene.characters
            ],
            character_actions=scene.character_actions,
            camera=scene.camera,
            transition=scene.transition,
        )
