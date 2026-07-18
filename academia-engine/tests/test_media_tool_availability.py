import unittest

from app.media import MediaToolAvailabilityChecker, MediaToolAvailabilityError, ProcessResult


class MediaToolAvailabilityTests(unittest.TestCase):
    def test_checker_uses_runner_for_both_tools(self) -> None:
        runner = FakeRunner()
        MediaToolAvailabilityChecker(runner).require_available()
        self.assertEqual(runner.calls, [("ffmpeg", "-version"), ("ffprobe", "-version")])

    def test_unavailable_tool_is_explicit_and_stops(self) -> None:
        runner = FakeRunner(failure_tool="ffprobe")
        with self.assertRaises(MediaToolAvailabilityError) as caught:
            MediaToolAvailabilityChecker(runner).require_available()
        self.assertIn("ffprobe", str(caught.exception))


class FakeRunner:
    def __init__(self, failure_tool=None):
        self.failure_tool = failure_tool
        self.calls = []

    def run(self, args, timeout_seconds=None):
        args = tuple(args)
        self.calls.append(args)
        return ProcessResult(exit_code=1 if args[0] == self.failure_tool else 0, stdout="", stderr="")
