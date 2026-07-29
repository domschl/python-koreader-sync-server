from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StorageManager:
    """Thread-safe persistent storage manager for user accounts and reading progress."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file.expanduser().resolve()
        self.lock = threading.RLock()
        self.data: dict[str, Any] = {"users": {}}
        self._load()

    def _load(self) -> None:
        """Load state data from state.json if it exists."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                if "users" not in self.data:
                    self.data["users"] = {}
                logger.info("Loaded state from %s", self.state_file)
            except Exception as e:
                logger.error("Error reading state file %s: %s", self.state_file, e)
                self.data = {"users": {}}
        else:
            logger.info("State file %s does not exist, creating new state", self.state_file)

    def _save(self) -> None:
        """Atomically save state data to state.json."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.state_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error("Failed to write state file %s: %s", self.state_file, e)

    def ensure_user(self, username: str, password: str) -> None:
        """Ensure a user exists with the given credentials."""
        with self.lock:
            users = self.data["users"]
            if username not in users:
                users[username] = {"password": password, "documents": {}}
                logger.info("Created user '%s' in state storage", username)
                self._save()
            elif users[username].get("password") != password:
                users[username]["password"] = password
                logger.info("Updated password for user '%s' in state storage", username)
                self._save()

    def create_user(self, username: str, password: str) -> bool:
        """Create a new user if not present. Returns True if created, False if user exists."""
        with self.lock:
            users = self.data["users"]
            if username in users:
                return False
            users[username] = {"password": password, "documents": {}}
            self._save()
            return True

    def authenticate(self, username: str | None, password: str | None) -> tuple[bool, str | None]:
        """Authenticate user credentials. Returns (is_valid, error_code)."""
        if not username:
            return False, "No username in request"
        if not password:
            return False, "No password in request"

        with self.lock:
            user_data = self.data["users"].get(username)
            if not user_data:
                return False, "Authentication failure (UNE)"
            if user_data.get("password") != password:
                return False, "Authentication failure (PWF)"
            return True, None

    def get_progress(self, username: str, document_hash: str) -> dict[str, Any]:
        """Get reading progress for a user and document. Creates zero entry if missing."""
        with self.lock:
            user_data = self.data["users"].setdefault(username, {"password": "", "documents": {}})
            docs = user_data.setdefault("documents", {})

            if document_hash not in docs:
                progress_payload: dict[str, Any] = {
                    "document": document_hash,
                    "progress": "0",
                    "percentage": 0.0,
                    "device": "none",
                    "device_id": "none",
                    "timestamp": 0,
                }
                docs[document_hash] = progress_payload
                self._save()
                return progress_payload
            return dict(docs[document_hash])

    def put_progress(self, username: str, document_hash: str, payload: dict[str, Any]) -> None:
        """Store reading progress for a user and document."""
        with self.lock:
            user_data = self.data["users"].setdefault(username, {"password": "", "documents": {}})
            docs = user_data.setdefault("documents", {})
            docs[document_hash] = payload
            self._save()
