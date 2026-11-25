from __future__ import annotations

import json
import logging
import os
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from koreader_sync_server.hasher import filename_md5, partial_koreader_md5

logger = logging.getLogger(__name__)

CATALOG_FILENAME = ".knrs-catalog.json"

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


def _norm_rel_path(rel_path: str) -> str:
    """Normalize a relative path to NFC POSIX format."""
    return unicodedata.normalize("NFC", rel_path.replace("\\", "/"))


@dataclass
class EbookInfo:
    rel_path: str
    abs_path: str
    book_uuid: str | None = None
    title: str | None = None
    authors: list[str] | None = None
    series: str | None = None


class EbookWatchdogHandler(FileSystemEventHandler):
    """Watchdog event handler for monitoring ebook and catalog file changes."""

    def __init__(self, resolver: LibraryResolver) -> None:
        super().__init__()
        self.resolver = resolver

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_catalog(event.src_path):
            logger.info("Watchdog detected catalog creation: %s", event.src_path)
            self.resolver.reload_catalog()
        elif self._is_ebook(event.src_path):
            logger.info("Watchdog detected file creation: %s", event.src_path)
            self.resolver.add_file(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_catalog(event.src_path):
            logger.info("Watchdog detected catalog modification: %s", event.src_path)
            self.resolver.reload_catalog()
        elif self._is_ebook(event.src_path):
            logger.info("Watchdog detected file modification: %s", event.src_path)
            self.resolver.add_file(Path(event.src_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if self._is_catalog(event.src_path):
            logger.info("Watchdog detected catalog deletion: %s", event.src_path)
            self.resolver.reload_catalog()
        elif self._is_ebook(event.src_path):
            logger.info("Watchdog detected file deletion: %s", event.src_path)
            self.resolver.remove_file(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest_path = getattr(event, "dest_path", None)
        if self._is_catalog(event.src_path) or (dest_path and self._is_catalog(dest_path)):
            logger.info("Watchdog detected catalog move/replace: %s", dest_path or event.src_path)
            self.resolver.reload_catalog()
            return
        if self._is_ebook(event.src_path):
            self.resolver.remove_file(Path(event.src_path))
        if dest_path and self._is_ebook(dest_path):
            logger.info("Watchdog detected file move to: %s", dest_path)
            self.resolver.add_file(Path(dest_path))

    @staticmethod
    def _is_ebook(path_str: str) -> bool:
        return Path(path_str).suffix.lower() in SUPPORTED_EXTENSIONS

    @staticmethod
    def _is_catalog(path_str: str) -> bool:
        return Path(path_str).name == CATALOG_FILENAME


class LibraryResolver:
    """Maintains a hash-to-ebook reverse resolution index for an ebook directory tree."""

    def __init__(self, repo_dir: str | Path | None, watchdog_enabled: bool = True) -> None:
        self.repo_dir = Path(repo_dir).expanduser().resolve() if repo_dir else None
        self.watchdog_enabled = watchdog_enabled
        self.lock = threading.RLock()
        self.hash_to_ebook: dict[str, EbookInfo] = {}
        self.file_to_hashes: dict[str, list[str]] = {}
        self._catalog_by_rel_path: dict[str, dict[str, Any]] = {}
        self._catalog_by_hash: dict[str, dict[str, Any]] = {}
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

    def _load_catalog_locked(self) -> bool:
        """Load .knrs-catalog.json under self.lock. Keeps previous state on parse/read errors."""
        if not self.repo_dir:
            return False

        catalog_path = self.repo_dir / CATALOG_FILENAME
        if not catalog_path.exists():
            self._catalog_by_rel_path = {}
            self._catalog_by_hash = {}
            return True

        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load catalog %s (keeping previous catalog): %s", catalog_path, exc)
            return False

        if not isinstance(raw, dict):
            logger.warning("Invalid catalog format in %s (expected JSON object)", catalog_path)
            return False

        books = raw.get("books")
        if not isinstance(books, dict):
            logger.warning("Invalid catalog 'books' section in %s", catalog_path)
            return False

        new_by_rel: dict[str, dict[str, Any]] = {}
        new_by_hash: dict[str, dict[str, Any]] = {}

        for book_uuid, book in books.items():
            if not isinstance(book, dict):
                continue
            authors_raw = book.get("authors")
            authors = [str(a) for a in authors_raw] if isinstance(authors_raw, list) else []
            title = str(book["title"]) if book.get("title") is not None else None
            series = str(book["series"]) if book.get("series") is not None else None

            for file_entry in book.get("files") or []:
                if not isinstance(file_entry, dict):
                    continue
                rel_raw = file_entry.get("rel_path")
                if not rel_raw:
                    continue
                norm_rel = _norm_rel_path(str(rel_raw))
                meta: dict[str, Any] = {
                    "book_uuid": str(book_uuid),
                    "title": title,
                    "authors": authors,
                    "series": series,
                    "rel_path": norm_rel,
                }
                new_by_rel[norm_rel] = meta
                for hkey in ("filename_md5", "koreader_partial_md5"):
                    hval = file_entry.get(hkey)
                    if hval:
                        new_by_hash[str(hval)] = meta

        self._catalog_by_rel_path = new_by_rel
        self._catalog_by_hash = new_by_hash
        logger.info("Loaded catalog %s (%d files, %d hashes)", catalog_path, len(new_by_rel), len(new_by_hash))
        return True

    def _lookup_catalog_meta(
        self,
        rel_path: str | None = None,
        hashes: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if rel_path:
            meta = self._catalog_by_rel_path.get(_norm_rel_path(rel_path))
            if meta:
                return meta
        if hashes:
            for h in hashes:
                meta = self._catalog_by_hash.get(h)
                if meta:
                    return meta
        return None

    def _register_catalog_only_hashes_locked(self, hash_map: dict[str, EbookInfo]) -> None:
        if not self.repo_dir:
            return
        for hval, meta in self._catalog_by_hash.items():
            if hval in hash_map:
                continue
            rel = meta["rel_path"]
            candidate = self.repo_dir / rel
            abs_str = str(candidate.resolve()) if candidate.exists() else str(candidate)
            hash_map[hval] = EbookInfo(
                rel_path=rel,
                abs_path=abs_str,
                book_uuid=meta["book_uuid"],
                title=meta["title"],
                authors=list(meta["authors"]),
                series=meta["series"],
            )

    def reload_catalog(self) -> None:
        """Reload .knrs-catalog.json and refresh metadata on indexed ebooks."""
        if not self.repo_dir:
            return

        with self.lock:
            if not self._load_catalog_locked():
                return

            # Remove stale catalog-only hashes not backed by scanned physical files
            physical_hashes = {
                h for hashes in self.file_to_hashes.values() for h in hashes
            }
            for h in list(self.hash_to_ebook):
                if h not in physical_hashes and h not in self._catalog_by_hash:
                    self.hash_to_ebook.pop(h, None)

            # Refresh metadata on all existing EbookInfo entries
            for doc_hash, info in list(self.hash_to_ebook.items()):
                meta = self._lookup_catalog_meta(info.rel_path, [doc_hash])
                if meta:
                    info.book_uuid = meta["book_uuid"]
                    info.title = meta["title"]
                    info.authors = list(meta["authors"])
                    info.series = meta["series"]
                else:
                    info.book_uuid = None
                    info.title = None
                    info.authors = None
                    info.series = None

            self._register_catalog_only_hashes_locked(self.hash_to_ebook)

    def rescan(self) -> None:
        """Perform a full rescan of the repository."""
        if not self.repo_dir or not self.repo_dir.exists():
            return

        with self.lock:
            self._load_catalog_locked()

        new_hash_to_ebook: dict[str, EbookInfo] = {}
        new_file_to_hashes: dict[str, list[str]] = {}
        failed_content_hashes = 0

        def _on_walk_error(err: OSError) -> None:
            logger.warning("Failed to traverse ebook library directory '%s': %s", err.filename, err)

        for root, _, files in os.walk(self.repo_dir, onerror=_on_walk_error):
            for file_name in files:
                file_path = Path(root) / file_name
                if file_path.suffix.lower() in SUPPORTED_EXTENSIONS:
                    if not self._index_file(file_path, new_hash_to_ebook, new_file_to_hashes):
                        failed_content_hashes += 1

        with self.lock:
            self._register_catalog_only_hashes_locked(new_hash_to_ebook)
            self.hash_to_ebook = new_hash_to_ebook
            self.file_to_hashes = new_file_to_hashes
        logger.info("Indexed %d ebook files (%d hashes total)", len(new_file_to_hashes), len(new_hash_to_ebook))
        if failed_content_hashes:
            logger.warning(
                "Failed to compute binary content hash for %d of %d ebook files "
                "(binary hash matching will not resolve them; check file/directory permissions)",
                failed_content_hashes,
                len(new_file_to_hashes),
            )

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
    ) -> bool:
        try:
            abs_path = str(file_path.resolve())
            rel_path = _norm_rel_path(file_path.relative_to(self.repo_dir).as_posix())
        except Exception as e:
            logger.warning("Failed to resolve ebook path '%s': %s", file_path, e)
            return False

        hashes: list[str] = []

        # 1. Basename MD5 hash (KOReader filename matching)
        basename_hash = filename_md5(file_path.name)
        hashes.append(basename_hash)

        # 2. Content partial MD5 hash (KOReader binary content matching)
        content_hash = partial_koreader_md5(file_path)
        if content_hash and content_hash not in hashes:
            hashes.append(content_hash)

        meta = self._lookup_catalog_meta(rel_path, hashes)
        info = EbookInfo(
            rel_path=rel_path,
            abs_path=abs_path,
            book_uuid=meta["book_uuid"] if meta else None,
            title=meta["title"] if meta else None,
            authors=list(meta["authors"]) if meta else None,
            series=meta["series"] if meta else None,
        )

        for h in hashes:
            hash_map[h] = info

        file_map[abs_path] = hashes
        return content_hash is not None
