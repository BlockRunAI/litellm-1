from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from nacl.exceptions import CryptoError
from pydantic import JsonValue, SecretStr, TypeAdapter

from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_if_encrypted_with, encrypt_value_helper
from litellm.proxy.mcp_server.auth_profiles import InvalidAuthProfiles, parse_auth_profiles, profile_credentials_error
from litellm.types.mcp_server.auth_profile import ClientCredentialsAuthProfile, MCPAuthProfile

_PUBLIC: Final = TypeAdapter(Mapping[str, JsonValue])


@dataclass(frozen=True, slots=True)
class MissingSigningKey:
    """Writing profiles requires a configured encryption key."""


@dataclass(frozen=True, slots=True)
class ProfileEncryptionFailed:
    """The configured provider could not encrypt a submitted credential."""


def _secrets(profile: MCPAuthProfile) -> Mapping[str, SecretStr | None]:
    if isinstance(profile, ClientCredentialsAuthProfile):
        return {"client_id": profile.client_id, "client_secret": profile.client_secret}
    return {"authentication_token": profile.authentication_token}


def _can_inherit(profile: MCPAuthProfile, previous: MCPAuthProfile) -> bool:
    if isinstance(profile, ClientCredentialsAuthProfile) and isinstance(previous, ClientCredentialsAuthProfile):
        return "token_url" not in profile.model_fields_set or profile.token_url == previous.token_url
    return profile.auth_type == previous.auth_type


def _encrypted_secrets(profile: MCPAuthProfile, signing_key: str) -> Mapping[str, str]:
    return {
        field: encrypt_value_helper(secret.get_secret_value(), new_encryption_key=signing_key)
        for field, secret in _secrets(profile).items()
        if secret
    }


def _stored_entry(
    profile: MCPAuthProfile, previous: MCPAuthProfile | None, encrypted: Mapping[str, str]
) -> Mapping[str, JsonValue]:
    inherits: Final = previous is not None and _can_inherit(profile, previous)
    public: Final = _PUBLIC.validate_python(profile.model_dump(mode="json", exclude_unset=True))
    retained: Final[Mapping[str, SecretStr | None]] = _secrets(previous) if previous is not None and inherits else {}
    return {
        **(_PUBLIC.validate_python(previous.model_dump(mode="json")) if previous is not None and inherits else {}),
        **public,
        "id": previous.id if previous is not None else profile.id,
        **{field: secret.get_secret_value() if secret else None for field, secret in retained.items()},
        **encrypted,
    }


def store_auth_profiles(
    server: str, submitted: object, stored: object, *, signing_key: str
) -> tuple[Mapping[str, JsonValue], ...] | InvalidAuthProfiles | MissingSigningKey | ProfileEncryptionFailed:
    """Replace the server's profile set, retaining eligible ciphertext across redacted edits."""
    if not signing_key:
        return MissingSigningKey()
    existing: Final = parse_auth_profiles(server, () if stored is None else stored)
    if isinstance(existing, InvalidAuthProfiles):
        return existing
    requested: Final = parse_auth_profiles(server, submitted)
    if isinstance(requested, InvalidAuthProfiles):
        return requested
    previous: Final = {p.id.lower(): p for p in existing}
    try:
        encrypted: Final = tuple(_encrypted_secrets(profile, signing_key) for profile in requested)
    except (CryptoError, OSError, ValueError):
        return ProfileEncryptionFailed()
    rows: Final = tuple(
        _stored_entry(profile, previous.get(profile.id.lower()), secrets)
        for profile, secrets in zip(requested, encrypted, strict=True)
    )
    merged: Final = parse_auth_profiles(server, rows)
    if isinstance(merged, InvalidAuthProfiles):
        return merged
    return profile_credentials_error(server, merged) or rows


def restore_auth_profiles(
    server: str, stored: object, *, signing_key: str
) -> tuple[MCPAuthProfile, ...] | InvalidAuthProfiles:
    """Keep unreadable profiles identifiable so a grant to one can fail closed."""
    profiles: Final = parse_auth_profiles(server, () if stored is None else stored)
    if isinstance(profiles, InvalidAuthProfiles):
        return profiles
    return parse_auth_profiles(
        server,
        tuple(
            {
                **_PUBLIC.validate_python(profile.model_dump(mode="json")),
                **{
                    field: decrypt_if_encrypted_with(secret.get_secret_value(), signing_key)
                    if secret and signing_key
                    else None
                    for field, secret in _secrets(profile).items()
                },
            }
            for profile in profiles
        ),
    )
