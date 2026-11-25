from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import unicodedata
from pathlib import Path

import pytest

from koreader_sync_server.hasher import filename_md5, partial_koreader_md5
from koreader_sync_server.library import CATALOG_FILENAME, LibraryResolver


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


def test_catalog_enrichment_and_atomic_hot_reload(tmp_path: Path) -> None:
    series_dir = tmp_path / "Philosophy"
    series_dir.mkdir()
    # Use NFD on disk path vs NFC in catalog to verify NFC normalization
    nfd_name = unicodedata.normalize("NFD", "Author - Über Book.epub")
    book_file = series_dir / nfd_name
    book_file.write_bytes(b"Ebook payload for catalog test" * 100)

    content_hash = partial_koreader_md5(book_file)
    assert content_hash is not None
    fn_hash = filename_md5(book_file.name)

    uuid_1 = "11111111-2222-3333-4444-555555555555"
    catalog_path = tmp_path / CATALOG_FILENAME
    catalog_path.write_text(
        json.dumps(
            {
                "version": 1,
                "books": {
                    uuid_1: {
                        "title": "Über Book",
                        "authors": ["Author Name"],
                        "series": "Philosophy",
                        "files": [
                            {
                                "rel_path": unicodedata.normalize("NFC", "Philosophy/Author - Über Book.epub"),
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

    resolver = LibraryResolver(repo_dir=tmp_path, watchdog_enabled=True)
    resolver.start()
    try:
        info = resolver.resolve_hash(content_hash)
        assert info is not None
        assert info.book_uuid == uuid_1
        assert info.title == "Über Book"
        assert info.authors == ["Author Name"]
        assert info.series == "Philosophy"

        # Atomic replace of catalog (simulates os.replace in knrs)
        uuid_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        tmp_catalog = tmp_path / ".knrs-catalog.json.tmp"
        tmp_catalog.write_text(
            json.dumps(
                {
                    "version": 1,
                    "books": {
                        uuid_2: {
                            "title": "Updated Book Title",
                            "authors": ["Author One", "Author Two"],
                            "series": "Science",
                            "files": [
                                {
                                    "rel_path": unicodedata.normalize("NFC", "Philosophy/Author - Über Book.epub"),
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
        os.replace(tmp_catalog, catalog_path)

        # Wait briefly for watchdog event
        deadline = time.time() + 3.0
        while time.time() < deadline:
            updated = resolver.resolve_hash(content_hash)
            if updated and updated.book_uuid == uuid_2:
                break
            time.sleep(0.1)

        updated = resolver.resolve_hash(content_hash)
        assert updated is not None
        assert updated.book_uuid == uuid_2
        assert updated.title == "Updated Book Title"
        assert updated.authors == ["Author One", "Author Two"]
        assert updated.series == "Science"
    finally:
        resolver.stop()
