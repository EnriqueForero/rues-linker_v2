"""Baseline de no-regresión del motor multicampo (F2.7).

Reproduce el pipeline multicampo completo sobre el GT sintético v3 y lo
compara contra ``tests/data/baseline_multicampo.json``. Mismo patrón que el
harness RUES: medir → JSON → test. El JSON NO se regenera sin acta.

Verifica además los umbrales duros del gate F2: PC ≥ 0.98, F1 ≥ 0.85,
controles negativos mal fusionados = 0.
"""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.campos import esquema_multicampo_completo
from record_linkage.matching.motor_bloqueo import (
    BloqueoComponible,
    LlaveExacta,
    LSHTexto,
)
from record_linkage.matching.motor_multicampo import (
    clusters_desde_decisiones,
    evaluar_esquema,
)
from record_linkage.matching.normalizadores import normalizar_campo
from record_linkage.testing.gt_multicampo import ConfigGT, generar_gt_multicampo

_BASELINE = Path(__file__).parent / "data" / "baseline_multicampo.json"


def _pares(labels: np.ndarray) -> set[tuple[int, int]]:
    s = pd.Series(labels)
    out: set[tuple[int, int]] = set()
    for _, idx in s.groupby(s).groups.items():
        a = sorted(np.asarray(idx).tolist())
        if len(a) > 1:
            out.update(combinations(a, 2))
    return out


def _correr() -> dict:
    df = generar_gt_multicampo(ConfigGT(seed=42))
    esq = esquema_multicampo_completo()
    bloq = BloqueoComponible(
        [
            LlaveExacta("NIT"),
            LlaveExacta("EMAIL"),
            LlaveExacta("TELEFONO"),
            LSHTexto("RAZON_SOCIAL", umbral=0.35, permutaciones=64, ngram=3),
        ]
    )
    val = {c.nombre: normalizar_campo(df[c.nombre], c).to_numpy() for c in esq.campos}
    union, por_est = bloq.pares(val)
    mb = BloqueoComponible.medir(por_est, union, df["ID_ENTIDAD"].to_numpy())
    res = evaluar_esquema(df, esq, bloq)
    lab = clusters_desde_decisiones(len(df), res.decisiones, respetar_vetos=True)

    verdaderos = _pares(df["ID_ENTIDAD"].to_numpy())
    predichos = _pares(lab)
    tp = len(predichos & verdaderos)
    fp = len(predichos - verdaderos)
    fn = len(verdaderos - predichos)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    negativos = df.index[df["ID_ENTIDAD"] >= 135].tolist()
    viol = sum(
        1
        for a in range(len(negativos))
        for b in range(a + 1, len(negativos))
        if lab[negativos[a]] == lab[negativos[b]]
        and df.loc[negativos[a], "ID_ENTIDAD"] != df.loc[negativos[b], "ID_ENTIDAD"]
    )
    return {
        "pc": mb["combinada"]["pc"],
        "precision": prec,
        "recall": rec,
        "f1": f1,
        "negativos": viol,
        "grupos": len(np.unique(lab)),
    }


@pytest.fixture(scope="module")
def medicion() -> dict:
    return _correr()


def test_gate_f2_pc_bloqueo(medicion: dict) -> None:
    assert medicion["pc"] >= 0.98, f"PC={medicion['pc']:.4f} < 0.98 (gate F2)"


def test_gate_f2_f1_global(medicion: dict) -> None:
    assert medicion["f1"] >= 0.85, f"F1={medicion['f1']:.4f} < 0.85 (gate F2)"


def test_gate_f2_controles_negativos(medicion: dict) -> None:
    assert medicion["negativos"] == 0, (
        f"{medicion['negativos']} controles negativos mal fusionados (debe ser 0)"
    )


def test_no_regresion_vs_baseline(medicion: dict) -> None:
    """Coincidencia estricta con el baseline congelado (tolerancia numérica mínima)."""
    base = json.loads(_BASELINE.read_text(encoding="utf-8"))
    assert medicion["pc"] == pytest.approx(base["bloqueo"]["pc_combinada"], abs=1e-4)
    q = base["calidad_pair_level"]
    assert medicion["precision"] == pytest.approx(q["precision"], abs=1e-4)
    assert medicion["recall"] == pytest.approx(q["recall"], abs=1e-4)
    assert medicion["f1"] == pytest.approx(q["f1"], abs=1e-4)
    assert medicion["negativos"] == base["controles_negativos_mal_fusionados"]
    assert medicion["grupos"] == base["clustering"]["grupos_formados"]
