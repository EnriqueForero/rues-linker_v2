"""Paridad del mapeo vectorizado de ``cluster_entities`` (camino estándar sin split).

El camino estándar (``scipy.sparse.csgraph.connected_components``) construía el
mapeo nodo→ID_GRUPO con un bucle Python sobre n nodos (``enumerate(labels)`` +
``min()``). En Fase 1 se reescribió a un ``groupby`` vectorizado. Este test
verifica, sobre 8 grafos aleatorios, que el mapeo resultante es IDÉNTICO al de la
versión por bucle: misma partición Y mismo representante (nodo mínimo por
componente).

Nota: el ``iterrows`` que la auditoría inicial señaló en ``clusterer.py:422``
(``_process_identical_nit_connections``) resultó ser CÓDIGO MUERTO — no lo invoca
``cluster_entities`` ni nada en ``src``. El cuello real del camino vivo era este
mapeo por bucle, que es lo que aquí se valida tras vectorizarlo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.clusterer import OptimizedClusterer


def _profile_minimo() -> dict:
    """Perfil mínimo en modo estándar (scipy), sin split de mega-clusters."""
    return {
        "clustering_batch_size": 50_000,
        "clustering_cache_size": 10_000,
        "use_strict_clusters": False,
        "max_nit_distance": 2,
    }


def _mapping_referencia(labels: np.ndarray) -> dict[int, int]:
    """Mapeo de referencia por bucle: representante = nodo mínimo del componente."""
    from collections import defaultdict

    grupos: dict[int, set[int]] = defaultdict(set)
    for node_id, lab in enumerate(labels):
        grupos[int(lab)].add(node_id)
    mapping: dict[int, int] = {}
    for nodes in grupos.values():
        rep = min(nodes)
        for nd in nodes:
            mapping[nd] = rep
    return mapping


def _labels_oraculo(scored: pd.DataFrame, n: int) -> np.ndarray:
    """Componentes conexos de referencia (mismo método que el camino estándar)."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components as scipy_cc

    if len(scored):
        rows = scored["idx_0"].to_numpy()
        cols = scored["idx_1"].to_numpy()
        data = np.ones(len(rows), dtype=np.int8)
        g = csr_matrix((data, (rows, cols)), shape=(n, n))
        g = g + g.T
        _, labels = scipy_cc(g, directed=False)
        return labels
    return np.arange(n)


@pytest.mark.parametrize("seed", range(8))
def test_mapeo_vectorizado_identico_a_bucle(seed: int) -> None:
    """El mapeo vectorizado == el mapeo por bucle (partición + representante mínimo)."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 80))
    n_pairs = int(rng.integers(0, n * 2))
    if n_pairs:
        a = rng.integers(0, n, n_pairs)
        b = rng.integers(0, n, n_pairs)
        mask = a != b
        scored = pd.DataFrame(
            {"idx_0": a[mask].astype(np.int64), "idx_1": b[mask].astype(np.int64)}
        )
    else:
        scored = pd.DataFrame({"idx_0": np.array([], np.int64), "idx_1": np.array([], np.int64)})

    df_full = pd.DataFrame({"NIT_OK": [None] * n})  # sin NIT ni SRC → camino estándar sin split
    clus = OptimizedClusterer(profile=_profile_minimo())
    obtenido = clus.cluster_entities(scored.copy(), df_full, n)

    esperado = _mapping_referencia(_labels_oraculo(scored, n))
    assert obtenido == esperado, f"Mapeo difiere en seed={seed}"


def test_representante_es_nodo_minimo() -> None:
    """El ID de cada grupo debe ser el nodo mínimo de su componente conexo."""
    scored = pd.DataFrame({"idx_0": [0, 1, 2], "idx_1": [1, 2, 3]})  # cadena 0-1-2-3
    df_full = pd.DataFrame({"NIT_OK": [None] * 5})  # nodo 4 aislado
    clus = OptimizedClusterer(profile=_profile_minimo())
    mapping = clus.cluster_entities(scored, df_full, 5)
    assert mapping[0] == mapping[1] == mapping[2] == mapping[3] == 0
    assert mapping[4] == 4
