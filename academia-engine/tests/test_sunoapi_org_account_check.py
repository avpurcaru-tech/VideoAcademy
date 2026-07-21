import os
import math
import unittest
from unittest.mock import Mock,patch

from app.cli.sunoapi_org_account_check import main as cli_main
from app.providers.sunoapi_org_music_provider import (SunoApiOrgAccountClient,SunoApiOrgAccountStatus,
    SunoApiOrgAuthenticationError,SunoApiOrgContractError,SunoApiOrgNetworkError,SunoApiOrgRateLimitError)


class FakeTransport:
    def __init__(self,payload): self.payload=payload; self.calls=[]
    def request_json(self,method,path,payload=None): self.calls.append((method,path,payload)); return self.payload
    def download(self,url): raise AssertionError("download is forbidden")


class AccountCheckTests(unittest.TestCase):
    def test_valid_authentication_parses_credits_with_exact_read_only_request(self):
        transport=FakeTransport({"code":200,"msg":"success","data":137})
        status=SunoApiOrgAccountClient(transport).get_account_status()
        self.assertEqual(status,SunoApiOrgAccountStatus(authentication_valid=True,credits_remaining=137,http_status=200,provider_code=200))
        self.assertEqual(transport.calls,[("GET","/api/v1/generate/credit",None)])

    def test_zero_credits_is_valid(self):
        status=SunoApiOrgAccountClient(FakeTransport({"code":200,"msg":"success","data":0})).get_account_status()
        self.assertTrue(status.authentication_valid); self.assertEqual(status.credits_remaining,0)

    def test_integral_float_credits_are_normalized_to_int(self):
        status=SunoApiOrgAccountClient(FakeTransport({"code":200,"msg":"success","data":1000.0})).get_account_status()
        self.assertEqual(status.credits_remaining,1000)
        self.assertIs(type(status.credits_remaining),int)

    def test_invalid_numeric_credit_forms_are_rejected(self):
        for credits in (1.5,-1.0,math.nan,math.inf,-math.inf,True,"1000"):
            with self.subTest(type=type(credits).__name__),self.assertRaises(SunoApiOrgContractError):
                SunoApiOrgAccountClient(FakeTransport({"code":200,"msg":"success","data":credits})).get_account_status()

    def test_malformed_and_provider_error_responses_are_rejected(self):
        for payload in ({"code":200,"msg":"success","data":{"credits":137}},
                        {"code":200,"msg":"success","data":"137"},
                        {"code":200,"msg":"success"},
                        {"code":200,"msg":"success","data":True},
                        {"code":200,"msg":"success","data":1.5},
                        {"code":200,"msg":"success","data":-1},
                        {"code":403,"msg":"Forbidden","data":None}):
            expected=SunoApiOrgContractError if payload["code"]==200 else Exception
            with self.subTest(payload=payload),self.assertRaises(expected): SunoApiOrgAccountClient(FakeTransport(payload)).get_account_status()

    def test_missing_configuration_is_sanitized_and_makes_no_request(self):
        with patch.dict(os.environ,{"SUNOAPI_ORG_API_KEY":""},clear=False),patch("app.cli.sunoapi_org_account_check.load_application_environment"),patch("urllib.request.urlopen") as http,patch("builtins.print") as emit:
            self.assertEqual(cli_main(),1)
        http.assert_not_called(); output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertEqual(output,"Third-party music provider configuration is missing.")

    def test_cli_success_prints_only_auth_and_credits(self):
        client=Mock(); client.get_account_status.return_value=SunoApiOrgAccountStatus(authentication_valid=True,credits_remaining=42,http_status=200,provider_code=200)
        with patch("app.cli.sunoapi_org_account_check.load_application_environment"),patch("app.cli.sunoapi_org_account_check.build_account_client",return_value=client),patch("builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        client.get_account_status.assert_called_once_with(); self.assertEqual(
            "\n".join(str(c.args[0]) for c in emit.call_args_list),"Authentication: valid\nCredits: 42")

    def test_401_403_and_429_outputs_are_sanitized(self):
        scenarios=((SunoApiOrgAuthenticationError("SECRET raw body",phase="http_failure",http_status=401,provider_code=401),"authentication rejected"),
                   (SunoApiOrgAuthenticationError("SECRET raw body",phase="http_failure",http_status=403,provider_code=403),"authentication rejected"),
                   (SunoApiOrgRateLimitError("SECRET raw body",phase="http_failure",http_status=429,provider_code=429,retry_after="9"),"rate limited"))
        for error,category in scenarios:
            client=Mock(); client.get_account_status.side_effect=error
            with self.subTest(status=error.http_status),patch("app.cli.sunoapi_org_account_check.load_application_environment"),patch("app.cli.sunoapi_org_account_check.build_account_client",return_value=client),patch("builtins.print") as emit:
                self.assertEqual(cli_main(),1)
            output="\n".join(str(c.args[0]) for c in emit.call_args_list)
            self.assertIn("Authentication: failed",output); self.assertIn(f"Account check: {category}",output)
            self.assertIn(f"HTTP status: {error.http_status}",output); self.assertNotIn("SECRET",output)

    def test_network_and_malformed_failures_hide_secrets(self):
        scenarios=((SunoApiOrgNetworkError("API_KEY Authorization SECRET",phase="network_before_response"),"network failure"),
                   (SunoApiOrgContractError("raw response SECRET",phase="response_parsing"),"malformed response"))
        for error,category in scenarios:
            client=Mock(); client.get_account_status.side_effect=error
            with self.subTest(category=category),patch("app.cli.sunoapi_org_account_check.load_application_environment"),patch("app.cli.sunoapi_org_account_check.build_account_client",return_value=client),patch("builtins.print") as emit:
                self.assertEqual(cli_main(),1)
            output="\n".join(str(c.args[0]) for c in emit.call_args_list)
            self.assertIn(f"Account check: {category}",output)
            for forbidden in ("API_KEY","Authorization","SECRET","raw response"): self.assertNotIn(forbidden,output)

    def test_malformed_credit_envelope_exposes_only_sanitized_shape(self):
        payload={"code":200,"msg":"SECRET response text","data":{"credits":137,"api_key":"SECRET"}}
        with self.assertRaises(SunoApiOrgContractError) as raised:
            SunoApiOrgAccountClient(FakeTransport(payload)).get_account_status()
        self.assertEqual(raised.exception.response_shape,(
            "Response root type: object",
            "Field present: code yes","Field type: code integer",
            "Field present: msg yes","Field type: msg string",
            "Field present: data yes","Field type: data object"))
        self.assertNotIn("SECRET","\n".join(raised.exception.response_shape))

    def test_real_cli_wiring_uses_requests_transport_and_updated_parser(self):
        response=Mock(status_code=200,headers={})
        response.json.return_value={"code":200,"msg":"success","data":1000.0}
        with patch.dict(os.environ,{"SUNOAPI_ORG_API_KEY":"masked-key","SUNOAPI_ORG_BASE_URL":"https://api.sunoapi.org"},clear=False),patch(
                "app.cli.sunoapi_org_account_check.load_application_environment"),patch(
                "app.providers.sunoapi_org_music_provider.requests.get",return_value=response) as http,patch(
                "builtins.print") as emit:
            self.assertEqual(cli_main(),0)
        http.assert_called_once()
        self.assertEqual(http.call_args.args[0],"https://api.sunoapi.org/api/v1/generate/credit")
        self.assertEqual("\n".join(str(c.args[0]) for c in emit.call_args_list),
                         "Authentication: valid\nCredits: 1000")

    def test_real_cli_wiring_reports_nested_shape_without_values(self):
        response=Mock(status_code=200,headers={})
        response.json.return_value={"data":{"code":200,"msg":"SECRET","data":100}}
        with patch.dict(os.environ,{"SUNOAPI_ORG_API_KEY":"masked-key","SUNOAPI_ORG_BASE_URL":"https://api.sunoapi.org"},clear=False),patch(
                "app.cli.sunoapi_org_account_check.load_application_environment"),patch(
                "app.providers.sunoapi_org_music_provider.requests.get",return_value=response),patch(
                "builtins.print") as emit:
            self.assertEqual(cli_main(),1)
        output="\n".join(str(c.args[0]) for c in emit.call_args_list)
        self.assertIn("Response root type: object",output)
        self.assertIn("Field present: code no",output)
        self.assertIn("Field type: data object",output)
        for forbidden in ("SECRET","masked-key",'\"code\"','\"data\"'): self.assertNotIn(forbidden,output)


if __name__=="__main__": unittest.main()
