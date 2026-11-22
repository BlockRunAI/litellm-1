import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final

import psycopg
from integration._support.database import scratch_database

MIGRATION: Final = (
    Path(__file__).resolve().parents[4]
    / "litellm/proxy-extras/litellm_proxy_extras/migrations/20261121010000_mcp_auth_profile_storage/migration.sql"
)


def test_profile_migration_preserves_existing_rows_and_accepts_legacy_inserts(tmp_path: Path) -> None:
    migrations: Final = MIGRATION.parent.parent
    schema: Final = tmp_path / "schema.prisma"
    schema.write_bytes((migrations.parent / "schema.prisma").read_bytes())
    previous: Final = tmp_path / "migrations"
    previous.mkdir()
    shutil.copy(migrations / "migration_lock.toml", previous)
    for item in migrations.iterdir():
        if item.is_dir() and item.name < MIGRATION.parent.name:
            shutil.copytree(item, previous / item.name)

    with scratch_database() as url, psycopg.connect(url) as connection:
        subprocess.run(
            [sys.executable, "-I", "-m", "prisma", "migrate", "deploy", "--schema", str(schema)],
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            timeout=300,
        )
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
