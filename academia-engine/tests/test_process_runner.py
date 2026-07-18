import unittest
from unittest.mock import patch

from app.media import SubprocessProcessRunner


class SubprocessProcessRunnerTests(unittest.TestCase):
    def test_runner_passes_an_argument_list_without_a_shell(self) -> None:
        with patch("app.media.process_runner.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "output"
            run.return_value.stderr = ""
            result = SubprocessProcessRunner().run(["ffprobe", "file with spaces.mp4"], 12)

        run.assert_called_once_with(
            ["ffprobe", "file with spaces.mp4"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            shell=False,
        )
        self.assertEqual(result.exit_code, 0)
