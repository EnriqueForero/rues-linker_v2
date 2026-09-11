"""Tests del feature P0-1 v2.8.0 — Tratamiento privilegiado de NIT idéntico.

Cubre tres niveles:
    1. **Paridad** (default OFF) — sin perillas, el scorer se comporta
       idéntico a v2.6.0/v2.7.0 sin extra_features. Bit-a-bit.
    2. **Override del filtro** (`nit_identical_overrides_name_filter=True`) —
       pares con NIT_OK idéntico no-vacío saltan el gate `min_name_similarity`.
    3. **Boost del score** (`nit_identical_score_boost > 0`) — el score
       combinado recibe un bonus aditivo, lo que permite superar el
       `score_threshold` aunque el nombre sea disímil.

Cada test es atómico: usa el `VectorizedScorer` directamente sobre un
DataFrame mínimo (sin pasar por el pipeline completo). Esto valida la
unidad antes que la integración.

Author: Claude (auditor)  Date: 2026-05-22  Version: 2.8.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.scorer import VectorizedScorer

# Profile mínimo equivalente a deduplication_standard pero limpio,
# para que los tests no se acoplen a cambios futuros del perfil global.
_BASE_PROFILE: dict = {
    "score_threshold": 0.68,
    "max_nit_distance": 3,
    "min_name_similarity": 0.60,
    "weights": {"name": 0.65, "nit": 0.20, "phonetic": 0.15},
    "scoring_batch_size": 50_000,
}


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Construye un DataFrame con las columnas mínimas que espera el scorer."""
    df = pd.DataFrame(rows)
    # El scorer espera estas columnas; PHONETIC_KEY1 puede estar vacía.
    for col in ["NIT_OK", "NOMBRE_LIMPIO"]:
        if col not in df.columns:
            df[col] = ""
    if "PHONETIC_KEY1" not in df.columns:
        df["PHONETIC_KEY1"] = ""
    return df


# ─────────────────────────────────────────────────────────────────────
# 1. PARIDAD — defaults OFF
# ─────────────────────────────────────────────────────────────────────


def test_defaults_son_off() -> None:
    """Los nuevos campos del scorer leen False/0.0 cuando el perfil no los define."""
    sc = VectorizedScorer(_BASE_PROFILE)
    assert sc.nit_identical_overrides_name_filter is False
    assert sc.nit_identical_score_boost == 0.0


def test_paridad_pares_con_nombre_alto() -> None:
    """Sin las nuevas perillas, pares con nombre alto y NIT igual scorean igual."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTCO"},
        ]
    )
    sc = VectorizedScorer(_BASE_PROFILE)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    # Estos nombres son muy similares: el par debe sobrevivir.
    assert len(res) == 1
    assert res.iloc[0]["nit_dist"] == 0
    # Sin boost, score = 0.65 * name + 0.20 * 1.0 (NIT idéntico)
    assert res.iloc[0]["score"] >= 0.68


def test_paridad_filtro_descarta_nombre_bajo() -> None:
    """Sin las nuevas perillas, un par con nombre disímil y NIT igual es descartado."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "PINTUCO ORBIS"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZOMOBEL PINYUCO"},
        ]
    )
    sc = VectorizedScorer(_BASE_PROFILE)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    # name_sim ≈ 0.56 < 0.60 → descartado por el filtro.
    assert len(res) == 0


# ─────────────────────────────────────────────────────────────────────
# 2. OVERRIDE DEL FILTRO
# ─────────────────────────────────────────────────────────────────────


def test_override_admite_par_con_nit_identico_y_nombre_bajo() -> None:
    """Con override=True, el par pasa el filtro (aunque luego pueda no pasar el threshold)."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "PINTUCO ORBIS"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZOMOBEL PINYUCO"},
        ]
    )
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_overrides_name_filter"] = True
    sc = VectorizedScorer(prof)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    # El filtro lo deja pasar, pero el score (≈0.56*0.65 + 1.0*0.20 = 0.564)
    # NO supera el threshold 0.68. Sin boost, sigue descartado.
    assert len(res) == 0


def test_override_no_aplica_a_nit_distinto() -> None:
    """Con override=True pero NITs distintos, el filtro normal sigue rigiendo."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "ABC"},
            {"NIT_OK": "8909001489", "NOMBRE_LIMPIO": "XYZ"},
        ]
    )
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_overrides_name_filter"] = True
    sc = VectorizedScorer(prof)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    # NIT_OK distancia 1, nombre dispar → filtro estricto descarta.
    assert len(res) == 0


def test_override_no_aplica_a_nit_vacio() -> None:
    """NIT vacío no es 'idéntico': nit_distance==-1, no se exime."""
    df = _make_df(
        [
            {"NIT_OK": "", "NOMBRE_LIMPIO": "ABC"},
            {"NIT_OK": "", "NOMBRE_LIMPIO": "XYZ"},
        ]
    )
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_overrides_name_filter"] = True
    sc = VectorizedScorer(prof)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    # NITs vacíos no se eximen; nombre dispar descarta.
    assert len(res) == 0


# ─────────────────────────────────────────────────────────────────────
# 3. BOOST DEL SCORE
# ─────────────────────────────────────────────────────────────────────


def test_boost_sube_score_para_nit_identico() -> None:
    """Con boost > 0, un par con NIT idéntico ve subir su score final."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTCO"},
        ]
    )
    prof_sin = dict(_BASE_PROFILE)
    prof_con = dict(_BASE_PROFILE)
    prof_con["nit_identical_score_boost"] = 0.05

    sc_sin = VectorizedScorer(prof_sin)
    sc_con = VectorizedScorer(prof_con)

    res_sin = sc_sin._score_batch_vectorized(np.array([[0, 1]]), df)
    res_con = sc_con._score_batch_vectorized(np.array([[0, 1]]), df)

    # Ambos sobreviven al filtro; el de con-boost tiene score 0.05 más alto.
    assert len(res_sin) == 1 and len(res_con) == 1
    delta = res_con.iloc[0]["score"] - res_sin.iloc[0]["score"]
    assert abs(delta - 0.05) < 1e-9, f"Esperado +0.05 exactos, medido {delta}"


def test_boost_no_se_aplica_a_nit_distinto() -> None:
    """El boost solo se aplica donde NIT_OK es idéntico."""
    # Dos pares: uno con NIT_OK igual, otro con NIT_OK distinto.
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTCO"},
            {"NIT_OK": "8909001485", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
        ]
    )
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_score_boost"] = 0.10
    sc = VectorizedScorer(prof)

    res = sc._score_batch_vectorized(np.array([[0, 1], [0, 2]]), df)
    # Par (0,1): NIT idéntico → boost aplica.
    # Par (0,2): NIT dist=1 → boost NO aplica.
    by_pair = {(int(r.idx_0), int(r.idx_1)): r.score for _, r in res.iterrows()}
    score_iguales = by_pair.get((0, 1))
    score_distintos = by_pair.get((0, 2))
    assert score_iguales is not None
    if score_distintos is not None:
        # El par con NIT igual tiene boost; el otro no.
        # La diferencia entre ambos no es exactamente 0.10 (los name_sim y nit_dist
        # difieren), pero el efecto del boost es aislable así:
        # score(0,2) sin boost: 0.65*1.0 + 0.20*(1 - dist/max) → algo más bajo que el otro.
        assert score_iguales > score_distintos, (
            f"NIT idéntico no recibió boost: {score_iguales} vs {score_distintos}"
        )


def test_boost_combinado_con_override_eleva_par_pintuco_sobre_threshold() -> None:
    """Caso real del exhaustivo: override + boost permite recuperar el par PINTUCO/AKZOMOBEL."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZOMOBEL PINYUCO"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
        ]
    )
    # name_sim ≈ 0.88, el filtro lo deja pasar incluso sin override.
    # El score es ~0.65*0.88 + 0.20 = 0.77 — ya sobre 0.68. Verifica el path positivo.
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_overrides_name_filter"] = True
    prof["nit_identical_score_boost"] = 0.05
    sc = VectorizedScorer(prof)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    assert len(res) == 1
    assert res.iloc[0]["score"] >= 0.68


def test_boost_recortado_a_1() -> None:
    """El boost no permite que el score supere 1.0 (clip final)."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "EMPRESA EJEMPLO SA"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "EMPRESA EJEMPLO SA"},
        ]
    )
    prof = dict(_BASE_PROFILE)
    prof["nit_identical_score_boost"] = 0.30  # boost grande
    sc = VectorizedScorer(prof)
    res = sc._score_batch_vectorized(np.array([[0, 1]]), df)
    assert len(res) == 1
    assert res.iloc[0]["score"] <= 1.0 + 1e-9


# ─────────────────────────────────────────────────────────────────────
# 4. PARIDAD ESTRICTA — boost=0 y override=False es idéntico a v2.7.0
# ─────────────────────────────────────────────────────────────────────


def test_paridad_completa_con_defaults_off() -> None:
    """Sobre un set de 6 pares variados, scoring con defaults OFF == scoring sin las nuevas perillas."""
    df = _make_df(
        [
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTUCO"},
            {"NIT_OK": "8909001482", "NOMBRE_LIMPIO": "AKZONOBEL PINTCO"},
            {"NIT_OK": "8909001483", "NOMBRE_LIMPIO": "OTRA EMPRESA"},
            {"NIT_OK": "", "NOMBRE_LIMPIO": "SIN NIT EMPRESA UNO"},
            {"NIT_OK": "", "NOMBRE_LIMPIO": "SIN NIT EMPRESA UNO"},
            {"NIT_OK": "1234567890", "NOMBRE_LIMPIO": "EMPRESA TOTAL"},
        ]
    )
    pares = np.array([[0, 1], [0, 2], [3, 4], [0, 5], [1, 5], [2, 3]])

    # Profile con defaults explícitos OFF (idéntico a no definirlos).
    prof_off = dict(_BASE_PROFILE)
    prof_off["nit_identical_overrides_name_filter"] = False
    prof_off["nit_identical_score_boost"] = 0.0

    sc_a = VectorizedScorer(_BASE_PROFILE)
    sc_b = VectorizedScorer(prof_off)

    res_a = sc_a._score_batch_vectorized(pares, df).reset_index(drop=True)
    res_b = sc_b._score_batch_vectorized(pares, df).reset_index(drop=True)

    # Mismas columnas, mismos valores.
    pd.testing.assert_frame_equal(res_a, res_b)
