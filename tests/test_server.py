from __future__ import annotations

import asyncio
import json
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


@pytest.mark.asyncio
async def test_cross_hash_correlation(test_config: Config) -> None:
    # 1. Create a dummy ebook in repo
    book_file = test_config.library.ebook_repo / "testbook.epub"
    book_file.write_bytes(b"Test book content for correlation test" * 50)

    storage = StorageManager(test_config.storage.state_file)
    storage.ensure_user(test_config.user.username, test_config.user.password)

    library = LibraryResolver(test_config.library.ebook_repo, watchdog_enabled=False)
    library.start()

    biograph = BiographClient(test_config.biograph)
    await biograph.start()

    server_inst = KOSyncServer(test_config, storage, library, biograph)
    client = TestClient(TestServer(server_inst.app))
    await client.start_server()

    try:
        from koreader_sync_server.hasher import filename_md5, partial_koreader_md5

        fn_hash = filename_md5("testbook.epub")
        content_hash = partial_koreader_md5(book_file)
        assert content_hash is not None

        auth_headers = {"x-auth-user": "testuser", "x-auth-key": "testpassword"}

        # Client A updates progress using content hash
        put_payload = {
            "document": content_hash,
            "progress": "/body/p[5]",
            "percentage": 0.50,
            "device": "DeviceA",
            "device_id": "devA",
            "timestamp": 1722250000,
        }
        resp = await client.put("/syncs/progress", headers=auth_headers, json=put_payload)
        assert resp.status == 200

        # Client B fetches progress using filename hash
        resp = await client.get(f"/syncs/progress/{fn_hash}", headers=auth_headers)
        assert resp.status == 200
        get_data = await resp.json()
        assert get_data["document"] == fn_hash
        assert get_data["percentage"] == 0.50
        assert get_data["progress"] == "/body/p[5]"

    finally:
        await client.close()
        await biograph.stop()
        library.stop()


@pytest.mark.asyncio
async def test_put_progress_enriches_biograph_event_from_catalog(tmp_path: Path) -> None:
    from koreader_sync_server.hasher import filename_md5, partial_koreader_md5
    from koreader_sync_server.library import CATALOG_FILENAME

    received_events: list[dict] = []

    async def fake_biograph_events(request: web.Request) -> web.Response:
        received_events.append(await request.json())
        return web.json_response({"id": 1}, status=201)

    bio_app = web.Application()
    bio_app.router.add_post("/events/", fake_biograph_events)
    bio_server = TestServer(bio_app)
    await bio_server.start_server()

    repo_dir = tmp_path / "repo"
    series_dir = repo_dir / "Science Fiction"
    series_dir.mkdir(parents=True)
    book_file = series_dir / "Frank Herbert - Dune.epub"
    book_file.write_bytes(b"Dune epub bytes" * 80)

    content_hash = partial_koreader_md5(book_file)
    assert content_hash is not None
    fn_hash = filename_md5(book_file.name)
    book_uuid = "99999999-8888-7777-6666-555555555555"

    (repo_dir / CATALOG_FILENAME).write_text(
        json.dumps(
            {
                "version": 1,
                "books": {
                    book_uuid: {
                        "title": "Dune",
                        "authors": ["Frank Herbert"],
                        "series": "Science Fiction",
                        "files": [
                            {
                                "rel_path": "Science Fiction/Frank Herbert - Dune.epub",
                                "format": "epub",
                                "filename_md5": fn_hash,
                                "koreader_partial_md5": content_hash,
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = Config(
        server=ServerConfig(bind_address="127.0.0.1", port=0),
        user=UserConfig(username="testuser", password="testpassword"),
        storage=StorageConfig(state_file=tmp_path / "state.json"),
        library=LibraryConfig(ebook_repo=repo_dir, watchdog_enabled=False),
        biograph=BiographConfig(enabled=True, url=str(bio_server.make_url("/")), api_token="tok"),
    )

    storage = StorageManager(cfg.storage.state_file)
    storage.ensure_user(cfg.user.username, cfg.user.password)
    library = LibraryResolver(cfg.library.ebook_repo, watchdog_enabled=False)
    library.start()
    biograph = BiographClient(cfg.biograph)
    await biograph.start()

    server_inst = KOSyncServer(cfg, storage, library, biograph)
    client = TestClient(TestServer(server_inst.app))
    await client.start_server()

    try:
        resp = await client.put(
            "/syncs/progress",
            headers={"x-auth-user": "testuser", "x-auth-key": "testpassword"},
            json={
                "document": content_hash,
                "progress": "/body/chapter[1]",
                "percentage": 0.25,
                "device": "Kobo Libra",
                "device_id": "kobo1",
                "timestamp": 1722260000,
            },
        )
        assert resp.status == 200

        # Wait briefly for background task to deliver Biograph POST
        for _ in range(20):
            if received_events:
                break
            await asyncio.sleep(0.05)

        assert len(received_events) == 1
        ev = received_events[0]
        assert ev["ev_type"] == "koreader-progress"
        assert ev["resource_uri"] == f"knrs://book/{book_uuid}"
        assert "Dune" in ev["summary"]
        content = ev["content"]
        assert content["book_uuid"] == book_uuid
        assert content["title"] == "Dune"
        assert content["authors"] == ["Frank Herbert"]
        assert content["author"] == "Frank Herbert"
        assert content["series"] == "Science Fiction"
        assert content["filename"] == "Science Fiction/Frank Herbert - Dune.epub"
    finally:
        await client.close()
        await biograph.stop()
        library.stop()
        await bio_server.close()
