"""Tests de la fachada unificada ``dedupe_esquema`` (v0.12.0, cierre de H2).

El motor multicampo (EsquemaCampos + normalizadores + comparadores tipados +
bloqueo componible + clusters con cannot-link) existía desde F2 pero no tenía
puerta de entrada de alto nivel: el Orchestrator no lo usaba y el usuario
debía cablear evaluar_esquema + clusters_desde_decisiones a mano.

Contrato:
    1. Funciona con CUALQUIER nombre de columna, sin renombrar nada.
    2. Deriva el bloqueo del esquema (o acepta uno explícito).
    3. Respeta vetos como cannot-link (puentes transitivos cortados).
    4. Calidad sobre el golden set del repo: F1 pairwise >= 0.90.
    5. ResultadoLinkage completo: correlativa, golden, métricas, manifiesto.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import pandas as pd
import pytest

import record_linkage as rl
from record_linkage import CampoSpec, EsquemaCampos, TipoCampo

_DATA = Path(__file__).parent / "data"


def _esquema_generico() -> EsquemaCampos:
    return EsquemaCampos(
        campos=[
            CampoSpec("TAX_ID", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("COMPANY", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec("PHONE", TipoCampo.TELEFONO, peso=1.0),
        ],
        umbral_score=0.60,
        min_concordancias=1,
        nombre="generico_test",
    )


def test_columnas_arbitrarias_sin_renombrar():
    """La universalidad de verdad: columnas TAX_ID/COMPANY/PHONE tal cual."""
    df = pd.DataFrame(
        {
            "TAX_ID": ["900111222", "900111222", "900333444", ""],
            "COMPANY": ["ACME SAS", "ACME S.A.S.", "BETA LTDA", "GAMMA SA"],
            "PHONE": ["3001234567", "3001234567", "3009999999", ""],
        }
    )
    res = rl.dedupe_esquema(df, _esquema_generico())
    et = res.correlativa["ID_GRUPO"]
    assert et[0] == et[1], "duplicado obvio no agrupado"
    assert et[2] != et[0] and et[3] != et[0]
    assert res.metricas["n_grupos"] == 3
    assert list(res.correlativa.columns[:3]) == ["TAX_ID", "COMPANY", "PHONE"]
    assert len(res.golden) == 3
    # Manifiesto serializa el esquema completo (auditoría).
    campos_manifiesto = {c["nombre"] for c in res.manifiesto["parametros"]["esquema"]["campos"]}
    assert campos_manifiesto == {"TAX_ID", "COMPANY", "PHONE"}


def test_default_rues_cuando_hay_columnas_canonicas():
    df = pd.DataFrame(
        {
            "NIT": ["900111222", "900111222"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S A S"],
        }
    )
    res = rl.dedupe_esquema(df)
    assert res.metricas["n_grupos"] == 1
    assert res.manifiesto["parametros"]["esquema"]["nombre"] == "rues"


def test_sin_esquema_ni_columnas_rues_lanza():
    df = pd.DataFrame({"X": ["a"], "Y": ["b"]})
    with pytest.raises(ValueError, match="EsquemaCampos"):
        rl.dedupe_esquema(df)


def test_veto_nit_corta_puente_transitivo():
    """Dos NITs distintos jamás quedan juntos, ni vía un tercero sin NIT."""
    df = pd.DataFrame(
        {
            "TAX_ID": ["900111222", "", "900333444"],
            "COMPANY": ["KANGNAM COMERCIAL SAS", "KANGNAM COMERCIAL", "KANGNAM COMERCIAL SA"],
            "PHONE": ["", "", ""],
        }
    )
    esq = _esquema_generico()
    res = rl.dedupe_esquema(df, esq, respetar_vetos=True)
    et = res.correlativa["ID_GRUPO"]
    assert et[0] != et[2], "cannot-link violado por puente transitivo"
    assert res.metricas["n_vetos"] >= 1


def test_calidad_golden_truth_f1_minimo():
    """Sobre el golden set del repo (270 filas, 36 grupos), F1 pairwise ≥ 0.90.

    Medido al escribir este test: F1=0.9937 (P=0.9875, R=1.0000). El umbral
    0.90 deja margen anti-flakiness sin dejar pasar una regresión real.
    """
    gt = pd.read_csv(_DATA / "golden_truth.csv", dtype=str).fillna("")
    esq = EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
        ],
        umbral_score=0.60,
        min_concordancias=1,
        nombre="rues_gt",
    )
    res = rl.dedupe_esquema(gt, esq)
    pred = res.correlativa["ID_GRUPO"].to_numpy()
    verdad = gt["ID_GROUP"].to_numpy()

    def pares(labels):
        grupos: dict = {}
        for idx, g in enumerate(labels):
            grupos.setdefault(g, []).append(idx)
        return {p for miembros in grupos.values() for p in combinations(miembros, 2)}

    p_pred, p_true = pares(pred), pares(verdad)
    tp = len(p_pred & p_true)
    precision = tp / len(p_pred) if p_pred else 1.0
    recall = tp / len(p_true) if p_true else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    assert f1 >= 0.90, f"F1={f1:.3f} (P={precision:.3f}, R={recall:.3f}) < 0.90"


def test_bloqueo_explicito_se_respeta():
    from record_linkage.matching.motor_bloqueo import BloqueoComponible, LlaveExacta

    df = pd.DataFrame(
        {
            "TAX_ID": ["900111222", "900111222", "900333444"],
            "COMPANY": ["ACME SAS", "ACME S.A.S.", "ACME SAS"],
            "PHONE": ["", "", ""],
        }
    )
    # Solo llave exacta por TAX_ID: el tercero (NIT distinto) ni se compara.
    res = rl.dedupe_esquema(
        df, _esquema_generico(), bloqueo=BloqueoComponible([LlaveExacta("TAX_ID")])
    )
    assert res.metricas["bloqueo"] == ["llave_exacta[TAX_ID]"]
    assert res.metricas["n_grupos"] == 2


def test_incluir_desglose_para_auditoria():
    df = pd.DataFrame(
        {
            "TAX_ID": ["900111222", "900111222"],
            "COMPANY": ["ACME SAS", "ACME S A S"],
            "PHONE": ["", ""],
        }
    )
    res = rl.dedupe_esquema(df, _esquema_generico(), incluir_desglose=True)
    assert "decisiones" in res.metricas and "desglose" in res.metricas
    assert {"i", "j", "score", "fusion"}.issubset(res.metricas["decisiones"].columns)
