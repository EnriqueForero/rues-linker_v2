"""Indexación LSH chunk-major, determinismo y recuperación exactly-once."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import record_linkage.engine.lsh.disk_based as disk_module
from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine


class _SimulatedCrash(BaseException):
    """Interrupción abrupta que no debe convertirse en un error funcional."""


def _df(n: int = 2_600) -> pd.DataFrame:
    """Nombres reproducibles con una variante cercana por entidad."""

    rng = np.random.default_rng(11)
    alphabet = np.asarray(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    base = ["".join(rng.choice(alphabet, size=20).tolist()) for _ in range(n // 2)]
    variants = [name[:-1] + ("A" if name[-1] != "A" else "B") for name in base]
    names = base + variants
    return pd.DataFrame(
        {
            "NOMBRE_LIMPIO": names,
            "NIT_OK": [f"9{i:09d}" for i in range(len(names))],
            "SRC": ["A"] * len(names),
        }
    )


def _engine(*, chunk_size: int = 1_000) -> DiskBasedLSHEngine:
    return DiskBasedLSHEngine(
        profile={
            "lsh_permutations": 64,
            "lsh_threshold": 0.5,
            "lsh_chunk_size": chunk_size,
        }
    )


def _index_db(root: Path) -> Path:
    return root / "lsh_disk_cache" / "lsh_index.db"


def test_construye_un_solo_indice_compuesto(tmp_path: Path) -> None:
    engine = _engine()
    engine.find_candidates(_df(), output_dir=str(tmp_path))

    with sqlite3.connect(_index_db(tmp_path)) as connection:
        indices = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }

    assert indices == {"idx_buckets_band_hash"}


def test_cada_registro_queda_en_todas_las_bandas(tmp_path: Path) -> None:
    engine = _engine()
    frame = _df()
    engine.find_candidates(frame, output_dir=str(tmp_path))

    with sqlite3.connect(_index_db(tmp_path)) as connection:
        n_bands = int(
            connection.execute("SELECT value FROM metadata WHERE key='total_bands'").fetchone()[0]
        )
        rows = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        by_band = connection.execute(
            "SELECT COUNT(*), COUNT(DISTINCT record_id) FROM lsh_buckets GROUP BY band_id"
        ).fetchall()

    assert rows == n_bands * len(frame)
    assert by_band == [(len(frame), len(frame))] * n_bands


def test_candidatos_deterministas_entre_tamanos_de_chunk(tmp_path: Path) -> None:
    frame = _df()
    first = _engine(chunk_size=1_000).find_candidates(frame, output_dir=str(tmp_path / "a"))
    second = _engine(chunk_size=2_600).find_candidates(frame, output_dir=str(tmp_path / "b"))

    def pairs(result: set[tuple[int, int]] | str) -> set[tuple[int, int]]:
        if isinstance(result, set):
            return {tuple(sorted(pair)) for pair in result}
        with sqlite3.connect(result) as connection:
            return {
                tuple(sorted(row))
                for row in connection.execute("SELECT idx_0, idx_1 FROM candidate_pairs")
            }

    assert pairs(first) == pairs(second)


def test_reanuda_desde_sqlite_aunque_json_este_atrasado(tmp_path: Path) -> None:
    frame = _df()
    first = _engine()
    first.find_candidates(frame, output_dir=str(tmp_path))
    cache = tmp_path / "lsh_disk_cache"

    with sqlite3.connect(cache / "lsh_index.db") as connection:
        expected_rows = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        connection.execute("DELETE FROM lsh_buckets WHERE record_id >= 1000")
        connection.execute("UPDATE metadata SET value = '1' WHERE key = 'completed_chunks'")
        connection.execute("UPDATE metadata SET value = '0' WHERE key = 'is_complete'")
        connection.commit()

    (cache / "checkpoint.json").write_text(
        json.dumps(
            {
                "completed_chunks": 0,
                "total_chunks": 3,
                "n_records": len(frame),
                "index_fp": first._index_fp,
                "index_layout": "chunk-major-v1",
                "version": first.VERSION,
            }
        ),
        encoding="utf-8",
    )

    resumed = _engine()
    resumed.find_candidates(frame, output_dir=str(tmp_path))

    with sqlite3.connect(cache / "lsh_index.db") as connection:
        rows = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        duplicates = connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT band_id, record_id FROM lsh_buckets "
            "GROUP BY band_id, record_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]

    assert resumed.metrics.resumed_from_checkpoint is True
    assert rows == expected_rows
    assert duplicates == 0


def test_caida_despues_del_commit_antes_del_json_no_duplica(tmp_path: Path) -> None:
    frame = _df()
    interrupted = _engine()

    def crash_before_json(_data: dict[str, object]) -> None:
        raise _SimulatedCrash

    interrupted._save_checkpoint = crash_before_json  # type: ignore[method-assign]
    with pytest.raises(_SimulatedCrash):
        interrupted.find_candidates(frame, output_dir=str(tmp_path))

    cache = tmp_path / "lsh_disk_cache"
    with sqlite3.connect(cache / "lsh_index.db") as connection:
        committed = int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='completed_chunks'"
            ).fetchone()[0]
        )
        rows_after_crash = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        n_bands = int(
            connection.execute("SELECT value FROM metadata WHERE key='n_bands'").fetchone()[0]
        )

    assert committed == 1
    assert rows_after_crash == n_bands * 1_000

    resumed = _engine()
    resumed.find_candidates(frame, output_dir=str(tmp_path))
    with sqlite3.connect(cache / "lsh_index.db") as connection:
        final_rows = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]
        duplicates = connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT band_id, record_id FROM lsh_buckets "
            "GROUP BY band_id, record_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]

    assert resumed.metrics.resumed_from_checkpoint is True
    assert final_rows == n_bands * len(frame)
    assert duplicates == 0


def test_caida_dentro_del_chunk_revierte_filas_y_progreso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _df(1_200)
    real_hash = disk_module._hash_rows_stable
    calls = 0

    def fail_on_second_band(values: np.ndarray) -> np.ndarray:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise _SimulatedCrash
        return real_hash(values)

    monkeypatch.setattr(disk_module, "_hash_rows_stable", fail_on_second_band)
    with pytest.raises(_SimulatedCrash):
        _engine().find_candidates(frame, output_dir=str(tmp_path))

    cache = tmp_path / "lsh_disk_cache"
    with sqlite3.connect(cache / "lsh_index.db") as connection:
        progress = int(
            connection.execute(
                "SELECT value FROM metadata WHERE key='completed_chunks'"
            ).fetchone()[0]
        )
        rows = connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0]

    assert progress == 0
    assert rows == 0

    monkeypatch.setattr(disk_module, "_hash_rows_stable", real_hash)
    completed = _engine()
    completed.find_candidates(frame, output_dir=str(tmp_path))
    with sqlite3.connect(cache / "lsh_index.db") as connection:
        n_bands = int(
            connection.execute("SELECT value FROM metadata WHERE key='n_bands'").fetchone()[0]
        )
        assert connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone()[0] == (
            n_bands * len(frame)
        )
