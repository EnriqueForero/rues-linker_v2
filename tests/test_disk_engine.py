"""Tests del DiskBasedLSHEngine reescrito (v2.3.0).

Cubre las tres mejoras del motor:
1. ``_hash_rows_stable`` es determinista (corrige el bug de ``hash()`` que
   rompía el checkpointing tras reinicios de sesión).
2. ``_generate_bucket_pairs`` vectorizado == la lógica original (mismos pares).
3. Smoke end-to-end: el motor encuentra los candidatos obvios en un set chico.
"""

from __future__ import annotations

import tempfile
from itertools import combinations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine, _hash_rows_stable


def test_hash_rows_estable_es_determinista() -> None:
    """El mismo input produce el mismo hash en llamadas e instancias distintas."""
    rng = np.random.default_rng(0)
    band = rng.integers(0, 2**63, size=(100, 4), dtype=np.uint64)
    h1 = _hash_rows_stable(band)
    h2 = _hash_rows_stable(band.copy())
    assert np.array_equal(h1, h2)


def test_hash_rows_filas_iguales_mismo_hash() -> None:
    """Filas idénticas -> mismo hash; filas distintas -> (casi siempre) distinto."""
    band = np.array([[1, 2, 3], [1, 2, 3], [9, 9, 9]], dtype=np.uint64)
    h = _hash_rows_stable(band)
    assert h[0] == h[1]
    assert h[0] != h[2]


def test_hash_rows_una_columna() -> None:
    """Maneja bandas de una sola columna (rows_per_band=1)."""
    band = np.array([[5], [5], [7]], dtype=np.uint64)
    h = _hash_rows_stable(band)
    assert h[0] == h[1] != h[2]


def _bucket_pairs_oraculo(ids, source_map):
    """Implementación original (doble bucle) como referencia."""
    pairs = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            if a > b:
                a, b = b, a
            if source_map and source_map.get(a) == source_map.get(b):
                continue
            pairs.append((a, b))
    return pairs


@pytest.mark.parametrize("seed", range(5))
def test_bucket_pairs_equivale_al_oraculo(seed: int) -> None:
    """La generación vectorizada de pares == el doble bucle original."""
    rng = np.random.default_rng(seed)
    ids = list(rng.choice(1000, size=int(rng.integers(2, 30)), replace=False))
    eng = DiskBasedLSHEngine.__new__(DiskBasedLSHEngine)
    obtenido = sorted(eng._generate_bucket_pairs([int(x) for x in ids], None))
    esperado = sorted(_bucket_pairs_oraculo([int(x) for x in ids], None))
    assert obtenido == esperado


def test_bucket_pairs_cross_source() -> None:
    """El filtro cross-source descarta pares de la misma fuente.

    v0.17.3: la política llega como matriz k×k y las fuentes como códigos
    NumPy indexados por record_id; el oráculo del doble bucle sigue siendo
    la referencia.
    """
    import numpy as np

    ids = [0, 1, 2, 3]
    source_map = {0: "RUES", 1: "RUES", 2: "CRM", 3: "CRM"}
    nombres = np.array(["CRM", "RUES"])
    codigos = np.array([1, 1, 0, 0], dtype=np.int16)
    eng = DiskBasedLSHEngine.__new__(DiskBasedLSHEngine)
    politica = eng._matriz_politica_fuentes(nombres, cross_source_only=True)
    obtenido = sorted(eng._generate_bucket_pairs(ids, codigos, politica))
    esperado = sorted(_bucket_pairs_oraculo(ids, source_map))
    assert obtenido == esperado
    for a, b in obtenido:
        assert source_map[a] != source_map[b]


def test_la_forma_antigua_de_source_map_falla_ruidosamente() -> None:
    """Un dict silenciosamente mal interpretado sería un fallo de datos."""
    import pytest

    eng = DiskBasedLSHEngine.__new__(DiskBasedLSHEngine)
    with pytest.raises(TypeError, match=r"v0\.17\.3"):
        eng._generate_bucket_pairs([0, 1], {0: "A", 1: "B"}, None)


def test_engine_encuentra_candidatos_obvios() -> None:
    """Smoke: el motor agrupa nombres casi idénticos como candidatos."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": [
                "ECOPETROL SA",
                "ECOPETROL S A",  # casi idéntico a [0]
                "BAVARIA SA",
                "BAVARIA S A",  # casi idéntico a [2]
                "ZZZ EMPRESA TOTALMENTE DISTINTA XYZ",
            ]
        }
    )
    profile = {"lsh_permutations": 128, "lsh_threshold": 0.4, "lsh_ngram": 3, "chunk_size": 100}
    config = {"profile": "p", "profiles": {"p": profile}}
    eng = DiskBasedLSHEngine(profile, config)
    with tempfile.TemporaryDirectory() as tmp:
        result = eng.find_candidates(df, output_dir=tmp, cross_source_only=False)
        # En este tamaño retorna un set en memoria
        if isinstance(result, str):
            import sqlite3

            conn = sqlite3.connect(result)
            pares = set(conn.execute("SELECT idx_0, idx_1 FROM candidate_pairs").fetchall())
            conn.close()
        else:
            pares = result
    # Los pares obvios (0,1) y (2,3) deben estar entre los candidatos
    assert (0, 1) in pares, "ECOPETROL SA / ECOPETROL S A no fue candidato"
    assert (2, 3) in pares, "BAVARIA SA / BAVARIA S A no fue candidato"
