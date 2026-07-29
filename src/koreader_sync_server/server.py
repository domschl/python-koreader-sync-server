from __future__ import annotations

import asyncio
import logging
import ssl
import time
from typing import Any

from aiohttp import web

from koreader_sync_server.biograph import BiographClient
from koreader_sync_server.config import Config
from koreader_sync_server.library import LibraryResolver
from koreader_sync_server.storage import StorageManager

logger = logging.getLogger(__name__)


class KOSyncServer:
    """Async web server implementing the KOReader sync REST protocol."""

    def __init__(
        self,
        config: Config,
        storage: StorageManager,
        library: LibraryResolver,
        biograph: BiographClient,
    ) -> None:
        self.config = config
        self.storage = storage
        self.library = library
        self.biograph = biograph
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/", self.handle_root)
        self.app.router.add_get("/healthcheck", self.handle_healthcheck)
        self.app.router.add_post("/users/create", self.handle_user_create)
        self.app.router.add_get("/users/auth", self.handle_user_auth)
        self.app.router.add_get("/syncs/progress/{document}", self.handle_get_progress)
        self.app.router.add_put("/syncs/progress", self.handle_put_progress)

    def _auth(self, request: web.Request) -> tuple[web.Response | None, str | None]:
        headers = request.headers
        username = headers.get("x-auth-user")
        password = headers.get("x-auth-key")

        valid, err_msg = self.storage.authenticate(username, password)
        if not valid:
            logger.warning("Auth failure from %s: %s (x-auth-user: '%s', x-auth-key length: %d)", request.remote, err_msg, username, len(password or ""))
            status_code = 401 if "PWF" in (err_msg or "") else 400
            return web.json_response({"error": err_msg}, status=status_code), None
        return None, username.strip() if username else None

    async def handle_root(self, request: web.Request) -> web.Response:
        return web.Response(text="KOSync server", content_type="text/plain")

    async def handle_healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"state": "OK"}, status=200)

    async def handle_user_create(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            return web.json_response({"error": "Username and password required"}, status=400)

        clean_user = username.strip()
        clean_pass = password.strip()
        self.storage.create_or_update_user(clean_user, clean_pass)

        logger.info("User '%s' registered/updated via /users/create", clean_user)
        return web.json_response({"username": clean_user}, status=201)

    async def handle_user_auth(self, request: web.Request) -> web.Response:
        err_resp, username = self._auth(request)
        if err_resp:
            return err_resp
        return web.json_response({"authorized": "OK"}, status=200)

    async def handle_get_progress(self, request: web.Request) -> web.Response:
        err_resp, username = self._auth(request)
        if err_resp or not username:
            return err_resp or web.json_response({"error": "Unauthorized"}, status=401)

        document_hash = request.match_info.get("document", "")
        logger.info("Get progress request from user '%s' for doc hash: %s", username, document_hash)

        resolved_info = self.library.resolve_hash(document_hash)
        if resolved_info:
            logger.info("Resolved document hash %s -> filename: %s", document_hash, resolved_info.rel_path)

        progress_data = self.storage.get_progress(username, document_hash)
        return web.json_response(progress_data, status=200)

    async def handle_put_progress(self, request: web.Request) -> web.Response:
        err_resp, username = self._auth(request)
        if err_resp or not username:
            return err_resp or web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON payload"}, status=400)

        document_hash = data.get("document")
        if not document_hash:
            return web.json_response({"error": "Missing document field"}, status=400)

        logger.info("Put progress request from user '%s' for doc %s: %s", username, document_hash, data)

        self.storage.put_progress(username, document_hash, data)

        resolved_info = self.library.resolve_hash(document_hash)
        rel_filename = resolved_info.rel_path if resolved_info else None

        progress_str = str(data.get("progress", ""))
        percentage = float(data.get("percentage", 0.0))
        device = str(data.get("device", "unknown"))
        device_id = str(data.get("device_id", "unknown"))
        ts = data.get("timestamp", time.time())

        # Forward reading progress event to Biograph asynchronously
        asyncio.create_task(
            self.biograph.send_progress_event(
                username=username,
                document_hash=document_hash,
                filename=rel_filename,
                progress=progress_str,
                percentage=percentage,
                device=device,
                device_id=device_id,
                client_timestamp=ts,
            )
        )

        now_ts = int(time.time())
        return web.json_response({"document": document_hash, "timestamp": now_ts}, status=200)

    async def run(self) -> None:
        """Run the server."""
        runner = web.AppRunner(self.app)
        await runner.setup()

        ssl_ctx: ssl.SSLContext | None = None
        if self.config.server.tls:
            if not self.config.server.cert_file or not self.config.server.key_file:
                logger.error("TLS enabled but cert_file or key_file not configured!")
            else:
                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_ctx.load_cert_chain(self.config.server.cert_file, self.config.server.key_file)

        site = web.TCPSite(
            runner,
            host=self.config.server.bind_address,
            port=self.config.server.port,
            ssl_context=ssl_ctx,
        )
        await site.start()
        proto = "https" if ssl_ctx else "http"
        logger.info("KOReader Sync Server listening on %s://%s:%d", proto, self.config.server.bind_address, self.config.server.port)

        # Keep server running until cancelled
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()
