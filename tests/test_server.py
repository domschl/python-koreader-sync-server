from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from koreader_sync_server.biograph import BiographClient
from koreader_sync_server.config import BiographConfig, Config, LibraryConfig, ServerConfig, StorageConfig, UserConfig
from koreader_sync_server.library import LibraryResolver
from koreader_sync_server.server import KOSyncServer
from koreader_sync_server.storage import StorageManager


@pytest.fixture
def test_config(tmp_path: Path) -> Config:
    state_file = tmp_path / "state.json"
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    return Config(
        server=ServerConfig(bind_address="127.0.0.1", port=0),
        user=UserConfig(username="testuser", password="testpassword"),
        storage=StorageConfig(state_file=state_file),
        library=LibraryConfig(ebook_repo=repo_dir, watchdog_enabled=False),
        biograph=BiographConfig(enabled=False),
    )


@pytest.mark.asyncio
async def test_kosync_endpoints(test_config: Config) -> None:
    storage = StorageManager(test_config.storage.state_file)
    storage.ensure_user(test_config.user.username, test_config.user.password)

    library = LibraryResolver(test_config.library.ebook_repo, watchdog_enabled=False)
    library.start()

    biograph = BiographClient(test_config.biograph)
    await biograph.start()

    server_inst = KOSyncServer(test_config, storage, library, biograph)
    app = server_inst.app

    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. GET /
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "KOSync server" in text

        # 2. GET /healthcheck
        resp = await client.get("/healthcheck")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"state": "OK"}

        # 3. GET /users/auth without headers (Unauthorized/Bad request)
        resp = await client.get("/users/auth")
        assert resp.status in (400, 401)

        # 4. GET /users/auth with invalid headers
        resp = await client.get("/users/auth", headers={"x-auth-user": "testuser", "x-auth-key": "wrongpass"})
        assert resp.status == 401

        # 5. GET /users/auth with valid headers (raw password)
        auth_headers = {"x-auth-user": "testuser", "x-auth-key": "testpassword"}
        resp = await client.get("/users/auth", headers=auth_headers)
        assert resp.status == 200
        data = await resp.json()
        assert data == {"authorized": "OK"}

        # 5b. GET /users/auth with SHA256 hashed password
        import hashlib
        sha_key = hashlib.sha256(b"testpassword").hexdigest()
        sha_headers = {"x-auth-user": "testuser", "x-auth-key": sha_key}
        resp = await client.get("/users/auth", headers=sha_headers)
        assert resp.status == 200
        assert (await resp.json()) == {"authorized": "OK"}

        # 6. GET /syncs/progress/doc123 (should return initial 0 progress)
        resp = await client.get("/syncs/progress/doc123", headers=auth_headers)
        assert resp.status == 200
        progress_data = await resp.json()
        assert progress_data["document"] == "doc123"
        assert progress_data["percentage"] == 0.0

        # 7. PUT /syncs/progress update progress
        put_payload = {
            "document": "doc123",
            "progress": "/body/page[10]",
            "percentage": 0.45,
            "device": "Kindle Oasis",
            "device_id": "k123",
            "timestamp": 1722245000,
        }
        resp = await client.put("/syncs/progress", headers=auth_headers, json=put_payload)
        assert resp.status == 200
        put_resp = await resp.json()
        assert put_resp["document"] == "doc123"
        assert "timestamp" in put_resp

        # 8. GET /syncs/progress/doc123 again and verify updated progress
        resp = await client.get("/syncs/progress/doc123", headers=auth_headers)
        assert resp.status == 200
        get_resp = await resp.json()
        assert get_resp["percentage"] == 0.45
        assert get_resp["progress"] == "/body/page[10]"

    finally:
        await client.close()
        await biograph.stop()
        library.stop()
