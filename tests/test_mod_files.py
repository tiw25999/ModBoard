import hashlib

import pytest

from app.services import mod_files
from app.services.mod_files import UploadTooLarge


class FakeUpload:
    """Minimal stand-in for Starlette's UploadFile: async chunked read."""
    def __init__(self, data: bytes, chunk: int = 7):
        self._data = data
        self._pos = 0
        self._chunk = chunk

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._data) - self._pos
        out = self._data[self._pos:self._pos + min(n, self._chunk)]
        self._pos += len(out)
        return out


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(mod_files.settings, "mod_files_dir", str(tmp_path))
    yield


@pytest.mark.asyncio
async def test_stream_save_writes_bytes_and_hashes():
    data = b"hello mod world" * 1000
    up = FakeUpload(data)
    stored_path, size, sha = await mod_files.stream_save(42, "Cool Mod v1.zip", up, max_bytes=10_000_000)
    assert size == len(data)
    assert sha == hashlib.sha256(data).hexdigest()
    with open(stored_path, "rb") as f:
        assert f.read() == data
    # stored under the per-mod directory, with a uuid name keeping the extension
    assert "/42/" in stored_path.replace("\\", "/")
    assert stored_path.endswith(".zip")


@pytest.mark.asyncio
async def test_stream_save_rejects_over_cap_and_removes_partial():
    up = FakeUpload(b"x" * 5000)
    with pytest.raises(UploadTooLarge):
        await mod_files.stream_save(7, "big.bin", up, max_bytes=1000)
    # no leftover file in the mod dir
    mdir = mod_files.mod_dir(7)
    leftovers = list(mdir.glob("*")) if mdir.exists() else []
    assert leftovers == [], f"partial file not cleaned up: {leftovers}"


@pytest.mark.asyncio
async def test_resolve_download_path_accepts_in_root():
    up = FakeUpload(b"data")
    stored_path, _, _ = await mod_files.stream_save(1, "a.zip", up, max_bytes=1000)
    resolved = mod_files.resolve_download_path(stored_path)
    assert resolved.exists()


def test_resolve_download_path_rejects_escape():
    with pytest.raises(ValueError):
        mod_files.resolve_download_path("/etc/passwd")


def test_ext_strips_dangerous_suffix():
    assert mod_files._ext("normal.zip") == ".zip"
    assert mod_files._ext("no-extension") == ""
    assert mod_files._ext("evil/../x") == ""
