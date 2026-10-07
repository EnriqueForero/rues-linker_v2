"""La regla de ``ID_REGISTRO`` y la de ``ID_GRUPO`` por contenido, escritas una vez (F2.3).

``salida/identificadores_registro.py`` es el único sitio donde se construye la
clave por registro (``<SRC>-<id nativo>`` o ``<SRC>-F<ORIGINAL_INDEX>``) y el
entero de grupo determinista (posición lexicográfica del menor ``ID_REGISTRO``
del grupo). L4 (``engine/clusterer.py``) y ``salida/completar.py`` lo llaman;
ninguno copia la regla.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.evaluation.banco import huella_particion
from record_linkage.salida.identificadores_registro import (
    asignar_id_registro,
    id_grupo_por_clave_minima,
    nombre_efectivo,
    recodificar_id_grupo,
)

# ── id_grupo_por_clave_minima ─────────────────────────────────────────────


def test_id_grupo_es_el_rango_lexicografico_del_menor_id_registro() -> None:
    grupos = np.array([7, 7, 3, 3, 9])
    claves = np.array(["B-2", "A-9", "C-1", "A-1", "B-1"], dtype=object)
    # mínimos por grupo: 7 → "A-9", 3 → "A-1", 9 → "B-1"; orden: A-1 < A-9 < B-1
    assert id_grupo_por_clave_minima(grupos, claves).tolist() == [1, 1, 0, 0, 2]


def test_id_grupo_es_entero_denso_desde_cero() -> None:
    grupos = np.array([100, 100, 5000, 42])
    claves = np.array(["Z", "Y", "M", "A"], dtype=object)
    salida = id_grupo_por_clave_minima(grupos, claves)
    assert salida.dtype == np.int64
    assert sorted(set(salida.tolist())) == [0, 1, 2]


def test_id_grupo_no_depende_del_orden_de_las_filas() -> None:
    rng = np.random.default_rng(7)
    n = 500
    grupos = rng.integers(0, 80, n)
    claves = np.array([f"SRC-{rng.integers(0, 10**6):06d}-{i}" for i in range(n)], dtype=object)
    base = id_grupo_por_clave_minima(grupos, claves)
    perm = rng.permutation(n)
    # Etiquetas de grupo distintas (relabel arbitrario) y filas permutadas:
    # el id final por clave es el mismo.
    relabel = {g: 10_000 - g for g in np.unique(grupos)}
    otros = id_grupo_por_clave_minima(np.array([relabel[g] for g in grupos[perm]]), claves[perm])
    por_clave_base = dict(zip(claves.tolist(), base.tolist(), strict=True))
    por_clave_otros = dict(zip(claves[perm].tolist(), otros.tolist(), strict=True))
    assert por_clave_base == por_clave_otros


def test_id_grupo_acepta_etiquetas_de_texto() -> None:
    """``dedupe`` etiqueta ``C<n>``/``S<n>``: la regla las lleva a entero igual."""
    grupos = np.array(["C0", "C0", "S1", "C0"], dtype=object)
    claves = np.array(["df-F0", "df-F1", "df-F2", "df-F3"], dtype=object)
    assert id_grupo_por_clave_minima(grupos, claves).tolist() == [0, 0, 1, 0]


def test_id_grupo_vacio() -> None:
    salida = id_grupo_por_clave_minima(np.array([], dtype=np.int64), np.array([], dtype=object))
    assert salida.dtype == np.int64 and len(salida) == 0


def test_id_grupo_falla_si_las_longitudes_no_coinciden() -> None:
    with pytest.raises(ValueError, match="claves"):
        id_grupo_por_clave_minima(np.array([0, 0]), np.array(["a"], dtype=object))


def test_id_grupo_falla_con_claves_repetidas() -> None:
    """Con claves repetidas el id dependería del orden de las filas: se rechaza (ADR-0013, 5)."""
    with pytest.raises(ValueError, match="repetida"):
        id_grupo_por_clave_minima(np.array([0, 1]), np.array(["a", "a"], dtype=object))


def test_id_grupo_falla_con_claves_repetidas_entre_fuentes_con_guion() -> None:
    """``SRC='X-Y'`` + id ``1`` y ``SRC='X'`` + id ``Y-1`` dan la misma clave ``X-Y-1``."""
    marco = pd.DataFrame({"SRC": ["X-Y", "X"], "ORIGINAL_INDEX": [0, 1], "CODIGO": ["1", "Y-1"]})
    claves, _ = asignar_id_registro(marco, "CODIGO")
    assert claves.tolist() == ["X-Y-1", "X-Y-1"]
    with pytest.raises(ValueError, match="repetida"):
        id_grupo_por_clave_minima(np.array([0, 1]), claves)


# ── recodificar_id_grupo (correlativa + golden con el mismo mapa) ─────────


def test_recodificar_id_grupo_aplica_el_mismo_mapa_al_golden() -> None:
    """La misma función para L5 y ``completar``: correlativa y golden hablan el mismo idioma."""
    correl = pd.DataFrame({"ID_GRUPO": [7, 7, 3, 9]})
    golden = pd.DataFrame({"ID_GRUPO": [3, 7, 9], "RAZON_SOCIAL_FINAL": ["c", "a", "d"]})
    claves = np.array(["S-F1", "S-F0", "S-F2", "S-F3"], dtype=object)
    golden_rec, info = recodificar_id_grupo(correl, golden, claves)
    # {7: S-F0} → 0; {3: S-F2} → 1; {9: S-F3} → 2
    assert correl["ID_GRUPO"].tolist() == [0, 0, 1, 2]
    assert golden_rec is not None
    assert golden_rec["ID_GRUPO"].tolist() == [1, 0, 2]
    assert golden["ID_GRUPO"].tolist() == [3, 7, 9], "no muta el golden del llamador"
    assert info["recodificado"] is True and info["valores_cambiados"] == 4
    assert info["n_grupos"] == 3 and info["huerfanas_golden"] == 0


def test_recodificar_id_grupo_declara_identidad_y_huerfanas() -> None:
    correl = pd.DataFrame({"ID_GRUPO": [0, 0, 1]})
    golden = pd.DataFrame({"ID_GRUPO": [0, 1, 5]})
    claves = np.array(["S-F0", "S-F1", "S-F2"], dtype=object)
    golden_rec, info = recodificar_id_grupo(correl, golden, claves)
    assert info["recodificado"] is False and info["valores_cambiados"] == 0
    assert golden_rec is not None and golden_rec["ID_GRUPO"].tolist() == [0, 1, -1]
    assert info["huerfanas_golden"] == 1
    sin_golden, info2 = recodificar_id_grupo(correl, None, claves)
    assert sin_golden is None and info2["recodificado"] is False


def test_huella_no_depende_del_valor_del_id() -> None:
    """La compuerta del banco: renombrar los grupos no mueve la huella."""
    grupos = np.array([7, 7, 3, 3, 9])
    claves = np.array(["B-2", "A-9", "C-1", "A-1", "B-1"], dtype=object)
    nuevos = id_grupo_por_clave_minima(grupos, claves)
    indices = list(range(5))
    assert huella_particion(indices, grupos.tolist()) == huella_particion(indices, nuevos.tolist())


# ── asignar_id_registro ───────────────────────────────────────────────────


def _marco() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "SRC": ["A", "A", "B", "B"],
            "ORIGINAL_INDEX": [0, 1, 2, 3],
            "CODIGO": ["x1", "x2", "", "y2"],
        }
    )


def test_sin_col_id_usa_la_fila() -> None:
    ids, info = asignar_id_registro(_marco(), None)
    assert ids.tolist() == ["A-F0", "A-F1", "B-F2", "B-F3"]
    assert info == {
        "A": {"regla": "fila", "motivo": "sin col_id"},
        "B": {"regla": "fila", "motivo": "sin col_id"},
    }


def test_col_id_por_fuente_y_caida_a_fila_declarada() -> None:
    ids, info = asignar_id_registro(_marco(), "CODIGO")
    assert ids.tolist() == ["A-x1", "A-x2", "B-F2", "B-F3"]
    assert info["A"] == {"regla": "col_id", "columna": "CODIGO"}
    assert info["B"]["regla"] == "fila" and "vacío" in info["B"]["motivo"]


def test_col_id_inexistente_falla_con_mensaje_accionable() -> None:
    with pytest.raises(ValueError, match="col_id='NO'"):
        asignar_id_registro(_marco(), "NO")


def test_nombre_efectivo_resuelve_renombre_canonico() -> None:
    presentes = ["RAZON_SOCIAL", "NIT", "CODIGO"]
    canonicos = {"NOMBRE": "RAZON_SOCIAL", "IDENT": "NIT"}
    assert nombre_efectivo("NOMBRE", presentes, canonicos) == "RAZON_SOCIAL"
    assert nombre_efectivo("CODIGO", presentes, canonicos) == "CODIGO"
    assert nombre_efectivo("OTRA", presentes, canonicos) == "OTRA"
