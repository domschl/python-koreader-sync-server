from __future__ import annotations

import tempfile
from pathlib import Path

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
