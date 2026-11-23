from types import MappingProxyType
from typing import Final

from litellm._logging import verbose_proxy_logger
from litellm.models.object_permission import LiteLLM_ObjectPermissionTable
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.jwt import ClaimReading
from litellm.proxy.mcp_server.auth.user_api_key_auth_mcp import (
    MCPRequestHandler,
    is_mcp_admitted_user_subject,
)
from litellm.types.mcp_server.auth_profile_grants import ProfileGrants, UnreadableGrants


def _unreadable(link: str | None, permission: LiteLLM_ObjectPermissionTable | None) -> bool:
    return link is not None and (permission is None or link != permission.object_permission_id)


def _one_team(caller: UserAPIKeyAuth) -> bool:
    if not caller.team_id:
        return False
    if not caller.jwt_claims:
        return True
    from litellm.proxy.proxy_server import litellm_jwtauth

    teams: Final = ClaimReading(litellm_jwtauth).get_all_jwt_team_ids(caller.jwt_claims)
    return len(frozenset(teams)) <= 1


async def caller_auth_profile_grants(caller: UserAPIKeyAuth | None) -> tuple[ProfileGrants, ...] | UnreadableGrants:
    if caller is None or (
        caller.managed_agent_context is not None and caller.managed_agent_context.mode == "delegated"
    ):
        return ()
    try:
        admitted_subject: Final = is_mcp_admitted_user_subject(caller)
        key: Final = None if admitted_subject else await MCPRequestHandler.key_object_permission_hydrated(caller)
        if not admitted_subject and _unreadable(caller.object_permission_id, key):
            return UnreadableGrants()
        team: Final = (
            await MCPRequestHandler.permission_team(caller) if not admitted_subject and _one_team(caller) else None
        )
        if team is not None and _unreadable(team.object_permission_id, team.object_permission):
            return UnreadableGrants()
        agent: Final = await MCPRequestHandler.agent_permission_for_grants(caller)
    except Exception as exc:  # noqa: BLE001  # store and cache backends can raise different read failures
        verbose_proxy_logger.warning("Could not read MCP auth profile grants (%s)", type(exc).__name__)
        return UnreadableGrants()
    if isinstance(agent, UnreadableGrants):
        return agent
    return tuple(
        MappingProxyType(dict(permission.mcp_auth_profiles or {}))
        for permission in (agent, key, team.object_permission if team is not None else None)
        if permission is not None
    )
