from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Annotated, Final

from pydantic import FailFast, TypeAdapter, ValidationError

from litellm.types.mcp_server.auth_profile import ClientCredentialsAuthProfile, MCPAuthProfile

_PROFILES: Final[TypeAdapter[tuple[MCPAuthProfile, ...]]] = TypeAdapter(
    Annotated[tuple[MCPAuthProfile, ...], FailFast()]
)


@dataclass(frozen=True, slots=True)
class InvalidAuthProfiles:
    message: str


def parse_auth_profiles(server: str, raw: object) -> tuple[MCPAuthProfile, ...] | InvalidAuthProfiles:
    if not isinstance(raw, (tuple, list)):
        return InvalidAuthProfiles(f"additional_auth of MCP server '{server}' must be a list")

    try:
        profiles: Final = _PROFILES.validate_python(raw)
    except ValidationError as exc:
        error: Final = exc.errors(include_input=False, include_context=False)[0]
        position: Final = error["loc"][0]
        return InvalidAuthProfiles(f"additional_auth[{position}] of MCP server '{server}' is invalid: {error['msg']}")

    counts: Final = Counter(profile.id.lower() for profile in profiles)
    if len(counts) != len(profiles):
        repeated: Final = next(key for key, count in counts.items() if count > 1)
        first, second = islice((profile.id for profile in profiles if profile.id.lower() == repeated), 2)
        return InvalidAuthProfiles(f"MCP server '{server}' repeats profile id '{second}' (first '{first}')")
    return profiles


def profile_credentials_error(server: str, profiles: Iterable[MCPAuthProfile]) -> InvalidAuthProfiles | None:
    for profile in profiles:
        if isinstance(profile, ClientCredentialsAuthProfile):
            if not (profile.client_id and profile.client_secret and profile.token_url):
                return InvalidAuthProfiles(
                    f"Profile '{profile.id}' of MCP server '{server}' requires client_id, client_secret and token_url"
                )
        elif not profile.authentication_token:
            return InvalidAuthProfiles(f"Profile '{profile.id}' of MCP server '{server}' requires authentication_token")
    return None
