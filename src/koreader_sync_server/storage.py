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

    def create_or_update_user(self, username: str, password: str) -> bool:
        """Create a new user or update password for existing user."""
        with self.lock:
            users = self.data["users"]
            existing_docs = users.get(username, {}).get("documents", {})
            users[username] = {"password": password, "documents": existing_docs}
            logger.info("User '%s' registered/updated in state storage", username)
            self._save()
            return True

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

        clean_user = username.strip()
        clean_pass = password.strip()

        with self.lock:
            user_data = self.data["users"].get(clean_user)
            if not user_data:
                return False, f"Authentication failure for '{clean_user}' (UNE - User Not Exist)"

            stored_pass = str(user_data.get("password", ""))
            if not self._verify_password(clean_pass, stored_pass):
                return False, f"Authentication failure for '{clean_user}' (PWF - Password Mismatch)"

            return True, None

    @staticmethod
    def _verify_password(given_key: str, stored_password: str) -> bool:
        if given_key == stored_password:
            return True
        import hashlib

        # Check SHA-256 of stored password
        if hashlib.sha256(stored_password.encode("utf-8")).hexdigest() == given_key:
            return True
        # Check MD5 of stored password
        if hashlib.md5(stored_password.encode("utf-8")).hexdigest() == given_key:
            return True
        # Check if given_key SHA-256 matches stored password
        if hashlib.sha256(given_key.encode("utf-8")).hexdigest() == stored_password:
            return True
        # Check if given_key MD5 matches stored password
        if hashlib.md5(given_key.encode("utf-8")).hexdigest() == stored_password:
            return True
        return False

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
