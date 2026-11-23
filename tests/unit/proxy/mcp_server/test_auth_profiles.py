from typing import Final

import pytest

from litellm.proxy.mcp_server.auth_profiles import (
    InvalidAuthProfiles,
    parse_auth_profiles,
    profile_credentials_error,
)


def test_profile_ids_are_unique_per_server_ignoring_case_and_preserve_declared_spelling() -> None:
    profile: Final = {"id": "Build", "auth_type": "basic", "auth_value": "credential"}
    tickets: Final = parse_auth_profiles("tickets", [profile, {**profile, "id": "read"}])
    wiki: Final = parse_auth_profiles("wiki", [profile])
    assert isinstance(tickets, tuple) and [p.id for p in tickets] == ["Build", "read"]
    assert isinstance(wiki, tuple) and wiki == tickets[:1]
    assert parse_auth_profiles("tickets", [profile, {**profile, "id": "BUILD"}]) == InvalidAuthProfiles(
        "MCP server 'tickets' repeats profile id 'BUILD' (first 'Build')"
    )


@pytest.mark.parametrize("raw", (None, False, {}, "[]"))
def test_a_supplied_non_list_is_invalid_even_when_empty(raw: object) -> None:
    assert parse_auth_profiles("tickets", raw) == InvalidAuthProfiles(
        "additional_auth of MCP server 'tickets' must be a list"
    )
    assert parse_auth_profiles("tickets", []) == ()


@pytest.mark.parametrize(
    "entry",
    (
        {"id": "default", "auth_type": "basic", "auth_value": "secret-value"},
        {"id": "reader", "auth_type": "oauth2", "oauth2_flow": "authorization_code", "client_secret": "secret-value"},
        {"id": "reader", "auth_type": "secret-value"},
    ),
)
def test_invalid_entry_reports_its_server_and_position_without_echoing_input(entry: dict[str, str]) -> None:
    refusal: Final = parse_auth_profiles("tickets", [{"id": "good", "auth_type": "basic"}, entry])
    assert isinstance(refusal, InvalidAuthProfiles)
    assert refusal.message.startswith("additional_auth[1] of MCP server 'tickets' is invalid")
    assert "secret-value" not in refusal.message


def test_complete_static_and_client_credentials_profiles_can_be_used() -> None:
    profiles: Final = parse_auth_profiles(
        "tickets",
        [
            {"id": "a", "auth_type": "basic", "auth_value": "value"},
            {
                "id": "b",
                "auth_type": "oauth2",
                "oauth2_flow": "client_credentials",
                "client_id": "id",
                "client_secret": "secret",
                "token_url": "https://issuer.example/token",
            },
        ],
    )
    assert isinstance(profiles, tuple) and [p.id for p in profiles] == ["a", "b"]
    assert profile_credentials_error("tickets", profiles) is None


@pytest.mark.parametrize(
    "entry",
    (
        {"auth_type": "basic"},
        {"auth_type": "basic", "authentication_token": ""},
        {"auth_type": "oauth2", "oauth2_flow": "client_credentials", "client_id": "id", "client_secret": "secret"},
        {
            "auth_type": "oauth2",
            "oauth2_flow": "client_credentials",
            "client_secret": "secret",
            "token_url": "https://issuer",
        },
        {"auth_type": "oauth2", "oauth2_flow": "client_credentials", "client_id": "id", "token_url": "https://issuer"},
    ),
)
def test_a_redacted_edit_can_parse_but_cannot_supply_a_new_servers_credentials(entry: dict[str, str]) -> None:
    profiles: Final = parse_auth_profiles(
        "tickets", [{"id": "first", "auth_type": "basic", "auth_value": "value"}, {"id": "build", **entry}]
    )
    assert isinstance(profiles, tuple)
    error: Final = profile_credentials_error("tickets", profiles)
    assert isinstance(error, InvalidAuthProfiles)
    assert error.message.startswith("Profile 'build' of MCP server 'tickets' requires ")
