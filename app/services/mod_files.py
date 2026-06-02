"""File storage for self-hosted mod uploads.

All on-disk filenames are UUIDs under a per-mod directory; the original
filename is kept only in the DB. Download serving re-validates that the
stored path lives under MOD_FILES_DIR (path-traversal guard).
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from app.config import settings

CHUNK = 1024 * 1024  # 1 MiB


class UploadTooLarge(Exception):
    """Raised when an upload exceeds the configured size cap."""


def mod_dir(mod_id: int) -> Path:
    return Path(settings.mod_files_dir) / str(mod_id)


def _ext(filename: str) -> str:
    """Safe extension suffix only. Empty string if missing or suspicious."""
    suffix = Path(filename).suffix
    if not suffix or len(suffix) > 16 or "/" in suffix or "\\" in suffix or ".." in suffix:
        return ""
    return suffix


async def stream_save(mod_id: int, filename: str, upload, max_bytes: int) -> tuple[str, int, str]:
    """Stream an UploadFile-like object to disk. Returns (stored_path, size, sha256).

    Raises UploadTooLarge (after deleting the partial file) if the stream
    exceeds max_bytes. `upload` must expose `async read(n) -> bytes`.
    """
    d = mod_dir(mod_id)
    d.mkdir(parents=True, exist_ok=True)
    stored = d / f"{uuid.uuid4().hex}{_ext(filename)}"
    h = hashlib.sha256()
    size = 0
    try:
        with open(stored, "wb") as f:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLarge()
                h.update(chunk)
                f.write(chunk)
    except BaseException:
        stored.unlink(missing_ok=True)
        raise
    return str(stored), size, h.hexdigest()


def resolve_download_path(stored_path: str) -> Path:
    """Resolve stored_path and assert it lives under MOD_FILES_DIR.

    Never trust the DB value directly — re-validate before opening.
    Raises ValueError if the path escapes the storage root.
    """
    base = Path(settings.mod_files_dir).resolve()
    p = Path(stored_path).resolve()
    if p != base and base not in p.parents:
        raise ValueError("path escapes storage root")
    return p


def delete_file(stored_path: str) -> None:
    """Best-effort delete of a stored file. Silent if path is invalid/missing."""
    try:
        resolve_download_path(stored_path).unlink(missing_ok=True)
    except ValueError:
        pass
