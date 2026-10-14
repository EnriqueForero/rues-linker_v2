"""Fase 3: la perilla ``comparador_nombre`` elige el comparador de L3 y el legado sigue intacto."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from record_linkage.config.profiles import PERFILES_BASE
from record_linkage.engine.scorer import COMPARADORES_NOMBRE, VectorizedScorer
from record_linkage.matching.idf import construir_idf
from record_linkage.matching.soft_tfidf import similitud_soft_tfidf

logging.getLogger("VectorizedScorer").disabled = True

NOMBRES = [
    "LAURA RODRIGUEZ",
    "RODRIGUEZ",
    "KRONOS EMPRESARIAL VALDORIA SAS",
    "KRONOS EMPRESARIAL VALDORIA",
    "ZELVORA ALIMENTOS SAS",
    "DULCEVENT NORTE SAS",
    "TRIVENTA INVERSIONES SAS",
    "TRIVENTE INVERSIONES SAS",
    "PELUVIKA SAS",
    "ORBITAL TEXTIL SAS",
]


def _df() -> pd.DataFrame:
    return pd.DataFrame({"NOMBRE_LIMPIO": NOMBRES, "NIT_OK": [""] * len(NOMBRES)})


def _perfil(**extra) -> dict:
    perfil = {
        "weights": {"name": 0.5, "nit": 0.5, "phonetic": 0.0},
        "score_threshold": 0.0,
        "max_nit_distance": 10,
        "min_name_similarity": 0.0,
    }
    perfil.update(extra)
    return perfil


def _name_sim(perfil: dict, pares: list[tuple[int, int]]) -> list[float]:
    resultado = VectorizedScorer(perfil)._score_batch_vectorized(np.array(pares), _df())
    assert len(resultado) == len(pares)
    return resultado.sort_values(["idx_0", "idx_1"])["name_sim"].tolist()


def test_produccion_estandar_usa_soft_tfidf_y_el_legado_sigue_disponible():
    """ADR-0015: el perfil de producción compara con Soft TF-IDF (0,85) y respaldo compactado 0,93."""
    perfil = PERFILES_BASE["produccion_estandar"]
    assert perfil["comparador_nombre"] == "soft_tfidf"
    assert perfil["soft_tfidf_umbral_token"] == 0.85
    assert perfil["similitud_compacta_min"] == 0.93
    assert perfil["nit_identical_overrides_name_filter"] is True
    # Sin perilla en el perfil, el scorer sigue en el legado: la contención vale 1,0.
    assert _name_sim(_perfil(), [(0, 1)]) == [1.0]
    assert _name_sim(_perfil(comparador_nombre="legado"), [(0, 1)]) == [1.0]


def test_soft_tfidf_castiga_la_contencion_y_tolera_la_errata():
    sims = _name_sim(_perfil(comparador_nombre="soft_tfidf"), [(0, 1), (2, 3), (6, 7)])
    contencion, sufijo_generico, errata = sims
    assert contencion < 0.9
    assert sufijo_generico > 0.94
    assert 0.9 <= errata < 1.0
    # Es exactamente el comparador de matching/soft_tfidf.py sobre el IDF del corpus completo.
    pesos = construir_idf(_df()["NOMBRE_LIMPIO"])
    esperado = similitud_soft_tfidf(
        pesos,
        np.array([NOMBRES[0], NOMBRES[2], NOMBRES[6]], dtype=object),
        np.array([NOMBRES[1], NOMBRES[3], NOMBRES[7]], dtype=object),
    )
    assert sims == pytest.approx(esperado.tolist())


def test_los_nombres_identicos_valen_uno_con_los_dos_comparadores():
    df = _df()
    df.loc[1, "NOMBRE_LIMPIO"] = NOMBRES[0]
    for comparador in sorted(COMPARADORES_NOMBRE):
        res = VectorizedScorer(_perfil(comparador_nombre=comparador))._score_batch_vectorized(
            np.array([[0, 1]]), df
        )
        assert res["name_sim"].tolist() == [1.0]


def test_un_comparador_desconocido_falla_al_construir_con_mensaje_accionable():
    with pytest.raises(ValueError, match=r"comparador_nombre=.otro. no existe.*legado.*soft_tfidf"):
        VectorizedScorer(_perfil(comparador_nombre="otro"))


def test_la_perilla_y_su_umbral_existen_en_todos_los_perfiles_base():
    for nombre, perfil in PERFILES_BASE.items():
        assert perfil["comparador_nombre"] in COMPARADORES_NOMBRE, nombre
        assert 0.0 < perfil["soft_tfidf_umbral_token"] <= 1.0, nombre
        # El scorer ya leía esta perilla; ahora todo perfil base la declara y --ajuste la acepta.
        assert isinstance(perfil["nit_identical_overrides_name_filter"], bool), nombre
