from functools import partial
from pathlib import Path
from typing import Final

import pytest
from integration._support.oauth_server import oauth_server
from integration._support.redis_process import owned_redis
from pydantic import SecretStr

from litellm.caching.dual_cache import DualCache
from litellm.caching.redis_cache import RedisCache
from litellm.proxy.common_utils.encrypt_decrypt_utils import decrypt_value_helper, encrypt_value_helper
from litellm.proxy.mcp_server.outbound_credentials.client_credentials import ClientCredentialsTokenSource
from litellm.proxy.mcp_server.outbound_credentials.dual_cache_token_backend import DualCacheTokenCacheBackend
from litellm.proxy.mcp_server.outbound_credentials.result import Ok
from litellm.proxy.mcp_server.outbound_credentials.token_cache_codec import OAuthTokenCacheCodec
from litellm.proxy.mcp_server.outbound_credentials.types import ClientCredentialsConfig


@pytest.mark.asyncio
async def test_fresh_sources_share_profile_tokens_and_refresh_only_the_rejected_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_SALT_KEY", "profile-cache-integration")
    codec: Final = OAuthTokenCacheCodec(encrypt_value_helper, partial(decrypt_value_helper, key="m2m"))
    with oauth_server() as issuer, owned_redis(tmp_path) as redis:
        issuer.clients["app"] = "secret"
        config: Final = ClientCredentialsConfig(
            token_url=issuer.issuer + "/token", client_id="app", client_secret=SecretStr("secret")
        )
        shared: Final = RedisCache(host=redis.host, port=redis.port)

        def source() -> ClientCredentialsTokenSource:
            cache: Final = DualCache(redis_cache=shared)
            return ClientCredentialsTokenSource(backend=DualCacheTokenCacheBackend(cache, codec))

        try:
            writer: Final = source()
            default: Final = await writer.get("server", config)
            named: Final = await writer.get("server", config, profile_id="bot")
            assert isinstance(default, Ok) and isinstance(named, Ok)
            assert default.ok.access_token != named.ok.access_token
            fresh: Final = await source().refetch("server", config, named.ok.access_token, profile_id="BOT")
            assert fresh and fresh not in (default.ok.access_token, named.ok.access_token)

            reader: Final = source()
            for profile, expected in ((None, default.ok.access_token), ("bot", fresh)):
                result: Final = await reader.get("server", config, profile_id=profile)
                assert isinstance(result, Ok) and result.ok.access_token == expected
            assert len(issuer.token_requests()) == 3
        finally:
            await shared.disconnect()
