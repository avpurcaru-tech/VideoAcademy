import unittest
from pathlib import Path


class SunoProviderReportTests(unittest.TestCase):
    def test_report_distinguishes_official_third_party_and_unofficial_sources(self):
        root=Path(__file__).resolve().parents[1]
        report=(root/"docs"/"MUSIC_PROVIDER_SUNO.md").read_text(encoding="utf-8")
        self.assertIn("https://platform.suno.com/",report)
        self.assertIn("Suno, Inc.",report)
        self.assertIn("not",report[report.index("`https://docs.sunoapi.org/`"):].splitlines()[0].lower())
        self.assertIn("third-party",report)
        self.assertIn("reverse-engineer",report)
        self.assertIn("Romanian lyrics support",report)
        self.assertIn("No real API calls",report)

    def test_report_does_not_claim_unconfirmed_official_contract(self):
        report=(Path(__file__).resolve().parents[1]/"docs"/"MUSIC_PROVIDER_SUNO.md").read_text(encoding="utf-8")
        for marker in ("Authentication | Not documented","API base URL | Not documented",
                       "Submit method and endpoint | Not documented","Status vocabulary | Not documented"):
            self.assertIn(marker,report)


if __name__=="__main__": unittest.main()
