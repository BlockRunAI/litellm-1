from collections.abc import Mapping
from typing import Final, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from litellm.proxy import proxy_server
from litellm.proxy._types import (
    UI_TEAM_ID,
    LiteLLM_JWTAuth,
    LiteLLM_ObjectPermissionTable,
    LiteLLM_TeamTableCachedObj,
    UserAPIKeyAuth,
)
from litellm.proxy.common_utils.user_api_key_cache import UserApiKeyCache
from litellm.proxy.mcp_server.auth.auth_profile_grants import caller_auth_profile_grants
from litellm.redis.stores.auth_cache import AuthObject
from litellm.types.agents import AgentResponse
from litellm.types.mcp_server.auth_profile_grants import UnreadableGrants
from litellm.types.proxy.agent_identity import ManagedAgentContext
from tests.unit.proxy.rust_auth_stores import FakeTeamStore, RustAuthStores, RustStoreRows, install_rust_auth_stores


def _permission(permission_id: str, grants: Mapping[str, str] | None = None) -> LiteLLM_ObjectPermissionTable:
    return LiteLLM_ObjectPermissionTable(object_permission_id=permission_id, mcp_auth_profiles=grants)


def _caller(
    *,
    key: LiteLLM_ObjectPermissionTable | None = None,
    agent: Mapping[str, str] | None = None,
    mode: Literal["autonomous", "delegated"] | None = None,
    team_id: str | None = "payments",
    admitted_subject: bool = False,
) -> UserAPIKeyAuth:
    managed: Final = (
        {}
        if agent is None
        else {
            "managed_agent_policy": AgentResponse(
                agent_id="agents-runner",
                agent_name="agents-runner",
                agent_card_params={},
                object_permission={"object_permission_id": "perm-agent", "mcp_auth_profiles": dict(agent)},
            ),
        }
    )
    context: Final = (
        {}
        if mode is None
        else {
            "managed_agent_context": ManagedAgentContext(
                agent_id="agents-runner", mode=mode, user_id="reader" if mode == "delegated" else None
            )
        }
    )
    auth: Final = UserAPIKeyAuth(
        api_key="hashed-key",
        team_id=team_id,
        agent_id=None if agent is None else "agents-runner",
        object_permission=key,
        object_permission_id=None if key is None else key.object_permission_id,
    )
    return auth.model_copy(update={"mcp_admitted_user_subject": admitted_subject, **managed, **context})


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> None:
    permissions: Final = {
        "perm-team": _permission("perm-team", TEAM),
        "perm-bots": _permission("perm-bots", {"tickets": "bots"}),
    }
    install_rust_auth_stores(
        monkeypatch,
        RustAuthStores(
            team=FakeTeamStore(rows={"payments": {"team_id": "payments", "object_permission_id": "perm-team"}}),
            object_permission=RustStoreRows(
                reader={
                    **permissions,
                    "agent-current": _permission("agent-current", {"tickets": "retired"}),
                    "agent-deleted": _permission("agent-deleted", {"tickets": "retired"}),
                    "timeout": _permission("timeout", {"tickets": "retired"}),
                    "disconnected": _permission("disconnected", {"tickets": "retired"}),
                },
                writer={
                    **permissions,
                    "agent-current": _permission("agent-current", {"tickets": "bots"}),
                    "timeout": TimeoutError(),
                    "disconnected": RuntimeError("database disconnected"),
                },
            ),
        ),
    )
    monkeypatch.setattr(proxy_server, "prisma_client", MagicMock())
    monkeypatch.setattr(proxy_server, "user_api_key_cache", UserApiKeyCache())


KEY: Final = _permission("perm-key", {"tickets": "bots"})
EMPTY: Final[Mapping[str, str]] = {}
TEAM: Final = {"tickets": "ops"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        pytest.param(_caller(), (TEAM,), id="a-single-team-grants-by-itself"),
        pytest.param(_caller(key=KEY), ({"tickets": "bots"}, TEAM), id="the-key-comes-before-the-team"),
        pytest.param(
            _caller(key=_permission("perm-empty")), (EMPTY, TEAM), id="a-key-without-grants-still-comes-before-the-team"
        ),
        pytest.param(
            _caller(key=KEY, agent={"tickets": "agents"}, mode="autonomous"),
            ({"tickets": "agents"}, {"tickets": "bots"}, TEAM),
            id="R6-an-autonomous-agent-comes-before-the-key",
        ),
        pytest.param(
            _caller(key=KEY, agent={"tickets": "agents"}, mode="delegated"),
            (),
            id="R7-a-delegated-agent-holds-no-carrier",
        ),
        pytest.param(
            _caller(key=KEY, agent={"tickets": "agents"}),
            ({"tickets": "agents"}, {"tickets": "bots"}, TEAM),
            id="R6-a-key-bound-to-an-agent-is-the-agent-acting",
        ),
        pytest.param(
            _caller(key=KEY, team_id=None), ({"tickets": "bots"},), id="a-key-without-a-team-has-no-team-step"
        ),
        pytest.param(
            _caller(key=KEY, team_id=UI_TEAM_ID), ({"tickets": "bots"},), id="the-dashboard-team-holds-no-carrier"
        ),
        pytest.param(
            _caller(key=KEY, admitted_subject=True),
            (),
            id="R8-a-caller-admitted-through-several-teams-skips-the-key-and-team-steps",
        ),
        pytest.param(None, (), id="no-caller-holds-no-carrier"),
    ],
)
async def test_grants_follow_the_most_specific_carrier_first(
    caller: UserAPIKeyAuth | None, expected: tuple[Mapping[str, str], ...]
) -> None:
    assert await caller_auth_profile_grants(caller) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        pytest.param(
            UserAPIKeyAuth(api_key="hashed-key", object_permission_id="perm-bots"),
            ({"tickets": "bots"},),
            id="a-key-cached-without-its-row-is-hydrated-by-main",
        ),
        pytest.param(UserAPIKeyAuth(api_key="hashed-key"), (), id="a-key-without-a-row-grants-nothing"),
    ],
)
@pytest.mark.usefixtures("database")
async def test_a_key_cached_without_its_permission_row_gets_what_main_hydrates(
    caller: UserAPIKeyAuth, expected: tuple[Mapping[str, str], ...]
) -> None:
    assert await caller_auth_profile_grants(caller) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_connected",
    [
        pytest.param(True, id="R10-a-key-naming-a-row-that-cannot-be-found-has-unreadable-grants"),
        pytest.param(False, id="R10-a-key-naming-a-row-on-a-proxy-without-a-database-has-unreadable-grants"),
    ],
)
async def test_a_key_naming_a_permission_row_that_cannot_be_read_has_unreadable_grants(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, database_connected: bool
) -> None:
    if database_connected:
        request.getfixturevalue("database")
    else:
        monkeypatch.setattr(proxy_server, "prisma_client", None)

    assert (
        await caller_auth_profile_grants(UserAPIKeyAuth(api_key="hashed-key", object_permission_id="perm-missing"))
        == UnreadableGrants()
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
@pytest.mark.parametrize(("fresh", "expected"), [(True, (EMPTY, TEAM)), (False, UnreadableGrants())])
async def test_cached_team_with_a_missing_permission_is_unreadable_until_hydrated(
    fresh: bool, expected: tuple[Mapping[str, str], ...] | UnreadableGrants
) -> None:
    await proxy_server.user_api_key_cache.async_set_cache(
        key=AuthObject.team("payments"),
        value=LiteLLM_TeamTableCachedObj(team_id="payments", object_permission_id="perm-team"),
    )
    caller: Final = _caller(agent={}, mode="autonomous").model_copy(update={"requires_fresh_policy": fresh})
    assert await caller_auth_profile_grants(caller) == expected


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
@pytest.mark.parametrize(
    ("teams", "expected"), [(("payments",), (TEAM,)), (("payments", "payments"), (TEAM,)), (("payments", "design"), ())]
)
async def test_jwt_only_inherits_one_unambiguous_team(
    monkeypatch: pytest.MonkeyPatch, teams: tuple[str, ...], expected: tuple[Mapping[str, str], ...]
) -> None:
    monkeypatch.setattr(proxy_server, "litellm_jwtauth", LiteLLM_JWTAuth(team_ids_jwt_field="groups"))
    caller: Final = _caller().model_copy(update={"jwt_claims": {"groups": list(teams)}})
    assert await caller_auth_profile_grants(caller) == expected


@pytest.mark.asyncio
async def test_unreadable_agent_link_cannot_degrade_to_the_keys_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    prisma: Final = MagicMock(**{"db.litellm_agentstable.find_unique": AsyncMock(side_effect=ConnectionError)})
    monkeypatch.setattr(proxy_server, "prisma_client", prisma)
    monkeypatch.setattr(proxy_server, "user_api_key_cache", UserApiKeyCache())
    caller: Final = _caller(key=KEY, team_id=None).model_copy(update={"agent_id": "agents-runner"})
    assert await caller_auth_profile_grants(caller) == UnreadableGrants()


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
async def test_a_scoped_permission_cannot_substitute_for_a_missing_key_permission() -> None:
    caller: Final = _caller(key=KEY).model_copy(update={"object_permission_id": "original"})
    assert await caller_auth_profile_grants(caller) == UnreadableGrants()


@pytest.mark.asyncio
async def test_malformed_managed_agent_permission_has_unreadable_grants() -> None:
    policy: Final = AgentResponse(
        agent_id="agents-runner",
        agent_name="runner",
        agent_card_params={},
        object_permission={"object_permission_id": "perm-agent", "mcp_auth_profiles": ["unreadable"]},
    )
    caller: Final = UserAPIKeyAuth(agent_id=policy.agent_id, object_permission=KEY).model_copy(
        update={"managed_agent_policy": policy}
    )
    assert await caller_auth_profile_grants(caller) == UnreadableGrants()


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("link", ["timeout", "disconnected"])
async def test_fresh_key_permission_store_failure_returns_unreadable_grants(link: str) -> None:
    caller: Final = UserAPIKeyAuth(object_permission_id=link)
    assert await caller_auth_profile_grants(caller) == ({"tickets": "retired"},)
    fresh: Final = caller.model_copy(update={"requires_fresh_policy": True})
    assert await caller_auth_profile_grants(fresh) == UnreadableGrants()


@pytest.mark.asyncio
@pytest.mark.usefixtures("database")
@pytest.mark.parametrize(
    ("link", "expected"), [("agent-current", ({"tickets": "bots"},)), ("agent-deleted", UnreadableGrants())]
)
async def test_managed_agent_with_an_unprojected_permission_reads_its_linked_grants(
    link: str, expected: tuple[Mapping[str, str], ...] | UnreadableGrants
) -> None:
    stale: Final = _permission(link, {"tickets": "retired"})
    await proxy_server.user_api_key_cache.async_set_cache(key=AuthObject.object_permission(link), value=stale)
    policy: Final = AgentResponse(
        agent_id="agent", agent_name="agent", agent_card_params={}, object_permission={"object_permission_id": link}
    )
    caller: Final = UserAPIKeyAuth(agent_id=policy.agent_id).model_copy(
        update={"managed_agent_policy": policy, "requires_fresh_policy": True}
    )
    assert await caller_auth_profile_grants(caller) == expected
