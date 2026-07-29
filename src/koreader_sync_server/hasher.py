from __future__ import annotations

import hashlib
import os
from pathlib import Path


def filename_md5(filename: str) -> str:
    """Calculate the standard MD5 hash of a UTF-8 filename string."""
    return hashlib.md5(filename.encode("utf-8")).hexdigest()


def partial_koreader_md5(filepath: str | Path) -> str | None:
    """Calculate the partial MD5 used by KOReader to identify document content.

    Algorithm matches KOReader frontend/util.lua sample calculation:
    Reads 1024-byte chunks at offsets 1024 << (2 * i) for i in range(-1, 11).
    """
    path = Path(filepath)
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            step = 1024
            size = 1024
            md5_hash = hashlib.md5()
            for i in range(-1, 11):
                exponent = 2 * i
                if exponent < 0:
                    offset = 0
                else:
                    offset = step << exponent
                f.seek(offset, os.SEEK_SET)
                sample = f.read(size)
                if sample:
                    md5_hash.update(sample)
                else:
                    break
            return md5_hash.hexdigest()
    except Exception:
        return None
