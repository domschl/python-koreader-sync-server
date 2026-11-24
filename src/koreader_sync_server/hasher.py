from __future__ import annotations

import hashlib
import logging
import os
import stat
from pathlib import Path

logger = logging.getLogger(__name__)


def filename_md5(filename: str) -> str:
    """Calculate the standard MD5 hash of a UTF-8 filename string."""
    return hashlib.md5(filename.encode("utf-8")).hexdigest()


def partial_koreader_md5(filepath: str | Path) -> str | None:
    """Calculate the partial MD5 used by KOReader to identify document content.

    Algorithm matches KOReader frontend/util.lua sample calculation:
    Reads 1024-byte chunks at offsets 1024 << (2 * i) for i in range(-1, 11).
    """
    path = Path(filepath)
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            return None
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
    except OSError as e:
        logger.warning("Failed to compute partial MD5 for '%s': %s", path, e)
        return None
    except Exception as e:
        logger.warning("Unexpected error computing partial MD5 for '%s': %s", path, e)
        return None
