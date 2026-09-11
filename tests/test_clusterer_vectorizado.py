"""Equivalencia del clustering vectorizado (v2.2.0) vs Union-Find (v2.1.0).

El método ``_cluster_in_memory`` se reescribió de un Union-Find con ``iterrows``
a ``scipy.sparse.csgraph.connected_components``. Los IDS de cluster cambian
(scipy usa labels 0..k-1; el Union-Find usaba la raíz arbitraria de cada
componente), pero la PARTICIÓN debe ser idéntica: el conjunto de pares que
quedan en el mismo cluster no puede cambiar.

Estos tests reimplementan el Union-Find original como oráculo y verifican que
ambos producen la misma partición sobre grafos aleatorios, incluyendo casos
borde (sin aristas, una sola entidad, cadenas largas, NITs idénticos).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.clusterer import OptimizedClusterer


def _union_find_oraculo(
    scored_pairs: pd.DataFrame, df_full: pd.DataFrame | None, n: int
) -> list[set[int]]:
    """Implementación de referencia (la de v2.1.0) que devuelve la partición."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    if df_full is not None and "NIT_OK" in df_full.columns:
        for _nit, idxs in df_full[df_full["NIT_OK"].notna()].groupby("NIT_OK").groups.items():
            il = list(idxs)
            for i in range(1, len(il)):
                union(il[0], il[i])
    for _, r in scored_pairs.iterrows():
        union(int(r["idx_0"]), int(r["idx_1"]))

    grupos: dict[int, set[int]] = {}
    for i in range(n):
        grupos.setdefault(find(i), set()).add(i)
    return sorted(grupos.values(), key=min)


def _particion_desde_mapping(mapping: dict[int, int], n: int) -> list[set[int]]:
    grupos: dict[int, set[int]] = {}
    for i in range(n):
        grupos.setdefault(mapping[i], set()).add(i)
    return sorted(grupos.values(), key=min)


@pytest.mark.parametrize("seed", range(8))
def test_equivalencia_particion_grafos_aleatorios(seed: int) -> None:
    """La partición vectorizada == Union-Find sobre pares aleatorios."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(5, 60))
    n_pairs = int(rng.integers(0, n * 2))
    if n_pairs:
        a = rng.integers(0, n, n_pairs)
        b = rng.integers(0, n, n_pairs)
        mask = a != b  # sin auto-aristas
        scored = pd.DataFrame({"idx_0": a[mask], "idx_1": b[mask]})
    else:
        scored = pd.DataFrame({"idx_0": [], "idx_1": []})

    clus = OptimizedClusterer.__new__(OptimizedClusterer)  # sin __init__ pesado
    clus.logger = _DummyLogger()
    clus.stats = {"unions_by_nit": 0, "unions_by_lsh": 0}
    clus._start_time = 0.0

    mapping = clus._cluster_in_memory(scored, None, n)
    esperado = _union_find_oraculo(scored, None, n)
    obtenido = _particion_desde_mapping(mapping, n)
    assert obtenido == esperado, f"seed={seed}: partición distinta"


def test_equivalencia_con_nit_identico() -> None:
    """Las aristas por NIT idéntico producen la misma partición."""
    n = 6
    df_full = pd.DataFrame({"NIT_OK": ["900", "900", "900", "800", None, "800"]})
    scored = pd.DataFrame({"idx_0": [4], "idx_1": [0]})  # conecta 4 al grupo 900

    clus = OptimizedClusterer.__new__(OptimizedClusterer)
    clus.logger = _DummyLogger()
    clus.stats = {"unions_by_nit": 0, "unions_by_lsh": 0}
    clus._start_time = 0.0

    mapping = clus._cluster_in_memory(scored, df_full, n)
    esperado = _union_find_oraculo(scored, df_full, n)
    obtenido = _particion_desde_mapping(mapping, n)
    assert obtenido == esperado


def test_sin_aristas_cada_uno_su_cluster() -> None:
    """Sin pares ni NITs, cada entidad es su propio cluster."""
    n = 5
    scored = pd.DataFrame({"idx_0": [], "idx_1": []})
    clus = OptimizedClusterer.__new__(OptimizedClusterer)
    clus.logger = _DummyLogger()
    clus.stats = {"unions_by_nit": 0, "unions_by_lsh": 0}
    clus._start_time = 0.0
    mapping = clus._cluster_in_memory(scored, None, n)
    assert len({mapping[i] for i in range(n)}) == n


class _DummyLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass
