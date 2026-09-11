"""Comparador de razones sociales sin identificador (v0.22.1).

Cada prueba fija un modo de falla MEDIDO sobre 211.949 destinatarios de
exportación. Si una se cae, el comparador volvió a uno de esos modos.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.genericos import (
    GENERICOS_ESTRUCTURALES,
    GENERICOS_GEOGRAFIA,
    genericos,
)
from record_linkage.matching.idf import construir_idf
from record_linkage.matching.nombre_idf import (
    SimilitudNombre,
    contencion_idf,
    jaccard_idf,
    marcar_informativos,
    neutralizar_genericos,
    piso_por_frecuencia,
)


#: Corpus sintético con la densidad de genéricos de una base real: sin ella,
#: el IDF de "FLOWERS" o "TRADING" sería alto y las puertas no se activarían.
def _corpus() -> pd.Series:
    relleno = []
    for i in range(400):
        relleno += [
            f"COMERCIALIZADORA MARCA{i} CA",
            f"IMPORTADORA EXPORTADORA MARCA{i}",
            f"FLOWERS MARCA{i} TRADING",
            f"INTERNATIONAL MARCA{i} GROUP",
        ]
    casos = [
        "HINCAPIE SPORTSWEAR",
        "HINCAPIE SPORTSWEAR FEDEX 453927848470",
        "HINCAPIE SPORTSWEAR FEDEX 466318195214",
        "COMERCIALIZADORA ATLANTA CA",
        "COMERCIALIZADORA ATLANTIC CA",
        "INTERNATIONAL",
        "CB INTERNATIONAL TRADING GROUP",
        "KA DK FLOWERS",
        "E FLOWERS",
        "ECOLAB",
        "ECOLAB CHILE",
        "ACEROS ESPECIAL ACES",
        "ACEROS ESPECIALES ACES",
        "MQE",
        "MQE MARIA GONZALES",
        "OMG QUIMICOS",
        "RG QUIMICOS",
    ]
    return pd.Series(relleno + casos)


@pytest.fixture(scope="module")
def comparador() -> SimilitudNombre:
    corpus = _corpus()
    pesos = neutralizar_genericos(construir_idf(corpus), genericos())
    return SimilitudNombre(
        pesos,
        corpus.to_numpy(),
        genericos_estructurales=GENERICOS_ESTRUCTURALES | GENERICOS_GEOGRAFIA,
    )


def _sim01(comparador: SimilitudNombre, a: str, b: str) -> float:
    return float((comparador.compare(np.array([a]), np.array([b]))[0] + 1) / 2)


UMBRAL = 0.84


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Guía de transporte: identifica un ENVÍO, no una empresa.
        ("HINCAPIE SPORTSWEAR", "HINCAPIE SPORTSWEAR FEDEX 453927848470"),
        # Topónimo como único añadido.
        ("ECOLAB", "ECOLAB CHILE"),
        # Errata de digitación.
        ("ACEROS ESPECIAL ACES", "ACEROS ESPECIALES ACES"),
    ],
)
def test_verdaderos_positivos_superan_el_umbral(comparador, a, b):
    assert _sim01(comparador, a, b) >= UMBRAL


@pytest.mark.parametrize(
    ("a", "b", "modo"),
    [
        # Prefijo genérico compartido: JW da 0,97 y son dos empresas.
        ("COMERCIALIZADORA ATLANTA CA", "COMERCIALIZADORA ATLANTIC CA", "prefijo"),
        # Imán genérico: "INTERNATIONAL" absorbía 157 razones sociales.
        ("INTERNATIONAL", "CB INTERNATIONAL TRADING GROUP", "iman"),
        # Palabra de sector como identidad: "FLOWERS" está en 2.739 nombres.
        ("KA DK FLOWERS", "E FLOWERS", "sector"),
        # Consolidador con su cliente final: "MQE" absorbía 214.
        ("MQE", "MQE MARIA GONZALES", "consolidador"),
        # Siglas reunidas: sin unir_iniciales compartían un único token.
        ("OMG QUIMICOS", "RG QUIMICOS", "siglas"),
    ],
)
def test_falsos_positivos_medidos_no_vuelven(comparador, a, b, modo):
    assert _sim01(comparador, a, b) < UMBRAL, modo


def test_desglose_expone_por_que_se_decidio(comparador):
    d = comparador.partes(
        np.array(["HINCAPIE SPORTSWEAR", "INTERNATIONAL"]),
        np.array(["HINCAPIE SPORTSWEAR FEDEX 453927848470", "CB INTERNATIONAL TRADING GROUP"]),
    )
    assert list(d.columns) == [
        "sim_tokens",
        "jaro_winkler",
        "dif_informativa",
        "uso_contencion",
    ]
    assert bool(d["uso_contencion"].iloc[0]) is True  # lo que sobra es ruido
    assert bool(d["uso_contencion"].iloc[1]) is False  # sin token informativo propio


def test_es_simetrico(comparador):
    a, b = "HINCAPIE SPORTSWEAR", "HINCAPIE SPORTSWEAR FEDEX 453927848470"
    assert _sim01(comparador, a, b) == pytest.approx(_sim01(comparador, b, a))


def test_faltante_es_neutro_nunca_evidencia(comparador):
    """F2.4: un vacío jamás puede satisfacer un override."""
    s = comparador.compare(np.array(["", "HINCAPIE SPORTSWEAR"]), np.array(["", ""]))
    assert list(s) == [0.0, 0.0]


def test_vector_vacio(comparador):
    assert len(comparador.compare(np.array([]), np.array([]))) == 0


def test_jaccard_y_contencion_son_lo_que_dicen():
    corpus = pd.Series(["ALFA BETA", "ALFA BETA GAMMA", "ALFA"])
    w = construir_idf(corpus)
    izq, der = np.array([0]), np.array([1])
    assert contencion_idf(w, izq, der)[0] == pytest.approx(1.0)  # A ⊂ B
    assert jaccard_idf(w, izq, der)[0] < 1.0  # simétrico: no
    assert contencion_idf(w, np.array([0]), np.array([0]))[0] == pytest.approx(1.0)


def test_neutralizar_genericos_no_toca_el_original():
    corpus = pd.Series(["COMERCIALIZADORA MARCA", "IMPORTADORA MARCA"])
    w = construir_idf(corpus)
    antes = w.pesos.copy()
    w2 = neutralizar_genericos(w, {"COMERCIALIZADORA", "IMPORTADORA"}, peso=1.0)
    assert np.array_equal(w.pesos, antes)
    i = w.vocabulario["COMERCIALIZADORA"]
    assert w2.pesos[i] == pytest.approx(1.0)
    assert w2._pesos_crudos[i] == pytest.approx(antes[i])


def test_marcar_informativos_excluye_numeros_y_siglas():
    corpus = pd.Series(["MARCA 453927848470 SA", "OTRA CO"])
    w = construir_idf(corpus)
    mascara = marcar_informativos(w, {"SA"}, longitud_minima=3, ignorar_numericos=True)
    assert not mascara[w.vocabulario["453927848470"]]
    assert not mascara[w.vocabulario["SA"]]
    assert mascara[w.vocabulario["MARCA"]]


def test_piso_por_frecuencia_es_monotono():
    w = construir_idf(pd.Series([f"TOKEN{i} FIJO" for i in range(100)]))
    assert piso_por_frecuencia(w, 10) > piso_por_frecuencia(w, 50)
    with pytest.raises(ValueError):
        piso_por_frecuencia(w, 0)


def test_parametros_invalidos_fallan_rapido():
    corpus = pd.Series(["A B", "C D"])
    w = construir_idf(corpus)
    with pytest.raises(ValueError, match="alfa"):
        SimilitudNombre(w, corpus.to_numpy(), alfa=1.5)
    with pytest.raises(ValueError, match="prefix_weight"):
        SimilitudNombre(w, corpus.to_numpy(), prefix_weight=0.9)
