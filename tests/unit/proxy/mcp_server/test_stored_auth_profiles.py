from collections.abc import Mapping
from typing import Final

import pytest
from nacl.exceptions import CryptoError

from litellm.proxy.mcp_server.auth_profiles import InvalidAuthProfiles, parse_auth_profiles, profile_credentials_error
from litellm.proxy.mcp_server.stored_auth_profiles import (
    MissingSigningKey,
    ProfileEncryptionFailed,
    restore_auth_profiles,
    store_auth_profiles,
)

_OAUTH: Final = {
    "id": "Build",
    "auth_type": "oauth2",
    "oauth2_flow": "client_credentials",
    "token_url": "https://issuer.test/token",
    "client_id": "example-app",
    "client_secret": "example-secret",
    "scopes": ["read"],
}
_STATIC: Final = {"id": "Read", "auth_type": "basic", "auth_value": "example-token"}
_KEY: Final = "example-storage-key"


def test_wire_round_trip_encrypts_every_secret_and_preserves_the_original_profiles() -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH, _STATIC], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    assert "example-app" not in repr(stored)
    assert "example-secret" not in repr(stored)
    assert "example-token" not in repr(stored)
    assert restore_auth_profiles("tickets", list(stored), signing_key=_KEY) == parse_auth_profiles(
        "tickets", [_OAUTH, _STATIC]
    )


def test_redacted_edit_preserves_ciphertext_and_spelling_and_replaces_the_set() -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH, _STATIC], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    update: Final = {"id": "BUILD", "auth_type": "oauth2", "oauth2_flow": "client_credentials", "scopes": None}
    edited: Final = store_auth_profiles("tickets", [update], stored, signing_key=_KEY)
    assert edited == ({**stored[0], "scopes": None},)
    assert store_auth_profiles("tickets", [], edited, signing_key=_KEY) == ()


def test_rotating_one_credential_preserves_the_other_ciphertext() -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    edit: Final = {
        "id": "Build",
        "auth_type": "oauth2",
        "oauth2_flow": "client_credentials",
        "client_secret": "replacement",
    }
    new: Final = store_auth_profiles("tickets", [edit], stored, signing_key=_KEY)
    assert isinstance(new, tuple)
    assert new[0]["client_id"] == stored[0]["client_id"]
    assert restore_auth_profiles("tickets", new, signing_key=_KEY) == parse_auth_profiles(
        "tickets", [{**_OAUTH, "client_secret": "replacement"}]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "Build", "auth_type": "oauth2", "oauth2_flow": "client_credentials", "token_url": "https://other.test"},
        {"id": "Read", "auth_type": "token"},
    ],
)
def test_new_token_endpoint_or_auth_mode_requires_fresh_credentials(change: Mapping[str, str]) -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH, _STATIC], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    refusal: Final = store_auth_profiles("tickets", [change], stored, signing_key=_KEY)
    assert isinstance(refusal, InvalidAuthProfiles) and "requires" in refusal.message


def test_wrong_key_retains_the_profile_but_leaves_it_unusable() -> None:
    stored: Final = store_auth_profiles("tickets", [_STATIC], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    restored: Final = restore_auth_profiles("tickets", stored, signing_key="different-key")
    assert restored == parse_auth_profiles(
        "tickets", [{"id": "Read", "auth_type": "basic", "authentication_token": None}]
    )
    assert isinstance(restored, tuple)
    assert profile_credentials_error("tickets", restored) == InvalidAuthProfiles(
        "Profile 'Read' of MCP server 'tickets' requires authentication_token"
    )


def test_an_absent_signing_key_refuses_the_write() -> None:
    assert store_auth_profiles("tickets", [_STATIC], None, signing_key="") == MissingSigningKey()


@pytest.mark.usefixtures("fips_mode")
def test_a_signing_key_refused_by_the_provider_refuses_the_whole_edit() -> None:
    assert store_auth_profiles("tickets", [_STATIC, _OAUTH], None, signing_key="too-short") == ProfileEncryptionFailed()


@pytest.mark.parametrize("fault", [CryptoError, OSError])
def test_a_provider_failure_refuses_the_whole_edit_without_exposing_the_secret(
    monkeypatch: pytest.MonkeyPatch, fault: type[Exception]
) -> None:
    def unavailable(value: str, *, new_encryption_key: str) -> str:
        raise fault("test-provider-diagnostic")

    monkeypatch.setattr("litellm.proxy.mcp_server.stored_auth_profiles.encrypt_value_helper", unavailable)
    assert store_auth_profiles("tickets", [_STATIC], None, signing_key=_KEY) == ProfileEncryptionFailed()


def test_one_damaged_ciphertext_preserves_readable_fields_and_other_profiles() -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH, _STATIC], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    damaged: Final = ({**stored[0], "client_secret": "bad-ciphertext"}, stored[1])
    assert restore_auth_profiles("tickets", damaged, signing_key=_KEY) == parse_auth_profiles(
        "tickets", [{**_OAUTH, "client_secret": None}, _STATIC]
    )


def test_changing_issuer_with_complete_credentials_drops_the_old_issuer_settings() -> None:
    stored: Final = store_auth_profiles("tickets", [_OAUTH], None, signing_key=_KEY)
    assert isinstance(stored, tuple)
    update: Final = {
        "id": "BUILD",
        "auth_type": "oauth2",
        "oauth2_flow": "client_credentials",
        "token_url": "https://new.test/token",
        "client_id": "new-app",
        "client_secret": "new-secret",
    }
    edited: Final = store_auth_profiles("tickets", [update], stored, signing_key=_KEY)
    assert restore_auth_profiles("tickets", edited, signing_key=_KEY) == parse_auth_profiles(
        "tickets", [{**update, "id": "Build"}]
    )


@pytest.mark.parametrize("stored,submitted", ((None, False), (False, [_STATIC])))
def test_malformed_sets_refuse_writes(stored: object, submitted: object) -> None:
    assert store_auth_profiles("tickets", submitted, stored, signing_key=_KEY) == InvalidAuthProfiles(
        "additional_auth of MCP server 'tickets' must be a list"
    )


def test_a_malformed_stored_set_refuses_the_read() -> None:
    assert restore_auth_profiles("tickets", False, signing_key=_KEY) == InvalidAuthProfiles(
        "additional_auth of MCP server 'tickets' must be a list"
    )
