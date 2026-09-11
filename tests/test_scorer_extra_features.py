"""Test aislado del scoring con variables adicionales (v2.7.0, P2 Camino #1).

Verifica el contrato de ``VectorizedScorer`` cuando el perfil define
``extra_features`` (CIUDAD, TELEFONO, ...). Cubre las dos familias de tipos:

- **No firmados** (``categorical``, ``exact_or_zero``, ``token_set_ratio``):
  rango ``[0, 1]``, solo PREMIAN coincidencias. No pueden separar negativos.
- **Firmados** (``categorical_signed``, ``exact_signed``,
  ``token_set_ratio_signed``): rango ``[-1, 1]``, PREMIAN coincidencia y
  PENALIZAN discrepancia. Son los que separan empresas distintas con
  NIT/nombre parecidos pero ciudad distinta.

Los tests trabajan directamente sobre ``_score_batch_vectorized`` (la única
función por la que pasan los tres paths de scoring: memoria, db, streaming),
con umbrales laxos para observar el score crudo sin que el filtro lo oculte.

Propiedad de negocio crítica (la que motivó el ítem del ROADMAP):
``ORGANIZACION CORONA`` (Bogotá) vs ``ORGANIZACION CARVAJAL`` (Cali), con NIT
adyacente y un token compartido, debe quedar por DEBAJO del threshold cuando
CIUDAD entra como ``categorical_signed`` — y por encima cuando no hay features
(reproduciendo la sobre-fusión que el CHANGELOG documentó).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.scorer import VectorizedScorer

# Silenciar logs ruidosos del scorer durante los tests.
logging.getLogger("VectorizedScorer").disabled = True


def _base_profile(**overrides) -> dict:
    """Perfil mínimo de scoring; threshold laxo para ver el score crudo."""
    prof = {
        "weights": {"name": 0.70, "nit": 0.25, "phonetic": 0.05},
        "score_threshold": 0.0,  # no filtra: queremos observar el score
        "max_nit_distance": 10,
        "min_name_similarity": 0.0,
    }
    prof.update(overrides)
    return prof


def _score_one(profile: dict, df: pd.DataFrame, pair: tuple[int, int]) -> float:
    """Devuelve el score de un único par tras pasar por el scorer."""
    scorer = VectorizedScorer(profile)
    result = scorer._score_batch_vectorized(np.array([list(pair)]), df)
    assert not result.empty, "El par no sobrevivió ni con threshold 0.0"
    return float(result["score"].iloc[0])


# ── 1. Paridad: sin extra_features, comportamiento idéntico a v2.6.0 ──────


def test_sin_extra_features_es_noop() -> None:
    """Sin ``extra_features``, el score no cambia respecto al cálculo base."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["EY COLOMBIA", "ERNST AND YOUNG"],
            "NIT_OK": ["900111222", "900111222"],
            "CIUDAD": ["BOGOTA", "BOGOTA"],
        }
    )
    prof_off = _base_profile()
    prof_empty = _base_profile(extra_features=[])
    assert _score_one(prof_off, df, (0, 1)) == _score_one(prof_empty, df, (0, 1))


def test_extra_features_vacio_no_altera_score() -> None:
    """``extra_features=[]`` debe ser bit-a-bit idéntico a no definirlo."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["NOEL", "GALLETAS NOEL"],
            "NIT_OK": ["890444555", "890444555"],
        }
    )
    s_off = _score_one(_base_profile(), df, (0, 1))
    s_empty = _score_one(_base_profile(extra_features=[]), df, (0, 1))
    assert s_off == pytest.approx(s_empty, abs=1e-12)


# ── 2. Tipos NO firmados: solo premian ───────────────────────────────────


def test_categorical_ciudad_igual_suma() -> None:
    """``categorical`` con ciudades iguales SUMA ``weight * 1.0``."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["EY COLOMBIA", "ERNST AND YOUNG"],
            "NIT_OK": ["900111222", "900111222"],
            "CIUDAD": ["BOGOTA", "BOGOTA"],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(extra_features=[{"column": "CIUDAD", "weight": 0.20, "type": "categorical"}]),
        df,
        (0, 1),
    )
    assert con == pytest.approx(min(1.0, base + 0.20), abs=1e-9)


def test_categorical_ciudad_distinta_no_penaliza() -> None:
    """``categorical`` con ciudades distintas NO mueve el score (suma 0.0)."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ORGANIZACION CORONA", "ORGANIZACION CARVAJAL"],
            "NIT_OK": ["860002536", "860007336"],
            "CIUDAD": ["BOGOTA", "CALI"],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(extra_features=[{"column": "CIUDAD", "weight": 0.20, "type": "categorical"}]),
        df,
        (0, 1),
    )
    assert con == pytest.approx(base, abs=1e-9)


def test_categorical_un_nulo_suma_medio() -> None:
    """``categorical`` con una ciudad nula suma ``weight * 0.5``."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ANDERSEN CONSULTING", "ACCENTURE COLOMBIA"],
            "NIT_OK": ["900222333", "900222333"],
            "CIUDAD": ["MEDELLIN", ""],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(extra_features=[{"column": "CIUDAD", "weight": 0.30, "type": "categorical"}]),
        df,
        (0, 1),
    )
    assert con == pytest.approx(min(1.0, base + 0.30 * 0.5), abs=1e-9)


def test_exact_or_zero_telefono_nulo_suma_cero() -> None:
    """``exact_or_zero`` con un teléfono nulo no aporta (suma 0.0)."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["PRODUCTOS NOEL", "GALLETAS NOEL"],
            "NIT_OK": ["890444555", "890444555"],
            "TELEFONO": ["6041112233", ""],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(
            extra_features=[{"column": "TELEFONO", "weight": 0.20, "type": "exact_or_zero"}]
        ),
        df,
        (0, 1),
    )
    assert con == pytest.approx(base, abs=1e-9)


# ── 3. Tipos FIRMADOS: premian y penalizan ───────────────────────────────


def test_categorical_signed_ciudad_distinta_penaliza() -> None:
    """``categorical_signed`` con ciudades distintas RESTA ``weight``."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ORGANIZACION CORONA", "ORGANIZACION CARVAJAL"],
            "NIT_OK": ["860002536", "860007336"],
            "CIUDAD": ["BOGOTA", "CALI"],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(
            extra_features=[{"column": "CIUDAD", "weight": 0.25, "type": "categorical_signed"}]
        ),
        df,
        (0, 1),
    )
    assert con == pytest.approx(max(0.0, base - 0.25), abs=1e-9)
    assert con < base, "Una ciudad distinta DEBE bajar el score con tipo signed"


def test_categorical_signed_ciudad_igual_premia() -> None:
    """``categorical_signed`` con ciudades iguales SUMA ``weight``."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["EY COLOMBIA", "ERNST AND YOUNG"],
            "NIT_OK": ["900111222", "900111222"],
            "CIUDAD": ["BOGOTA", "BOGOTA"],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(
            extra_features=[{"column": "CIUDAD", "weight": 0.25, "type": "categorical_signed"}]
        ),
        df,
        (0, 1),
    )
    assert con == pytest.approx(min(1.0, base + 0.25), abs=1e-9)


def test_categorical_signed_un_nulo_es_neutral() -> None:
    """``categorical_signed`` con un valor nulo NO altera el score (neutral)."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ANDERSEN CONSULTING", "ACCENTURE COLOMBIA"],
            "NIT_OK": ["900222333", "900222333"],
            "CIUDAD": ["MEDELLIN", ""],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(
            extra_features=[{"column": "CIUDAD", "weight": 0.25, "type": "categorical_signed"}]
        ),
        df,
        (0, 1),
    )
    assert con == pytest.approx(base, abs=1e-9)


def test_exact_signed_telefono_distinto_penaliza() -> None:
    """``exact_signed`` con teléfonos distintos (ambos presentes) RESTA peso."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["EMPRESA UNICA", "OTRA EMPRESA UNICA"],
            "NIT_OK": ["901000001", "901000002"],
            "TELEFONO": ["6022223344", "6023334455"],
        }
    )
    base = _score_one(_base_profile(), df, (0, 1))
    con = _score_one(
        _base_profile(
            extra_features=[{"column": "TELEFONO", "weight": 0.15, "type": "exact_signed"}]
        ),
        df,
        (0, 1),
    )
    assert con == pytest.approx(max(0.0, base - 0.15), abs=1e-9)


# ── 4. Función de similitud por componente (unidad pura) ──────────────────


def test_feature_similarity_signed_rango() -> None:
    """``categorical_signed`` devuelve exactamente {-1, 0, +1}."""
    a = np.array(["BOGOTA", "BOGOTA", "BOGOTA", ""])
    b = np.array(["BOGOTA", "CALI", "", "CALI"])
    out = VectorizedScorer._feature_similarity_vectorized(a, b, "categorical_signed")
    np.testing.assert_array_equal(out, np.array([1.0, -1.0, 0.0, 0.0]))


def test_feature_similarity_categorical_rango() -> None:
    """``categorical`` (no firmado) devuelve {0, 0.5, 1}."""
    a = np.array(["BOGOTA", "BOGOTA", "BOGOTA", ""])
    b = np.array(["BOGOTA", "CALI", "", "CALI"])
    out = VectorizedScorer._feature_similarity_vectorized(a, b, "categorical")
    np.testing.assert_array_equal(out, np.array([1.0, 0.0, 0.5, 0.5]))


def test_feature_similarity_tipo_desconocido_es_cero() -> None:
    """Un tipo desconocido devuelve ceros sin romper (degradación segura)."""
    a = np.array(["X", "Y"])
    b = np.array(["X", "Z"])
    out = VectorizedScorer._feature_similarity_vectorized(a, b, "tipo_inexistente")
    np.testing.assert_array_equal(out, np.zeros(2))


def test_feature_similarity_array_vacio() -> None:
    """Arrays vacíos no rompen y devuelven un array vacío."""
    out = VectorizedScorer._feature_similarity_vectorized(
        np.array([]), np.array([]), "categorical_signed"
    )
    assert out.shape == (0,)


# ── 5. Propiedad de negocio: separar el caso negativo CORONA/CARVAJAL ─────


def test_propiedad_corona_carvajal_se_separa_con_signed() -> None:
    """Caso negativo crítico del ROADMAP.

    CORONA-Bogotá vs CARVAJAL-Cali (NIT adyacente, token 'ORGANIZACION'
    compartido) supera el threshold 0.68 SIN variables adicionales (sobre-fusión
    documentada en CHANGELOG), pero cae por debajo cuando CIUDAD entra como
    ``categorical_signed``.
    """
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ORGANIZACION CORONA", "ORGANIZACION CARVAJAL"],
            "NIT_OK": ["860002536", "860007336"],
            "CIUDAD": ["BOGOTA", "CALI"],
        }
    )
    threshold = 0.68
    base = _score_one(_base_profile(score_threshold=0.0), df, (0, 1))
    con = _score_one(
        _base_profile(
            score_threshold=0.0,
            extra_features=[{"column": "CIUDAD", "weight": 0.25, "type": "categorical_signed"}],
        ),
        df,
        (0, 1),
    )
    assert base >= threshold, (
        f"Pre-condición del test: sin features el par debe sobre-fusionarse "
        f"(score {base:.3f} ≥ {threshold}). Si esto falla, el dataset cambió."
    )
    assert con < threshold, (
        f"Con CIUDAD signed el par negativo debe separarse (score {con:.3f} < {threshold})"
    )
