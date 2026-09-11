"""Medición reproducible de P/R/F1 del motor unificado (v0.13.0, N7).

Corre los cuatro escenarios de calidad sobre corpus sintético determinista
(``record_linkage.testing.datos_sinteticos``) y el GT grande del repo, e
imprime la tabla. Es la fuente de los PISOS fijados en
``tests/test_calidad_motor_unificado.py``.

Uso:
    python scripts/medir_calidad_sintetica.py

Advertencia H8 (permanente): esto calibra la MECÁNICA del motor sobre
sintético; no sustituye ground truth real etiquetado.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import record_linkage as rl
from record_linkage import CampoSpec, EsquemaCampos, TipoCampo
from record_linkage.matching.comparators import FoneticoEspanolSigned
from record_linkage.testing.datos_sinteticos import (
    generar_corpus,
    metricas_pairwise,
)


def esquema_mixto() -> EsquemaCampos:
    return EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("TELEFONO", TipoCampo.TELEFONO, peso=1.5),
            CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
        ],
        umbral_score=0.60,
        min_concordancias=1,
        nombre="calidad_mixto",
    )


def esquema_sin_nit(*, con_fonetica: bool) -> EsquemaCampos:
    campos = [
        CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
        CampoSpec("TELEFONO", TipoCampo.TELEFONO, peso=1.5),
        CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
    ]
    if con_fonetica:
        campos.insert(
            1,
            CampoSpec(
                "RAZON_SOCIAL_FON",
                TipoCampo.NOMBRE_EMPRESA,
                peso=1.0,
                comparador=FoneticoEspanolSigned(),
            ),
        )
    return EsquemaCampos(
        campos=campos,
        umbral_score=0.55 if con_fonetica else 0.60,
        min_concordancias=1,
        nombre="calidad_sin_nit",
    )


def main() -> None:
    # E1 · corpus mixto CON_NIT/SIN_NIT
    df = generar_corpus(n_entidades=1500, seed=42)
    t0 = time.time()
    res = rl.dedupe_esquema(df, esquema_mixto())
    m1 = metricas_pairwise(res.correlativa["ID_GRUPO"], df["ID_ENTIDAD"])
    print(f"E1 mixto ({len(df):,} filas): {m1} · {time.time() - t0:.1f}s")

    # E2/E3 · 100% SIN_NIT, sin y con campo fonético de apoyo
    df2 = generar_corpus(n_entidades=1500, p_sin_nit=1.0, seed=7)
    res2 = rl.dedupe_esquema(df2, esquema_sin_nit(con_fonetica=False))
    m2 = metricas_pairwise(res2.correlativa["ID_GRUPO"], df2["ID_ENTIDAD"])
    print(f"E2 sin_nit SIN fonética: {m2}")

    df3 = df2.assign(RAZON_SOCIAL_FON=df2["RAZON_SOCIAL"])
    res3 = rl.dedupe_esquema(df3, esquema_sin_nit(con_fonetica=True))
    m3 = metricas_pairwise(res3.correlativa["ID_GRUPO"], df3["ID_ENTIDAD"])
    print(f"E3 sin_nit CON fonética: {m3}")

    # E4 · GT grande del repo (12,427 filas)
    ruta_gt = Path(__file__).resolve().parents[1] / "tests" / "data" / "ground_truth_grande.csv"
    gt = pd.read_csv(ruta_gt, dtype=str).fillna("")
    esq4 = EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("CIUDAD", TipoCampo.CIUDAD, peso=0.5),
        ],
        umbral_score=0.60,
        min_concordancias=1,
        nombre="calidad_gt_grande",
    )
    t0 = time.time()
    res4 = rl.dedupe_esquema(gt, esq4)
    m4 = metricas_pairwise(res4.correlativa["ID_GRUPO"], gt["ID_GROUP"])
    print(f"E4 GT grande ({len(gt):,} filas): {m4} · {time.time() - t0:.1f}s")
    print("Referencia histórica sobre este GT: F1=0.84 (perfil produccion_calibrada)")


if __name__ == "__main__":
    main()
