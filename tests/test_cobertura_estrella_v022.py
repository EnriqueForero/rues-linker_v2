"""Cobertura por estrellas: corta el encadenamiento del single-linkage (v0.22.1)."""

from __future__ import annotations

import numpy as np
import pytest

from record_linkage.engine.cobertura import cobertura_estrella


class ComparadorPrefijo:
    """Similitud artificial: +1 si comparten los 3 primeros caracteres."""

    signed = True

    def compare(self, izq, der):
        return np.array(
            [1.0 if str(a)[:3] == str(b)[:3] else -1.0 for a, b in zip(izq, der, strict=True)],
            dtype=np.float64,
        )


class ComparadorCamino:
    """Cadena A–B–C–D: solo los vecinos inmediatos se parecen."""

    signed = True

    def compare(self, izq, der):
        vecinos = {("A", "B"), ("B", "C"), ("C", "D")}
        return np.array(
            [
                1.0
                if a == b or (str(a), str(b)) in vecinos or (str(b), str(a)) in vecinos
                else -1.0
                for a, b in zip(izq, der, strict=True)
            ],
            dtype=np.float64,
        )


def test_una_estrella_legitima_se_conserva():
    """A–B–C con B al centro SÍ es una estrella: los tres están cerca de B.

    La cobertura no parte por partir; parte cuando ningún miembro alcanza a
    todos. Fijar esto evita "arreglar" el algoritmo hacia un comportamiento
    que rompería agrupaciones correctas.
    """
    valores = np.array(["A", "B", "C"])
    r = cobertura_estrella(
        valores,
        np.zeros(3, dtype=int),
        np.array([1.0, 3.0, 1.0]),
        ComparadorCamino(),
        similitud_minima=0.0,
    )
    assert r.n_grupos_despues == 1
    assert valores[r.es_lider][0] == "B"


def test_rompe_el_encadenamiento_cuando_nadie_cubre_a_todos():
    """A–B–C–D: ningún miembro alcanza a los cuatro, así que hay que partir.

    Es el modo de falla que motivó el módulo: el single-linkage devolvía un
    solo grupo con A y D dentro, que no se parecen en nada.
    """
    valores = np.array(["A", "B", "C", "D"])
    r = cobertura_estrella(
        valores,
        np.zeros(4, dtype=int),
        np.array([1.0, 4.0, 3.0, 2.0]),
        ComparadorCamino(),
        similitud_minima=0.0,
    )
    assert r.n_grupos_antes == 1
    assert r.n_grupos_despues > 1
    grupos = dict(zip(valores, r.etiquetas, strict=True))
    assert grupos["A"] != grupos["D"]
    # Y la garantía se mantiene en cada grupo resultante.
    assert (r.similitud_al_lider >= 0.0).all()


def test_garantiza_que_todo_miembro_esta_cerca_de_su_lider():
    """La invariante que la correlativa afirma. Si se cae, la tabla miente."""
    valores = np.array(["AAA1", "AAA2", "BBB1", "BBB2", "AAA3", "CCC1"])
    r = cobertura_estrella(
        valores,
        np.zeros(6, dtype=int),
        np.arange(6.0)[::-1],
        ComparadorPrefijo(),
        similitud_minima=0.0,
    )
    assert (r.similitud_al_lider >= 0.0).all()
    for etiqueta in np.unique(r.etiquetas):
        miembros = valores[r.etiquetas == etiqueta]
        assert len({m[:3] for m in miembros}) == 1


def test_cada_grupo_tiene_exactamente_un_lider():
    valores = np.array(["AAA1", "AAA2", "BBB1"])
    r = cobertura_estrella(
        valores,
        np.zeros(3, dtype=int),
        np.arange(3.0),
        ComparadorPrefijo(),
        similitud_minima=0.0,
    )
    for etiqueta in np.unique(r.etiquetas):
        assert int(r.es_lider[r.etiquetas == etiqueta].sum()) == 1


def test_es_determinista_e_independiente_del_orden_de_entrada():
    valores = np.array(["AAA1", "AAA2", "AAA3", "BBB1"])
    masa = np.array([10.0, 5.0, 1.0, 7.0])
    a = cobertura_estrella(
        valores, np.zeros(4, dtype=int), masa, ComparadorPrefijo(), similitud_minima=0.0
    )
    b = cobertura_estrella(
        valores, np.zeros(4, dtype=int), masa, ComparadorPrefijo(), similitud_minima=0.0
    )
    assert np.array_equal(a.etiquetas, b.etiquetas)
    assert np.array_equal(a.es_lider, b.es_lider)


def test_lider_por_masa_respeta_el_desempate_declarado():
    valores = np.array(["AAA1", "AAA2"])
    r = cobertura_estrella(
        valores,
        np.zeros(2, dtype=int),
        np.array([1.0, 99.0]),
        ComparadorPrefijo(),
        similitud_minima=0.0,
        regla_lider="masa",
    )
    assert valores[r.es_lider][0] == "AAA2"


def test_singleton_es_su_propio_lider():
    r = cobertura_estrella(
        np.array(["SOLO"]),
        np.zeros(1, dtype=int),
        np.array([1.0]),
        ComparadorPrefijo(),
        similitud_minima=0.0,
    )
    assert bool(r.es_lider[0]) is True
    assert r.n_cortes == 0


def test_entrada_vacia():
    r = cobertura_estrella(
        np.array([]),
        np.array([], dtype=int),
        np.array([]),
        ComparadorPrefijo(),
        similitud_minima=0.0,
    )
    assert r.n_grupos_despues == 0


def test_errores_accionables():
    with pytest.raises(ValueError, match="largos distintos"):
        cobertura_estrella(
            np.array(["A"]),
            np.array([0, 0]),
            np.array([1.0]),
            ComparadorPrefijo(),
            similitud_minima=0.0,
        )
    with pytest.raises(ValueError, match="FIRMADA"):
        cobertura_estrella(
            np.array(["A"]),
            np.array([0]),
            np.array([1.0]),
            ComparadorPrefijo(),
            similitud_minima=1.5,
        )
    with pytest.raises(ValueError, match="regla_lider"):
        cobertura_estrella(
            np.array(["A"]),
            np.array([0]),
            np.array([1.0]),
            ComparadorPrefijo(),
            similitud_minima=0.0,
            regla_lider="medoide",
        )
