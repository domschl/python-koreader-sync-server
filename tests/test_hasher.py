from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pytest

from koreader_sync_server.hasher import filename_md5, partial_koreader_md5


def test_filename_md5() -> None:
    filename = "Literature/Fiction/Book.epub"
    h = filename_md5(filename)
    assert isinstance(h, str)
    assert len(h) == 32
    assert h == filename_md5(filename)  # deterministic


def test_partial_koreader_md5() -> None:
    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp.write(b"Hello KOReader sync test content " * 100)
        tmp_path = Path(tmp.name)

    try:
        h = partial_koreader_md5(tmp_path)
        assert isinstance(h, str)
        assert len(h) == 32
    finally:
        tmp_path.unlink(missing_ok=True)


def test_partial_koreader_md5_logs_warning_on_unreadable_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    if os.geteuid() == 0:
        pytest.skip("Root ignores file permission checks")

    unreadable = tmp_path / "secret.epub"
    unreadable.write_bytes(b"Secret ebook content" * 50)
    unreadable.chmod(0o000)

    try:
        with caplog.at_level(logging.WARNING, logger="koreader_sync_server.hasher"):
            result = partial_koreader_md5(unreadable)

        assert result is None
        assert any("Failed to compute partial MD5" in rec.message for rec in caplog.records)
    finally:
        unreadable.chmod(0o644)
