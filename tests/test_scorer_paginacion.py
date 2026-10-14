"""F4.1: la paginación de ``candidate_pairs`` usa la clave primaria (O(lote)), nunca un SCAN ni OFFSET."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from record_linkage.engine.scorer import CONSULTA_PAGINA_CANDIDATOS


def _base(tmp_path, pares):
    ruta = tmp_path / "candidates.db"
    con = sqlite3.connect(ruta)
    con.execute(
        "CREATE TABLE candidate_pairs (idx_0 INTEGER NOT NULL, idx_1 INTEGER NOT NULL, "
        "PRIMARY KEY (idx_0, idx_1)) WITHOUT ROWID"
    )
    con.executemany("INSERT OR IGNORE INTO candidate_pairs VALUES (?, ?)", pares)
    con.commit()
    return con


def test_el_plan_de_consulta_busca_por_la_clave_primaria(tmp_path):
    con = _base(tmp_path, [(0, 1), (0, 2), (1, 2)])
    plan = " ".join(
        fila[3]
        for fila in con.execute("EXPLAIN QUERY PLAN " + CONSULTA_PAGINA_CANDIDATOS, (0, 1, 100))
    )
    assert "USING PRIMARY KEY" in plan, plan
    assert "SCAN" not in plan, plan
    con.close()


@pytest.mark.parametrize("lote", [1, 3, 7, 1000])
def test_la_paginacion_recorre_todos_los_pares_una_vez_y_en_orden(tmp_path, lote):
    azar = np.random.default_rng(42)
    pares = {(int(a), int(b)) for a, b in azar.integers(0, 40, size=(300, 2)) if a != b}
    pares = {(min(a, b), max(a, b)) for a, b in pares}
    con = _base(tmp_path, sorted(pares))
    ultimo, vistos = (-1, -1), []
    while True:
        filas = con.execute(CONSULTA_PAGINA_CANDIDATOS, (*ultimo, lote)).fetchall()
        if not filas:
            break
        vistos.extend(filas)
        ultimo = filas[-1]
    con.close()
    assert vistos == sorted(pares)
