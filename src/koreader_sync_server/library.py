from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from koreader_sync_server.hasher import filename_md5, partial_koreader_md5

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS: set[str] = {
    ".epub",
    ".pdf",
    ".mobi",
    ".azw3",
    ".cbz",
    ".fb2",
    ".djvu",
    ".txt",
    ".chm",
    ".pdb",
    ".rtf",
    ".doc",
    ".docx",
}


@dataclass
class EbookInfo:
    rel_path: str
    abs_path: str


class EbookWatchdogHandler(FileSystemEventHandler):
    """Watchdog event handler for monitoring ebook file changes."""

    def __init__(self, resolver: LibraryResolver) -> None:
        super().__init__()
        self.resolver = resolver

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_ebook(event.src_path):
            logger.info("Watchdog detected file creation: %s", event.src_path)
            self.resolver.add_file(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_ebook(event.src_path):
            logger.info("Watchdog detected file modification: %s", event.src_path)
            self.resolver.add_file(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and self._is_ebook(event.src_path):
            logger.info("Watchdog detected file deletion: %s", event.src_path)
            self.resolver.remove_file(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            dest_path = getattr(event, "dest_path", None)
            if self._is_ebook(event.src_path):
                self.resolver.remove_file(Path(event.src_path))
            if dest_path and self._is_ebook(dest_path):
                logger.info("Watchdog detected file move to: %s", dest_path)
                self.resolver.add_file(Path(dest_path))

    @staticmethod
    def _is_ebook(path_str: str) -> bool:
        return Path(path_str).suffix.lower() in SUPPORTED_EXTENSIONS


class LibraryResolver:
    """Maintains a hash-to-ebook reverse resolution index for an ebook directory tree."""

    def __init__(self, repo_dir: str | Path | None, watchdog_enabled: bool = True) -> None:
        self.repo_dir = Path(repo_dir).expanduser().resolve() if repo_dir else None
        self.watchdog_enabled = watchdog_enabled
        self.lock = threading.RLock()
        self.hash_to_ebook: dict[str, EbookInfo] = {}
        self.file_to_hashes: dict[str, list[str]] = {}
        self._observer: Observer | None = None

    def start(self) -> None:
        """Scan repository and start watchdog monitoring if enabled."""
        if not self.repo_dir or not self.repo_dir.exists():
            logger.warning("Ebook repository directory %s does not exist", self.repo_dir)
            return

        logger.info("Scanning ebook library at %s...", self.repo_dir)
        self.rescan()

        if self.watchdog_enabled:
            try:
                handler = EbookWatchdogHandler(self)
                self._observer = Observer()
                self._observer.schedule(handler, str(self.repo_dir), recursive=True)
                self._observer.start()
                logger.info("Ebook library watchdog started for %s", self.repo_dir)
            except Exception as e:
                logger.error("Failed to start watchdog observer: %s", e)

    def stop(self) -> None:
        """Stop the watchdog observer."""
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
                logger.info("Ebook library watchdog stopped")
            except Exception as e:
                logger.error("Error stopping watchdog observer: %s", e)
            self._observer = None

    def rescan(self) -> None:
        """Perform a full rescan of the repository."""
        if not self.repo_dir or not self.repo_dir.exists():
            return

        new_hash_to_ebook: dict[str, EbookInfo] = {}
        new_file_to_hashes: dict[str, list[str]] = {}

        for root, _, files in os.walk(self.repo_dir):
            for file_name in files:
                file_path = Path(root) / file_name
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    self._index_file(file_path, new_hash_to_ebook, new_file_to_hashes)

        with self.lock:
            self.hash_to_ebook = new_hash_to_ebook
            self.file_to_hashes = new_file_to_hashes
        logger.info("Indexed %d ebook files (%d hashes total)", len(new_file_to_hashes), len(new_hash_to_ebook))

    def add_file(self, file_path: Path) -> None:
        """Add or update a single file in the index."""
        if not self.repo_dir or not file_path.exists():
            return

        with self.lock:
            self._index_file(file_path, self.hash_to_ebook, self.file_to_hashes)

    def remove_file(self, file_path: Path) -> None:
        """Remove a single file from the index."""
        if not self.repo_dir:
            return

        abs_str = str(file_path.resolve())
        with self.lock:
            hashes = self.file_to_hashes.pop(abs_str, [])
            for h in hashes:
                self.hash_to_ebook.pop(h, None)

    def resolve_hash(self, document_hash: str) -> EbookInfo | None:
        """Look up an ebook by its content partial MD5 or filename MD5 hash."""
        with self.lock:
            return self.hash_to_ebook.get(document_hash)

    def get_alias_hashes(self, document_hash: str) -> list[str]:
        """Return all known hash aliases (filename hash, content hash) for a given document hash."""
        with self.lock:
            info = self.hash_to_ebook.get(document_hash)
            if not info:
                return [document_hash]
            hashes = self.file_to_hashes.get(info.abs_path, [document_hash])
            return list(hashes)

    def _index_file(
        self,
        file_path: Path,
        hash_map: dict[str, EbookInfo],
        file_map: dict[str, list[str]],
    ) -> None:
        try:
            abs_path = str(file_path.resolve())
            rel_path = str(file_path.relative_to(self.repo_dir))
        except Exception:
            return

        hashes: list[str] = []
        info = EbookInfo(rel_path=rel_path, abs_path=abs_path)

        # 1. Basename MD5 hash (KOReader standard filename sync hash)
        basename_hash = filename_md5(file_path.name)
        hash_map[basename_hash] = info
        hashes.append(basename_hash)

        # 2. Relative path MD5 hash
        relpath_hash = filename_md5(rel_path)
        if relpath_hash not in hashes:
            hash_map[relpath_hash] = info
            hashes.append(relpath_hash)

        # 3. Content partial MD5 hash (KOReader binary content sync hash)
        content_hash = partial_koreader_md5(file_path)
        if content_hash and content_hash not in hashes:
            hash_map[content_hash] = info
            hashes.append(content_hash)

        file_map[abs_path] = hashes
