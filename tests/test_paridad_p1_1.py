"""Tests P1-1 v2.9.0 — Vectorización del scorer.

Verifica que la versión vectorizada del scorer reproduce bit-a-bit los
outputs del scorer con bucle, sobre un set diverso de casos borde.

El oráculo (`tests/data/oraculo_scorer_p1_1.pkl`) fue capturado con
v2.8.0 antes de tocar el scorer. Si este test falla, alguna ruta del
scorer perdió paridad — investigar antes de mergear.

Cobertura:
    - 120 pares × 3 perfiles = 360 outputs verificados
    - Casos: idénticos, NIT a distancia 0/1/2, NIT vacío, nombres
      disjuntos, refinamiento de score intermedio, bonus por primer
      token, extra_features signed.

Manejo de incompatibilidad de pickle (v3.2.2+):
    Si el oráculo fue generado con una versión de pandas cuya firma de
    StringDtype.__init__ difiere de la del runtime actual (típicamente
    pickle generado en pandas ≥2.3 cargado en pandas 2.2.x), el test
    skipea con mensaje claro indicando cómo regenerar. NO falla la
    publicación: el oráculo es un artefacto de tests, no código de
    producción. Para regenerar localmente:
        python scripts/capturar_oraculo_p1_1.py
"""

from __future__ import annotations

import os
import pickle
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.scorer import VectorizedScorer

ORACULO_PATH = Path(__file__).parent / "data" / "oraculo_scorer_p1_1.pkl"
TOL = 1e-9

_REGEN_HINT = (
    "El oráculo de paridad parece haber sido generado con una versión de "
    "pandas distinta a la actual (pandas={pd_ver}). Regenera ejecutando:\n"
    "    python scripts/capturar_oraculo_p1_1.py\n"
    "Error original: {err}"
)


@pytest.fixture(scope="module")
def oraculo() -> dict:
    if not ORACULO_PATH.exists():
        pytest.skip(f"Oráculo no encontrado: {ORACULO_PATH}")
    try:
        with ORACULO_PATH.open("rb") as f:
            return pickle.load(f)
    except (TypeError, AttributeError, ModuleNotFoundError, ImportError) as e:
        # TypeError: incompatibilidad de firma (p.ej. StringDtype 2 vs 3 args)
        # AttributeError: clase del dtype renombrada entre versiones
        # ModuleNotFoundError/ImportError: dependencia faltante (p.ej. pyarrow)
        # En todos los casos NO es un bug del scorer; es el pickle el problema.
        pytest.skip(_REGEN_HINT.format(pd_ver=pd.__version__, err=f"{type(e).__name__}: {e}"))
        return {}  # pragma: no cover — pytest.skip lanza Skipped


def _correr(scorer: VectorizedScorer, pairs: np.ndarray, df: pd.DataFrame) -> pd.DataFrame:
    with open(os.devnull, "w") as dn, redirect_stdout(dn), redirect_stderr(dn):
        res = scorer._score_batch_vectorized(pairs.copy(), df.copy())
    return res.sort_values(["idx_0", "idx_1"]).reset_index(drop=True)


@pytest.mark.parametrize(
    "perfil", ["default_off", "with_override_and_boost", "with_extra_features"]
)
def test_paridad_bit_a_bit(oraculo, perfil) -> None:
    df = oraculo["df"]
    pairs = oraculo["pairs"]
    prof = dict(oraculo["profiles"][perfil])
    captura = oraculo["capturas"][perfil]

    # v0.19.0 — El oráculo congela la semántica de v2.8.0, en la que un
    # identificador y ese mismo identificador con su dígito de verificación
    # contaban como distintos. Esa comparación cambió a propósito (ver
    # ADR-0004) y la perilla `dv_es_mismo_identificador` permite recuperarla.
    # Este test sigue guardando lo que fue escrito para guardar —que la
    # vectorización del scorer no alteró ningún número— y por eso se corre con
    # la perilla apagada; el comportamiento nuevo lo cubre
    # `test_identificadores_v019.py`.
    prof["dv_es_mismo_identificador"] = False

    scorer = VectorizedScorer(prof)
    actual = _correr(scorer, pairs, df)

    # Mismas filas en mismo orden.
    assert len(actual) == len(captura), (
        f"Perfil {perfil}: distinto número de pares ({len(actual)} vs {len(captura)})"
    )

    # Índices y nit_dist (enteros): exactos.
    np.testing.assert_array_equal(
        actual["idx_0"].to_numpy(),
        captura["idx_0"].to_numpy(),
        err_msg=f"{perfil}: idx_0 no coincide",
    )
    np.testing.assert_array_equal(
        actual["idx_1"].to_numpy(),
        captura["idx_1"].to_numpy(),
        err_msg=f"{perfil}: idx_1 no coincide",
    )
    np.testing.assert_array_equal(
        actual["nit_dist"].to_numpy(),
        captura["nit_dist"].to_numpy(),
        err_msg=f"{perfil}: nit_dist no coincide",
    )

    # Floats: tolerancia 1e-9.
    np.testing.assert_allclose(
        actual["name_sim"].to_numpy(),
        captura["name_sim"].to_numpy(),
        atol=TOL,
        err_msg=f"{perfil}: name_sim fuera de tolerancia {TOL}",
    )
    np.testing.assert_allclose(
        actual["score"].to_numpy(),
        captura["score"].to_numpy(),
        atol=TOL,
        err_msg=f"{perfil}: score fuera de tolerancia {TOL}",
    )


def test_oraculo_cubre_120_pares(oraculo) -> None:
    """El oráculo debe seguir cubriendo los 120 pares originales (16 regs × 15/2)."""
    assert len(oraculo["pairs"]) == 120
    assert len(oraculo["profiles"]) == 3
    for cap in oraculo["capturas"].values():
        assert len(cap) == 120
