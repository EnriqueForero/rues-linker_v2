"""Pisos de calidad P/R/F1 del motor unificado (v0.13.0, N7).

Pisos fijados desde mediciones reproducibles (scripts/medir_calidad_sintetica.py,
2 vCPU, seeds fijos). Cada piso deja margen anti-flakiness sin dejar pasar una
regresión real. ADVERTENCIA H8 (permanente): sintético calibra la mecánica del
motor; no sustituye ground truth real etiquetado.

Medido al fijar los pisos:
    E1 mixto (3,696 filas, 20% sin NIT):      F1=0.9974 · P=1.000 · R=0.9948
    E2 100% SIN_NIT:                          F1=0.9874 · P=0.9864
    GT denso del repo (12,427 filas, robusto): F1=0.915  · P=0.994 · R=0.847
    (referencia histórica de la ruta clásica calibrada sobre el mismo GT: 0.84)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import record_linkage as rl
from record_linkage import CampoSpec, EsquemaCampos, TipoCampo
from record_linkage.testing.datos_sinteticos import generar_corpus, metricas_pairwise

_DATA = Path(__file__).parent / "data"


def test_calidad_corpus_mixto():
    """CON_NIT/SIN_NIT mezclados con typos, fonética y sufijos alternos."""
    df = generar_corpus(n_entidades=1500, seed=42)
    esq = EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("TELEFONO", TipoCampo.TELEFONO, peso=1.5),
            CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
        ],
        umbral_score=0.60,
        min_concordancias=1,
    )
    m = metricas_pairwise(rl.dedupe_esquema(df, esq).correlativa["ID_GRUPO"], df["ID_ENTIDAD"])
    assert m["f1"] >= 0.98, m
    assert m["precision"] >= 0.98, m


def test_calidad_regimen_sin_nit():
    """El régimen difícil: 100% sin identificador (medido F1=0.9874)."""
    df = generar_corpus(n_entidades=1500, p_sin_nit=1.0, seed=7)
    esq = EsquemaCampos(
        campos=[
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("TELEFONO", TipoCampo.TELEFONO, peso=1.5),
            CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
        ],
        umbral_score=0.60,
        min_concordancias=1,
    )
    m = metricas_pairwise(rl.dedupe_esquema(df, esq).correlativa["ID_GRUPO"], df["ID_ENTIDAD"])
    assert m["f1"] >= 0.96, m


def test_calidad_gt_denso_configuracion_robusta():
    """GT denso del repo (12,427 filas): el default robusto de esquema_rues.

    Grilla medida (v0.13.0):
        umbral=0.60, min_conc=1 → F1=0.308 (P=0.182)  ← el default INGENUO previo
        umbral=0.60, min_conc=2 → F1=0.915 (P=0.994)  ← default actual
    La lección estructural: exigir DOS campos concordantes es la defensa
    contra puentes por nombre en corpus densos. Referencia histórica de la
    ruta clásica calibrada sobre este mismo GT: F1=0.84.
    """
    gt = pd.read_csv(_DATA / "ground_truth_grande.csv", dtype=str).fillna("")
    esq = rl.esquema_rues()  # default robusto v0.13.0 (min_concordancias=2)
    m = metricas_pairwise(rl.dedupe_esquema(gt, esq).correlativa["ID_GRUPO"], gt["ID_GROUP"])
    assert m["f1"] >= 0.88, m
    assert m["precision"] >= 0.98, m


def test_default_ingenuo_documentado_como_peligroso():
    """El modo de falla que motivó el cambio queda FIJADO como regresión:
    con min_concordancias=1 y umbral 0.60 el GT denso sobre-fusiona.
    Si este test 'mejora' solo (F1 alto), la grilla debe re-evaluarse."""
    gt = pd.read_csv(_DATA / "ground_truth_grande.csv", dtype=str).fillna("")
    esq = EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
        ],
        umbral_score=0.60,
        min_concordancias=1,
    )
    m = metricas_pairwise(rl.dedupe_esquema(gt, esq).correlativa["ID_GRUPO"], gt["ID_GROUP"])
    assert m["precision"] < 0.5, (
        "la sobre-fusión del default ingenuo desapareció: re-evaluar la grilla "
        f"y quizás relajar el default robusto ({m})"
    )


def test_generador_es_determinista():
    a = generar_corpus(n_entidades=50, seed=3)
    b = generar_corpus(n_entidades=50, seed=3)
    pd.testing.assert_frame_equal(a, b)
    c = generar_corpus(n_entidades=50, seed=4)
    assert not a.equals(c)


def test_metricas_pairwise_casos_borde():
    assert metricas_pairwise([0, 1, 2], [0, 1, 2])["f1"] == 1.0  # sin pares
    m = metricas_pairwise([0, 0, 1], [0, 0, 0])
    assert m["precision"] == 1.0 and m["recall"] == pytest.approx(1 / 3)
    with pytest.raises(ValueError, match="longitudes"):
        metricas_pairwise([0], [0, 1])
