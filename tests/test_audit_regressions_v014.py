"""Regresiones de los hallazgos de auditoría de rues-linker 0.14.0."""

from __future__ import annotations

import io
import sqlite3
from contextlib import closing
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.clusterer import OptimizedClusterer
from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine
from record_linkage.engine.lsh.trusted import TrustedSourceLSHEngine
from record_linkage.engine.scorer import (
    MemoryManager,
    VectorizedScorer,
    _publish_sqlite_output,
)
from record_linkage.utils.output import safe_print


def test_safe_print_escapes_only_characters_unsupported_by_cp1252() -> None:
    """La salida decorativa nunca debe abortar un pipeline en Windows."""

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", newline="")
    safe_print("Resultado ✅: niño", file=stream, flush=True)
    rendered = raw.getvalue().decode("cp1252")

    assert rendered == "Resultado \\u2705: niño\n"


def test_publish_sqlite_output_uses_writable_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows exige un descriptor escribible para ``fsync``/``_commit``."""

    private = tmp_path / "scored.private.db"
    target = tmp_path / "scored.db"
    with closing(sqlite3.connect(private)) as connection:
        connection.execute("CREATE TABLE scored_pairs (idx_0 INTEGER, idx_1 INTEGER)")
        connection.execute("INSERT INTO scored_pairs VALUES (1, 2)")
        connection.commit()

    real_open = Path.open
    observed_modes: list[str] = []

    def guarded_open(path: Path, mode: str = "r", *args, **kwargs):
        if path == private:
            observed_modes.append(mode)
            assert "+" in mode or "w" in mode or "a" in mode
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    _publish_sqlite_output(private, target)

    assert observed_modes == ["rb+"]
    assert target.is_file()
    assert not private.exists()
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("SELECT * FROM scored_pairs").fetchall() == [(1, 2)]


@pytest.mark.parametrize("override", [0.5, 0.0])
def test_score_threshold_override_including_zero_is_effective(
    override: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El batch debe filtrar con el umbral de la llamada, no con el perfil."""

    monkeypatch.setattr(MemoryManager, "monitor_and_warn", lambda *_args, **_kwargs: None)
    profile = {
        "weights": {"name": 1.0, "nit": 0.0, "phonetic": 0.0},
        "score_threshold": 0.95,
        "max_nit_distance": 9,
        "min_name_similarity": 0.0,
        "veto_nit_base_distinto": False,
    }
    frame = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ALFA UNO", "ALFA DOS"],
            "NIT_OK": ["", ""],
        }
    )

    result = VectorizedScorer(profile).score_pairs({(0, 1)}, frame, score_threshold=override)

    assert len(result) == 1
    assert 0.5 < float(result.iloc[0]["score"]) < profile["score_threshold"]


def test_lsh_index_resumes_valid_checkpoint_without_cleaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una DB parcial coherente continúa desde el chunk durable en SQLite."""

    engine = DiskBasedLSHEngine(profile={"lsh_chunk_size": 1_000})
    engine._index_db_file = tmp_path / "lsh_index.db"
    engine._checkpoint_file = tmp_path / "checkpoint.json"
    engine._signatures_file = tmp_path / "signatures.h5"

    with closing(sqlite3.connect(engine._index_db_file)) as connection:
        engine._init_index_schema(connection, 4, 4, 2_500, "same-run")
        connection.execute("INSERT INTO lsh_buckets VALUES (0, 123, 0)")
        connection.execute("UPDATE metadata SET value = '1' WHERE key = 'completed_chunks'")
        connection.commit()
    with h5py.File(engine._signatures_file, "w") as signatures_file:
        signatures_file.create_dataset("signatures", data=np.zeros((2_500, 16), dtype=np.uint64))

    completeness_checks = iter([False, True])
    indexed_chunks: list[int] = []
    monkeypatch.setattr(engine, "_calculate_optimal_bands", lambda: (4, 4))
    monkeypatch.setattr(engine, "_index_run_fingerprint", lambda *_args: "same-run")
    monkeypatch.setattr(engine, "_is_index_complete", lambda *_args: next(completeness_checks))
    monkeypatch.setattr(
        engine,
        "_clean_index_files",
        lambda: pytest.fail("un checkpoint válido no debe limpiarse"),
    )
    monkeypatch.setattr(
        engine,
        "_init_index_schema",
        lambda *_args: pytest.fail("reanudar no debe reinicializar el schema"),
    )
    monkeypatch.setattr(
        engine,
        "_index_chunk",
        lambda _connection, _signatures, chunk, _bands, _rows, _records: indexed_chunks.append(
            chunk
        ),
    )

    assert engine._build_lsh_index(2_500) == (4, 4)
    assert indexed_chunks == [1, 2]
    assert engine.metrics.resumed_from_checkpoint is True
    assert engine._load_checkpoint()["completed_chunks"] == 3


def test_candidate_fingerprint_includes_nit_blocking_policy() -> None:
    """Cambiar el complemento NIT debe invalidar candidates.db, no acumular pares."""

    frame = pd.DataFrame({"NOMBRE_LIMPIO": ["ACME", "ACME"]})
    disabled = DiskBasedLSHEngine(profile={"enable_nit_blocking": False})
    enabled = DiskBasedLSHEngine(
        profile={
            "enable_nit_blocking": True,
            "nit_blocking_neighbors": True,
            "nit_blocking_max_bucket": 17,
            "nit_blocking_column": "NIT_BASE",
        }
    )
    disabled._index_fp = enabled._index_fp = "same-index"

    assert disabled._candidate_run_fingerprint(frame, 8, False) != (
        enabled._candidate_run_fingerprint(frame, 8, False)
    )


def test_clusterer_streams_edges_from_sqlite_without_pandas_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Una ruta SQLite debe recorrer aristas por fetchmany, no read_sql_query."""

    scored_db = tmp_path / "scored.db"
    with closing(sqlite3.connect(scored_db)) as connection:
        connection.execute("CREATE TABLE scored_pairs (idx_0 INTEGER, idx_1 INTEGER, score REAL)")
        connection.executemany(
            "INSERT INTO scored_pairs VALUES (?, ?, ?)",
            [(0, 1, 0.9), (1, 2, 0.9), (3, 4, 0.9)],
        )
        connection.commit()

    def fail_materialization(*_args, **_kwargs):
        pytest.fail("el clustering streaming no debe usar pandas.read_sql_query")

    monkeypatch.setattr(pd, "read_sql_query", fail_materialization)
    clusterer = OptimizedClusterer({"clustering_batch_size": 1, "use_strict_clusters": False})
    frame = pd.DataFrame({"NIT_OK": [""] * 6})

    mapping = clusterer.cluster_entities(str(scored_db), df_full=frame)

    assert mapping == {0: 0, 1: 0, 2: 0, 3: 3, 4: 3, 5: 5}
    assert clusterer.stats["edges_processed"] == 3
    assert clusterer.stats["clusters_found"] == 3


def test_streaming_clusterer_preserves_max_sources_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El camino de bajo RAM debe respetar la misma política post-clustering."""

    clusterer = OptimizedClusterer(
        {
            "clustering_batch_size": 2,
            "use_strict_clusters": False,
            "max_sources_per_group": 2,
        }
    )
    monkeypatch.setattr(clusterer, "_should_use_disk_processing", lambda *_args: True)
    frame = pd.DataFrame(
        {
            "SRC": ["RUES", "RUES", "CRM", "DIAN", "EXPO"],
            "NIT": ["111", "111", "111", "111", "111"],
            "NIT_OK": ["111", "111", "111", "111", "111"],
        }
    )
    edges = pd.DataFrame({"idx_0": [0, 1, 2, 3], "idx_1": [1, 2, 3, 4], "score": [0.9] * 4})

    mapping = clusterer.cluster_entities(edges, df_full=frame)

    assert mapping[0] == mapping[1] == 0
    assert mapping[2] == 2
    assert mapping[3] == 3
    assert mapping[4] == 4
    assert clusterer.stats["clusters_found"] == 4
    assert clusterer.stats["largest_cluster_size"] == 2


@pytest.mark.parametrize(
    ("enable_nit_blocking", "expected_pairs"),
    [
        (True, {(0, 1), (4, 5)}),
        (False, {(4, 5)}),
    ],
)
def test_public_candidates_share_trusted_policy_and_nit_blocking_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enable_nit_blocking: bool,
    expected_pairs: set[tuple[int, int]],
) -> None:
    """La API pública fusiona NIT sin duplicar ni vetar fuentes no trusted."""

    engine = TrustedSourceLSHEngine(
        profile={
            "enable_nit_blocking": enable_nit_blocking,
            "nit_blocking_neighbors": False,
        },
        trusted_sources={"RUES"},
    )
    frame = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": [f"EMPRESA {index}" for index in range(6)],
            "NIT_BASE": [
                "900123456",
                "900123456",
                "800123456",
                "800123456",
                "700123456",
                "700123456",
            ],
            "FUENTE": [
                "EXPORTACIONES",
                "EXPORTACIONES",
                "RUES",
                "RUES",
                "RUES",
                "EXPORTACIONES",
            ],
        }
    )
    # v0.17.3: el generador ya no recibe un dict {record_id: nombre_fuente}
    # sino códigos enteros + la matriz de política. La política SIGUE saliendo
    # de _source_pair_mask, así que lo que esta prueba vigila —que RUES no se
    # deduplique contra sí misma y que EXPORTACIONES sí— no cambió.
    nombres_fuente, codigos = np.unique(frame["FUENTE"].astype(str).to_numpy(), return_inverse=True)
    politica = engine._matriz_politica_fuentes(nombres_fuente, cross_source_only=True)

    lsh_pairs = set(
        engine._generate_bucket_pairs(
            list(range(len(frame))), codigos.astype(np.int16, copy=False), politica
        )
    )
    assert (0, 1) in lsh_pairs
    assert (2, 3) not in lsh_pairs
    assert (4, 5) in lsh_pairs

    monkeypatch.setattr(engine, "_generate_signatures", lambda _frame: None)

    def seed_lsh_index(n_records: int) -> tuple[int, int]:
        """Crea una banda real; la segunda corrida reanuda candidates.db."""

        engine._index_fp = "test-index-fingerprint"
        with engine._get_sqlite_connection(engine._index_db_file) as connection:
            engine._init_index_schema(
                connection,
                n_bands=1,
                rows_per_band=16,
                n_records=n_records,
                index_fingerprint=engine._index_fp,
            )
            if connection.execute("SELECT COUNT(*) FROM lsh_buckets").fetchone() == (0,):
                # Este par también lo genera NIT blocking: INSERT OR IGNORE
                # debe impedir que repetir/reanudar cambie el cardinal.
                connection.executemany(
                    "INSERT INTO lsh_buckets VALUES (?, ?, ?)",
                    [(0, 12345, 4), (0, 12345, 5)],
                )
                connection.commit()
        return 1, 16

    monkeypatch.setattr(engine, "_build_lsh_index", seed_lsh_index)

    first = engine.find_candidates(frame, output_dir=str(tmp_path))
    second = engine.find_candidates(frame, output_dir=str(tmp_path))

    assert first == expected_pairs
    assert second == expected_pairs
    assert engine.metrics.candidates_found == len(expected_pairs)
