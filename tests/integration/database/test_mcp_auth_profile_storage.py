import os
from pathlib import Path
from typing import Final

import psycopg

MIGRATION: Final = (
    Path(__file__).resolve().parents[3]
    / "litellm/proxy-extras/litellm_proxy_extras/migrations/20261121010000_mcp_auth_profile_storage/migration.sql"
)


def test_profile_migration_preserves_existing_rows_and_accepts_legacy_inserts() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute('CREATE TEMP TABLE "LiteLLM_MCPServerTable" (server_id text PRIMARY KEY)')
        connection.execute('CREATE TEMP TABLE "LiteLLM_ObjectPermissionTable" (object_permission_id text PRIMARY KEY)')
        connection.execute("INSERT INTO \"LiteLLM_MCPServerTable\" (server_id) VALUES ('original')")
        connection.execute("INSERT INTO \"LiteLLM_ObjectPermissionTable\" (object_permission_id) VALUES ('original')")

        connection.execute(MIGRATION.read_text())
        connection.execute("INSERT INTO \"LiteLLM_MCPServerTable\" (server_id) VALUES ('later')")
        connection.execute("INSERT INTO \"LiteLLM_ObjectPermissionTable\" (object_permission_id) VALUES ('later')")

        servers: Final = connection.execute(
            'SELECT server_id, additional_auth, pg_typeof(additional_auth)::text FROM "LiteLLM_MCPServerTable" ORDER BY 1'
        ).fetchall()
        permissions: Final = connection.execute(
            "SELECT object_permission_id, mcp_auth_profiles, pg_typeof(mcp_auth_profiles)::text "
            'FROM "LiteLLM_ObjectPermissionTable" ORDER BY 1'
        ).fetchall()
        assert servers == permissions == [("later", None, "jsonb"), ("original", None, "jsonb")]
