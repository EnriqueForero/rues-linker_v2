"""Fase 3: el comparador Soft TF-IDF no confunde contención con identidad ni castiga las erratas."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.idf import construir_idf
from record_linkage.matching.soft_tfidf import similitud_soft_tfidf

# Corpus inventado: SAS aparece casi en todos (genérico); los demás tokens son raros.
CORPUS = [
    "KRONOS EMPRESARIAL VALDORIA SAS",
    "ZELVORA ALIMENTOS SAS",
    "DULCEVENT NORTE SAS",
    "TRIVENTA INVERSIONES SAS",
    "PELUVIKA SAS",
    "LAURA RODRIGUEZ",
    "RODRIGUEZ",
    "INVERSIONES FENIX CAFE SAS",
    "CAFE SAS",
    "BANCO BOGOTA SAS",
    "MARMOLERIA QUINTAL SAS",
    "ORBITAL TEXTIL SAS",
]


@pytest.fixture(scope="module")
def pesos():
    return construir_idf(pd.Series(CORPUS))


def _sim(pesos, a, b, **kw):
    return float(
        similitud_soft_tfidf(pesos, np.array([a], dtype=object), np.array([b], dtype=object), **kw)[
            0
        ]
    )


def _idf(pesos, token):
    return float(pesos.pesos[pesos.vocabulario[token]])


def test_el_mismo_conjunto_de_tokens_vale_uno_sin_importar_el_orden(pesos):
    assert _sim(
        pesos, "KRONOS EMPRESARIAL VALDORIA", "KRONOS EMPRESARIAL VALDORIA"
    ) == pytest.approx(1.0)
    assert _sim(
        pesos, "VALDORIA KRONOS EMPRESARIAL", "KRONOS EMPRESARIAL VALDORIA"
    ) == pytest.approx(1.0)
    assert _sim(pesos, "KRONOS KRONOS VALDORIA", "KRONOS VALDORIA") == pytest.approx(1.0)


def test_la_contencion_no_es_identidad_y_sigue_la_formula_del_coseno(pesos):
    w_l, w_r = _idf(pesos, "LAURA"), _idf(pesos, "RODRIGUEZ")
    esperado = w_r / np.sqrt(w_l**2 + w_r**2)
    assert _sim(pesos, "RODRIGUEZ", "LAURA RODRIGUEZ") == pytest.approx(esperado)
    assert esperado < 0.9  # con el umbral de hoy (name ≥ 0,9 sin NIT) este par ya no se une


def test_un_token_raro_que_falta_pesa_mas_que_un_generico_que_falta(pesos):
    con_generico = _sim(pesos, "KRONOS EMPRESARIAL VALDORIA", "KRONOS EMPRESARIAL VALDORIA SAS")
    con_raro = _sim(pesos, "INVERSIONES FENIX CAFE SAS", "CAFE SAS")
    assert con_generico > 0.95
    assert con_raro < 0.6


def test_una_errata_dentro_de_un_token_conserva_casi_toda_la_evidencia(pesos):
    sim = _sim(pesos, "TRIVENTA INVERSIONES", "TRIVENTE INVERSIONES")
    assert 0.9 <= sim < 1.0
    # El mismo par, sin tolerancia, pierde el token con errata.
    assert _sim(pesos, "TRIVENTA INVERSIONES", "TRIVENTE INVERSIONES", umbral_token=1.0) < sim


def test_el_emparejamiento_es_uno_a_uno(pesos):
    # Dos tokens de A parecidos a un solo token de B no pueden cobrarse los dos: solo cuenta el
    # emparejamiento exacto KRONOS con KRONOS; KRONOX (fuera del vocabulario) queda sin pareja y resta.
    w_k, w_x = _idf(pesos, "KRONOS"), pesos.idf_desconocido
    assert _sim(pesos, "KRONOS KRONOX", "KRONOS") == pytest.approx(w_k / np.sqrt(w_k**2 + w_x**2))


def test_sin_tokens_utiles_o_corpus_vacio_da_cero(pesos):
    assert _sim(pesos, "", "KRONOS") == 0.0
    assert _sim(pesos, "A", "KRONOS") == 0.0  # un solo carácter no cuenta
    vacio = construir_idf(pd.Series(["", ""]))
    assert similitud_soft_tfidf(
        vacio, np.array(["X"], dtype=object), np.array(["X"], dtype=object)
    ).tolist() == [0.0]
    assert similitud_soft_tfidf(
        pesos, np.array([], dtype=object), np.array([], dtype=object)
    ).shape == (0,)


def test_un_lote_devuelve_un_valor_por_par_en_orden(pesos):
    izq = np.array(
        ["KRONOS EMPRESARIAL VALDORIA", "RODRIGUEZ", "TRIVENTA INVERSIONES", "ZELVORA"],
        dtype=object,
    )
    der = np.array(
        ["KRONOS EMPRESARIAL VALDORIA SAS", "LAURA RODRIGUEZ", "TRIVENTE INVERSIONES", "ORBITAL"],
        dtype=object,
    )
    lote = similitud_soft_tfidf(pesos, izq, der)
    assert lote.shape == (4,)
    assert lote.tolist() == pytest.approx(
        [_sim(pesos, a, b) for a, b in zip(izq, der, strict=True)]
    )
    assert lote[3] == 0.0
    assert (lote >= 0).all() and (lote <= 1).all()


def test_falla_rapido_ante_largos_distintos_o_umbral_invalido(pesos):
    with pytest.raises(ValueError, match="largos distintos"):
        similitud_soft_tfidf(pesos, np.array(["A B"], dtype=object), np.array([], dtype=object))
    with pytest.raises(ValueError, match="umbral_token"):
        _sim(pesos, "KRONOS", "KRONOS", umbral_token=0.0)
