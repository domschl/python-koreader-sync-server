from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Any

import aiohttp

from koreader_sync_server.config import BiographConfig

logger = logging.getLogger(__name__)


class BiographClient:
    """Async client for sending KOReader reading progress events to a Biograph server."""

    def __init__(self, config: BiographConfig) -> None:
        self.config = config
        self.hostname = socket.gethostname()
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        """Initialize the HTTP client session."""
        if self.config.enabled:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.config.api_token:
                headers["Authorization"] = f"Bearer {self.config.api_token}"
            self.session = aiohttp.ClientSession(headers=headers)
            logger.info("Biograph client initialized for %s", self.config.url)

    async def stop(self) -> None:
        """Close the HTTP client session."""
        if self.session:
            await self.session.close()
            self.session = None

    async def send_progress_event(
        self,
        username: str,
        document_hash: str,
        filename: str | None,
        progress: str,
        percentage: float,
        device: str,
        device_id: str,
        client_timestamp: int | float,
    ) -> None:
        """Send a structured reading progress event to Biograph."""
        if not self.config.enabled or not self.session:
            return

        target_url = self.config.url.rstrip("/") + "/events/"
        now_utc = datetime.now(timezone.utc).isoformat()

        display_name = filename if filename else document_hash[:10]
        pct_display = f"{percentage * 100:.1f}%" if percentage <= 1.0 else f"{percentage:.1f}%"
        summary = f"Reading progress on '{display_name}': {pct_display}"

        payload: dict[str, Any] = {
            "timestamp": now_utc,
            "ev_type": "koreader-progress",
            "source_host": self.hostname,
            "source_app": "koreader-sync-server",
            "content": {
                "user": username,
                "document_hash": document_hash,
                "filename": filename,
                "progress": progress,
                "percentage": percentage,
                "device": device,
                "device_id": device_id,
                "timestamp": client_timestamp,
            },
            "summary": summary,
        }

        try:
            async with self.session.post(target_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status in (200, 201):
                    logger.debug("Successfully forwarded reading progress event to Biograph")
                else:
                    body = await resp.text()
                    logger.warning("Biograph returned status %d: %s", resp.status, body)
        except Exception as e:
            logger.error("Failed to post event to Biograph at %s: %s", target_url, e)
