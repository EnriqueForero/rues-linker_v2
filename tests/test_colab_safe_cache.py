"""Security and compatibility tests for the Colab disk-backed cache."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from record_linkage.deduplication.colab import ColabOptimizedManager, SQLiteJSONCache


def _manager(tmp_path: Path) -> ColabOptimizedManager:
    manager = ColabOptimizedManager.__new__(ColabOptimizedManager)
    manager.temp_storage = str(tmp_path)
    return manager


def test_json_cache_roundtrip_and_mutable_writeback(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    with manager.create_disk_backed_cache("safe") as cache:
        assert isinstance(cache, SQLiteJSONCache)
        cache["payload"] = {"items": [1], "active": True}
        cache["payload"]["items"].append(2)
        cache["quote'; DROP TABLE cache_entries;--"] = "safe"

    with manager.create_disk_backed_cache("safe") as reopened:
        assert reopened["payload"] == {"items": [1, 2], "active": True}
        assert reopened["quote'; DROP TABLE cache_entries;--"] == "safe"
        assert len(reopened) == 2
        del reopened["payload"]
        assert "payload" not in reopened


def test_cache_rejects_executable_or_non_json_values(tmp_path: Path) -> None:
    marker = tmp_path / "executed"

    class PickleStylePayload:
        def __reduce__(self):
            return os.system, (f"touch {marker}",)

    with _manager(tmp_path).create_disk_backed_cache("safe") as cache:
        with pytest.raises(TypeError, match="compatibles con JSON"):
            cache["payload"] = PickleStylePayload()

    assert not marker.exists()


@pytest.mark.parametrize("name", ["../escape", "sub/cache", r"sub\cache", "", ".."])
def test_cache_name_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="nombre simple"):
        _manager(tmp_path).create_disk_backed_cache(name)


def test_legacy_shelve_bytes_are_never_deserialized(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.cache"
    legacy.write_bytes(b"not-a-safe-shelve-and-never-opened")

    with _manager(tmp_path).create_disk_backed_cache("legacy") as cache:
        cache["regenerated"] = True
        assert cache.path.name == "legacy.cache.sqlite3"

    assert legacy.read_bytes() == b"not-a-safe-shelve-and-never-opened"
