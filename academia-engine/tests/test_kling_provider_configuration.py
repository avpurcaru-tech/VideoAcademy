import unittest
from unittest.mock import patch

from app.config import KlingProviderConfigurationError
from app.providers import KlingProviderRegistry


VALID={"KLING_API_KEY":"top-secret","KLING_RESOLUTION":"720p","KLING_DURATION":"15",
       "KLING_AUDIO":"off","KLING_MULTI_SHOT":"true"}


class KlingProviderConfigurationTests(unittest.TestCase):
    def test_authoritative_factory_constructs_without_http(self):
        with patch("app.providers.kling_client.urlopen") as http:
            configuration,provider=KlingProviderRegistry().construct("kling",VALID)
        self.assertEqual(configuration.generation.duration,15); self.assertIsNotNone(provider); http.assert_not_called()

    def test_missing_credential_is_field_level_and_secret_free(self):
        with self.assertRaises(KlingProviderConfigurationError) as caught:
            KlingProviderRegistry().construct("kling",{})
        self.assertEqual(caught.exception.diagnostics,(("KLING_API_KEY","missing"),))
        self.assertNotIn("top-secret",str(caught.exception))

    def test_each_invalid_generation_setting_is_identified(self):
        cases=(("KLING_RESOLUTION","1080p"),("KLING_DURATION","10"),("KLING_AUDIO","on"),("KLING_MULTI_SHOT","maybe"))
        for field,value in cases:
            environment=dict(VALID); environment[field]=value
            with self.subTest(field=field),self.assertRaises(KlingProviderConfigurationError) as caught:
                KlingProviderRegistry().construct("kling",environment)
            self.assertEqual(caught.exception.diagnostics[0][0],field)

    def test_invalid_base_url_is_rejected_without_exposing_value(self):
        environment=dict(VALID); environment["KLING_BASE_URL"]="http://secret.invalid"
        with self.assertRaises(KlingProviderConfigurationError) as caught:
            KlingProviderRegistry().construct("kling",environment)
        self.assertEqual(caught.exception.diagnostics,(("KLING_BASE_URL","invalid URL"),))
        self.assertNotIn("secret.invalid",str(caught.exception))


if __name__=="__main__": unittest.main()
