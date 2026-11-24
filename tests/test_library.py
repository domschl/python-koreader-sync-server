from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

import pytest

from koreader_sync_server.hasher import filename_md5, partial_koreader_md5
from koreader_sync_server.library import LibraryResolver


def test_library_resolver_and_watchdog() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        book1 = tmp_path / "book1.epub"
        book1.write_bytes(b"Sample ebook content for book 1" * 50)

        resolver = LibraryResolver(repo_dir=tmp_path, watchdog_enabled=True)
        resolver.start()

        try:
            fn_hash = filename_md5("book1.epub")
            info = resolver.resolve_hash(fn_hash)
            assert info is not None
            assert info.rel_path == "book1.epub"

            content_hash = partial_koreader_md5(book1)
            assert content_hash is not None
            info_content = resolver.resolve_hash(content_hash)
            assert info_content is not None
            assert info_content.rel_path == "book1.epub"

            # Dynamically create new file and verify watchdog picks it up
            book2 = tmp_path / "book2.pdf"
            book2.write_bytes(b"PDF ebook content" * 50)
            time.sleep(0.5)  # give watchdog time to catch event

            fn_hash2 = filename_md5("book2.pdf")
            info2 = resolver.resolve_hash(fn_hash2)
            assert info2 is not None
            assert info2.rel_path == "book2.pdf"

            # Delete file and verify removal
            book2.unlink()
            time.sleep(0.5)
            assert resolver.resolve_hash(fn_hash2) is None

        finally:
            resolver.stop()


def test_library_resolver_warns_on_unreadable_content_hash(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    if os.geteuid() == 0:
        pytest.skip("Root ignores file permission checks")

    subdir = tmp_path / "Science Fiction"
    subdir.mkdir()
    book = subdir / "book.epub"
    book.write_bytes(b"Unreadable ebook content" * 50)
    subdir.chmod(0o444)

    try:
        resolver = LibraryResolver(repo_dir=tmp_path, watchdog_enabled=False)
        with caplog.at_level(logging.WARNING, logger="koreader_sync_server"):
            resolver.rescan()

        # Filename hash is indexed, but warning is emitted for missing binary content hash
        assert resolver.resolve_hash(filename_md5("book.epub")) is not None
        assert any(
            "Failed to compute binary content hash for 1 of 1 ebook files" in rec.message
            for rec in caplog.records
        )
    finally:
        subdir.chmod(0o755)
