"""Security and compatibility tests for the Colab disk-backed cache."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

import record_linkage.deduplication.colab as colab_module
from record_linkage.deduplication.colab import (
    ColabOptimizedManager,
    CrossChunkDeduplicationWarning,
    SQLiteJSONCache,
)


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


def test_streaming_consolidates_every_chunk_without_mutating_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresión: antes se devolvía solo results[0] y se mutaba el input."""

    class FakeManager:
        def __init__(self) -> None:
            self.temp_storage = str(tmp_path)

        def auto_configure_for_dataset(self, _n_records: int) -> dict[str, object]:
            return {"batch_size": 1, "streaming_mode": True}

        def optimize_dataframe_memory(self, df: pd.DataFrame) -> pd.DataFrame:
            df["numero"] = pd.to_numeric(df["numero"], downcast="integer")
            df["categoria"] = df["categoria"].astype("category")
            return df

    calls: list[int] = []

    def fake_deduplicate(chunk: pd.DataFrame, **_kwargs):
        calls.append(len(chunk))
        # Reordenar prueba que la consolidación usa el marcador global, no la
        # posición local devuelta por el motor.
        result = chunk.iloc[::-1].copy()
        # Cada llamada local reinicia ambos IDs, tal como hace el pipeline real.
        result["ORIGINAL_INDEX"] = range(len(result))
        result["ID_GRUPO"] = 0
        result["NIT_OK"] = result["NIT"]
        result["RECORD_COUNT"] = len(result)
        return result, result.copy()

    monkeypatch.setattr(colab_module, "ColabOptimizedManager", FakeManager)
    monkeypatch.setattr(colab_module, "deduplicate_unified", fake_deduplicate)
    monkeypatch.setattr(colab_module, "_COLAB_STREAMING_MIN_ROWS", 0)

    original = pd.DataFrame(
        {
            "NIT": ["1", "2", "3", "4", "5"],
            "RAZON_SOCIAL": ["A", "B", "C", "D", "E"],
            "numero": pd.Series([1, 2, 3, 4, 5], dtype="int64"),
            "categoria": ["x", "x", "y", "y", "z"],
        }
    )
    before = original.copy(deep=True)
    before_dtypes = original.dtypes.copy()

    with pytest.warns(CrossChunkDeduplicationWarning, match="chunks distintos"):
        correlativa, conexiones = colab_module.deduplicate_large_dataset_colab(
            original, chunk_size=2
        )

    assert calls == [2, 2, 1]
    assert len(correlativa) == len(original) == 5
    assert correlativa["ORIGINAL_INDEX"].tolist() == [0, 1, 2, 3, 4]
    assert correlativa["ID_GRUPO"].nunique() == 3
    assert correlativa["RECORD_COUNT"].tolist() == [2, 2, 2, 2, 1]
    assert len(conexiones) == 4
    assert correlativa.attrs["cross_chunk_linkage"] is False

    pd.testing.assert_frame_equal(original, before)
    pd.testing.assert_series_equal(original.dtypes, before_dtypes)
