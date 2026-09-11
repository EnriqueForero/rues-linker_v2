"""Paridad y rendimiento del union-find con cannot-links (v0.12.0, cierre de H3).

La versión 0.11.x de ``clusters_desde_decisiones`` reconstruía la pertenencia
de TODOS los nodos por cada unión candidata (~cuadrático: 193 s con n=30.000 y
1% de vetos, experimento E10 de la auditoría 2026-08-26). La versión 0.12.0
mantiene conjuntos de raíces enemigas con re-apuntado en la unión.

Contrato:
    1. PARIDAD BIT A BIT de etiquetas contra la implementación de referencia
       0.11.x (copiada aquí) sobre casos aleatorios densos en vetos.
    2. Rendimiento: n=30.000 con 1% de vetos en < 5 s (antes 193 s).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.motor_multicampo import clusters_desde_decisiones


def _referencia_0_11(n_filas: int, decisiones: pd.DataFrame, respetar_vetos: bool = True):
    """Implementación 0.11.x literal (lenta), como oráculo de paridad."""
    padre = np.arange(n_filas)
    rango = np.zeros(n_filas, dtype=np.int64)

    def find(x: int) -> int:
        raiz = x
        while padre[raiz] != raiz:
            raiz = padre[raiz]
        while padre[x] != raiz:
            padre[x], x = raiz, padre[x]
        return raiz

    cannot: set[tuple[int, int]] = set()
    if respetar_vetos and "veto" in decisiones.columns:
        for a, b in decisiones.loc[decisiones["veto"], ["i", "j"]].to_numpy():
            cannot.add((int(a), int(b)))

    def hay_conflicto(ra: int, rb: int) -> bool:
        if not cannot:
            return False
        raices = np.array([find(k) for k in range(n_filas)])
        sa = set(np.flatnonzero(raices == ra).tolist())
        sb = set(np.flatnonzero(raices == rb).tolist())
        return any((u in sa and v in sb) or (u in sb and v in sa) for u, v in cannot)

    fusiones = decisiones.loc[decisiones["fusion"], ["i", "j", "score"]]
    fusiones = fusiones.sort_values("score", ascending=False)
    for a, b, _ in fusiones.to_numpy():
        ra, rb = find(int(a)), find(int(b))
        if ra == rb:
            continue
        if hay_conflicto(ra, rb):
            continue
        if rango[ra] < rango[rb]:
            ra, rb = rb, ra
        padre[rb] = ra
        if rango[ra] == rango[rb]:
            rango[ra] += 1

    raices = np.array([find(k) for k in range(n_filas)])
    _, etiquetas = np.unique(raices, return_inverse=True)
    return etiquetas


def _decisiones_aleatorias(n: int, m: int, p_fusion: float, p_veto: float, seed: int):
    rng = np.random.default_rng(seed)
    i = rng.integers(0, n, m)
    j = rng.integers(0, n, m)
    return pd.DataFrame(
        {
            "i": i,
            "j": j,
            "score": rng.random(m),
            "fusion": rng.random(m) < p_fusion,
            "veto": rng.random(m) < p_veto,
        }
    )


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_paridad_bit_a_bit_con_referencia(seed):
    """Etiquetas idénticas a 0.11.x en casos aleatorios densos en vetos."""
    n, m = 120, 400
    dec = _decisiones_aleatorias(n, m, p_fusion=0.6, p_veto=0.15, seed=seed)
    esperado = _referencia_0_11(n, dec)
    obtenido = clusters_desde_decisiones(n, dec)
    np.testing.assert_array_equal(obtenido, esperado)


def test_paridad_sin_vetos():
    dec = _decisiones_aleatorias(200, 500, p_fusion=0.7, p_veto=0.0, seed=7)
    np.testing.assert_array_equal(clusters_desde_decisiones(200, dec), _referencia_0_11(200, dec))


def test_paridad_respetar_vetos_false():
    dec = _decisiones_aleatorias(150, 400, p_fusion=0.6, p_veto=0.2, seed=11)
    np.testing.assert_array_equal(
        clusters_desde_decisiones(150, dec, respetar_vetos=False),
        _referencia_0_11(150, dec, respetar_vetos=False),
    )


def test_veto_corta_puente_transitivo():
    """a↔c↔b con veto a—b: el veto debe impedir que a y b acaben juntos."""
    dec = pd.DataFrame(
        {
            "i": [0, 1, 0],
            "j": [2, 2, 1],
            "score": [0.9, 0.8, 0.95],
            "fusion": [True, True, True],
            "veto": [False, False, True],
        }
    )
    etiquetas = clusters_desde_decisiones(3, dec)
    assert etiquetas[0] != etiquetas[1], "el cannot-link a—b fue violado"


def test_rendimiento_30k_bajo_5s():
    """E10 de la auditoría: n=30.000, 1% vetos. 0.11.x: 193 s. Meta: < 5 s."""
    n = 30_000
    dec = _decisiones_aleatorias(n, n // 2, p_fusion=0.5, p_veto=0.01, seed=0)
    t0 = time.time()
    etiquetas = clusters_desde_decisiones(n, dec)
    elapsed = time.time() - t0
    assert len(etiquetas) == n
    assert elapsed < 5.0, f"clustering con cannot-links tardó {elapsed:.1f}s (meta < 5s)"
