import hashlib
import os
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.models import VideoArtifact
from app.providers import KlingVideoArtifactDownloader
from app.services import (
    ArtifactDestinationExistsError,
    ArtifactDownloadError,
    ArtifactDownloadValidationError,
)


class KlingVideoArtifactDownloaderTests(unittest.TestCase):
    def test_streamed_download_hashes_fsyncs_and_atomically_renames(self) -> None:
        content = b"video-chunk-one" + b"video-chunk-two"
        response = FakeStreamResponse(
            chunks=[b"video-chunk-one", b"video-chunk-two"],
            headers={"Content-Type": "video/mp4", "Content-Length": str(len(content))},
        )
        opener = RecordingOpener(response)
        original_replace = os.replace
        replace_calls: list[tuple[Path, Path]] = []

        with TemporaryDirectory() as directory, patch(
            "app.providers.kling_downloader.os.replace",
            side_effect=lambda source, destination: (
                replace_calls.append((Path(source), Path(destination))),
                original_replace(source, destination),
            )[1],
        ):
            destination = Path(directory) / "nested" / "episode.mp4"
            downloaded = KlingVideoArtifactDownloader(opener=opener, chunk_size=4).download_video_artifact(
                self._artifact(), destination
            )

            self.assertEqual(destination.read_bytes(), content)
            self.assertEqual(downloaded.byte_size, len(content))
            self.assertEqual(downloaded.sha256, hashlib.sha256(content).hexdigest())
            self.assertEqual(downloaded.content_type, "video/mp4")
            self.assertNotIn("url", downloaded.model_dump())
            self.assertEqual(len(replace_calls), 1)
            self.assertEqual(replace_calls[0][1], destination)
            self.assertTrue(response.closed)

        request = opener.request
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertTrue(all(size == 4 for size in response.read_sizes))

    def test_network_failure_removes_temporary_file(self) -> None:
        response = FakeStreamResponse(
            chunks=[b"partial", OSError("network failure")], headers={"Content-Type": "video/mp4"}
        )
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "video.mp4"
            with self.assertRaises(ArtifactDownloadError) as context:
                KlingVideoArtifactDownloader(opener=RecordingOpener(response)).download_video_artifact(
                    self._artifact(), destination
                )

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])
            self.assertNotIn("signed", str(context.exception))

    def test_content_length_mismatch_removes_temporary_file(self) -> None:
        response = FakeStreamResponse(
            chunks=[b"short"],
            headers={"Content-Type": "video/mp4", "Content-Length": "100"},
        )
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "video.mp4"
            with self.assertRaises(ArtifactDownloadValidationError):
                KlingVideoArtifactDownloader(opener=RecordingOpener(response)).download_video_artifact(
                    self._artifact(), destination
                )

            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_rejects_empty_non_success_and_invalid_content_type_responses(self) -> None:
        cases = [
            (FakeStreamResponse(chunks=[]), ArtifactDownloadValidationError),
            (FakeStreamResponse(chunks=[b"error"], status=404), ArtifactDownloadValidationError),
            (
                FakeStreamResponse(chunks=[b"<html>error</html>"], headers={"Content-Type": "text/html"}),
                ArtifactDownloadValidationError,
            ),
            (
                FakeStreamResponse(chunks=[b'{"error": true}'], headers={"Content-Type": "application/json"}),
                ArtifactDownloadValidationError,
            ),
        ]
        for response, error_type in cases:
            with self.subTest(status=response.status, content_type=response.headers.get("Content-Type")):
                with TemporaryDirectory() as directory, self.assertRaises(error_type):
                    KlingVideoArtifactDownloader(opener=RecordingOpener(response)).download_video_artifact(
                        self._artifact(), Path(directory) / "video.mp4"
                    )

    def test_existing_destination_is_not_overwritten(self) -> None:
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "video.mp4"
            destination.write_bytes(b"existing")
            opener = RecordingOpener(FakeStreamResponse(chunks=[b"new"]))

            with self.assertRaises(ArtifactDestinationExistsError):
                KlingVideoArtifactDownloader(opener=opener).download_video_artifact(
                    self._artifact(), destination
                )

            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertIsNone(opener.request)

    @staticmethod
    def _artifact() -> VideoArtifact:
        return VideoArtifact(
            artifact_id="video-01",
            url="https://cdn.example.test/signed-secret-url",
            content_type="video/mp4",
        )


class RecordingOpener:
    def __init__(self, response: "FakeStreamResponse") -> None:
        self._response = response
        self.request = None

    def __call__(self, request: object, timeout: float) -> "FakeStreamResponse":
        self.request = request
        return self._response


class FakeStreamResponse:
    def __init__(
        self,
        chunks: list[bytes | BaseException],
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self._chunks = chunks
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value
        self.status = status
        self.closed = False
        self.read_sizes: list[int] = []

    def getcode(self) -> int:
        return self.status

    def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        if not self._chunks:
            return b""
        next_chunk = self._chunks.pop(0)
        if isinstance(next_chunk, BaseException):
            raise next_chunk
        return next_chunk

    def close(self) -> None:
        self.closed = True
