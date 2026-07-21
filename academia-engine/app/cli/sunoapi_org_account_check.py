from app.config.environment import load_application_environment
from app.providers.sunoapi_org_music_provider import (SunoApiOrgAccountClient,SunoApiOrgAuthenticationError,
    SunoApiOrgConfigurationError,SunoApiOrgContractError,SunoApiOrgError,SunoApiOrgNetworkError,
    SunoApiOrgRateLimitError,SunoApiOrgTimeoutError)


def build_account_client() -> SunoApiOrgAccountClient:
    return SunoApiOrgAccountClient.from_environment()


def main() -> int:
    load_application_environment()
    try: status=build_account_client().get_account_status()
    except SunoApiOrgConfigurationError:
        print("Third-party music provider configuration is missing."); return 1
    except SunoApiOrgAuthenticationError as error:
        _failure(error,"authentication rejected"); return 1
    except SunoApiOrgRateLimitError as error:
        _failure(error,"rate limited"); return 1
    except SunoApiOrgTimeoutError as error:
        _failure(error,"network timeout"); return 1
    except SunoApiOrgNetworkError as error:
        _failure(error,"network failure"); return 1
    except SunoApiOrgContractError as error:
        _failure(error,"malformed response"); return 1
    except SunoApiOrgError as error:
        _failure(error,"provider failure"); return 1
    except Exception:
        print("Authentication: failed"); print("Account check: unexpected local failure"); return 1
    print("Authentication: valid")
    print(f"Credits: {status.credits_remaining if status.credits_remaining is not None else 'unavailable'}")
    return 0


def _failure(error: SunoApiOrgError,category: str) -> None:
    print("Authentication: failed"); print(f"Account check: {category}")
    if error.http_status is not None: print(f"HTTP status: {error.http_status}")
    if error.provider_code is not None: print(f"Provider code: {error.provider_code}")
    if error.retry_after: print(f"Retry-After: {error.retry_after}")
    for diagnostic in error.response_shape: print(diagnostic)


if __name__=="__main__": raise SystemExit(main())
