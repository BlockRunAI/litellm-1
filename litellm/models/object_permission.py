"""
Object permission table model.

Canonical definition for ``litellm_objectpermissiontable``. Re-exported from
``litellm.proxy._types`` for backwards compatibility.
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Final

from pydantic import model_serializer

from litellm.types.llms.base import LiteLLMPydanticObjectBase


class LiteLLM_ObjectPermissionTable(LiteLLMPydanticObjectBase):
    """Represents a LiteLLM_ObjectPermissionTable record"""

    object_permission_id: str
    mcp_servers: list[str] | None = []
    mcp_access_groups: list[str] | None = []
    mcp_tool_permissions: dict[str, list[str]] | None = None
    vector_stores: list[str] | None = []
    agents: list[str] | None = []
    agent_access_groups: list[str] | None = []
    models: list[str] | None = []
    mcp_toolsets: list[str] | None = None
    blocked_tools: list[str] | None = []
    search_tools: list[str] | None = []
    mcp_tool_search_enabled: bool | None = None
    skills: list[str] | None = None
    apps: Sequence[str] | None = None
    mcp_auth_profiles: Mapping[str, str] | None = None

    @model_serializer(mode="wrap")
    def _omit_empty_profile_grants(  # noqa: ANN202  # retain response schema
        self, handler: Callable[["LiteLLM_ObjectPermissionTable"], Mapping[str, object]]
    ):
        result: Final = handler(self)
        if self.mcp_auth_profiles or "mcp_auth_profiles" not in result:
            return result
        return {key: value for key, value in result.items() if key != "mcp_auth_profiles"}
