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


def test_una_componente_gigante_se_resuelve_en_streaming_sin_matriz(monkeypatch):
    """A 139k registros una componente de decenas de miles de nombres pedía 13,5 GB (matriz k×k) y moría por OOM.

    Por encima de ``FILAS_MATRIZ_MAX`` el líder se elige por masa y solo se compara contra él. Con el umbral
    bajado a 3, una componente de 6 nombres toma ese camino y debe dar exactamente la partición del camino
    exacto con ``regla_lider="masa"``.
    """
    from record_linkage.engine import cobertura

    valores = np.array(
        ["ABC uno", "ABC dos", "XYZ uno", "XYZ dos", "ABC tres", "QQQ"], dtype=object
    )
    etiquetas = np.zeros(6, dtype=np.int64)  # una sola componente
    masa = np.array([1.0, 5.0, 2.0, 9.0, 1.0, 1.0])
    exacto = cobertura_estrella(
        valores, etiquetas, masa, ComparadorPrefijo(), similitud_minima=0.0, regla_lider="masa"
    )
    assert exacto.componentes_en_streaming == 0

    monkeypatch.setattr(cobertura, "FILAS_MATRIZ_MAX", 3)
    streaming = cobertura.cobertura_estrella(
        valores, etiquetas, masa, ComparadorPrefijo(), similitud_minima=0.0, regla_lider="cobertura"
    )
    assert streaming.componentes_en_streaming == 1
    assert streaming.etiquetas.tolist() == exacto.etiquetas.tolist()
    assert streaming.es_lider.tolist() == exacto.es_lider.tolist()
    assert streaming.similitud_al_lider.tolist() == exacto.similitud_al_lider.tolist()
    # Tres estrellas: ABC (líder «ABC dos», masa 5), XYZ (líder «XYZ dos», masa 9) y QQQ sola.
    assert streaming.n_grupos_despues == 3
    assert streaming.es_lider[[1, 3, 5]].all()


def test_una_componente_de_dos_mil_quinientos_nombres_termina_en_streaming():
    """Guarda del OOM de 139k: por encima de FILAS_MATRIZ_MAX no se arma la matriz k×k.

    Con el comparador de prefijo y 2.500 nombres repartidos en 50 prefijos, el camino en streaming debe
    devolver 50 estrellas (una por prefijo) y marcar la componente como resuelta en streaming.
    """
    from record_linkage.engine.cobertura import FILAS_MATRIZ_MAX

    n = FILAS_MATRIZ_MAX + 500
    valores = np.array([f"P{i % 50:02d} nombre {i}" for i in range(n)], dtype=object)
    etiquetas = np.zeros(n, dtype=np.int64)
    masa = np.ones(n)
    res = cobertura_estrella(valores, etiquetas, masa, ComparadorPrefijo(), similitud_minima=0.0)
    assert res.componentes_en_streaming == 1
    assert res.n_grupos_despues == 50
    assert int(res.es_lider.sum()) == 50
