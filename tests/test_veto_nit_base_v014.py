"""Veto duro por NIT base válido distinto (v0.14.0).

Regresión del defecto reproducido sobre datos reales RUES x Exportaciones
DANE: tres empresas con NIT válido distinto ("CEGID COLOMBIA LTDA",
"HLF COLOMBIA LTDA", "SILESIA COLOMBIA LTDA") terminaban en un mismo grupo
porque sus NIT distan 1-3 dígitos y sus nombres comparten el patrón
"<X> COLOMBIA LTDA". Un NIT cuyo dígito de verificación cuadra no es un
error de digitación.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import record_linkage as rl
from record_linkage.engine.scorer import VectorizedScorer, _a_booleano

#: Caso real observado en la corrida RUES x DANE (NIT y nombres verbatim).
CASO_REAL = pd.DataFrame(
    {
        "NIT": ["900189962", "900172963", "900179968", "9001799688", "9001729631"],
        "RAZON_SOCIAL": [
            "CEGID COLOMBIA LTDA",
            "HLF COLOMBIA LTDA",
            "SILESIA COLOMBIA LTDA",
            "SILESIA COLOMBIA LTDA",
            "HLF COLOMBIA LTDA",
        ],
    }
)


def _perfil(**extra):
    base = {
        "score_threshold": 0.45,
        "max_nit_distance": 3,
        "min_name_similarity": 0.3,
        "name_weight": 0.7,
        "nit_weight": 0.3,
    }
    base.update(extra)
    return base


def _frame_pares() -> pd.DataFrame:
    """El par exacto que se fusionaba: distancia de NIT 3 (= ``max_nit_distance``).

    Sin veto obtiene score 0,742 y funde dos empresas reales distintas. Es el
    par que hay que vetar; los otros dos del trío entraban por transitividad.
    """
    return pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["CEGID COLOMBIA LTDA", "SILESIA COLOMBIA LTDA"],
            "NIT_OK": ["9001899627", "9001799688"],
            "NIT_BASE": ["900189962", "900179968"],
            "NIT_VALID": [True, True],
        }
    )


def test_veto_activo_descarta_el_par() -> None:
    scorer = VectorizedScorer(profile=_perfil())
    resultado = scorer.score_pairs({(0, 1)}, _frame_pares())
    assert resultado.empty, "un par con NIT válido distinto no debe puntuarse"
    assert scorer._vetos_nit_base == 1


def test_veto_desactivado_restaura_comportamiento_previo() -> None:
    scorer = VectorizedScorer(profile=_perfil(veto_nit_base_distinto=False))
    resultado = scorer.score_pairs({(0, 1)}, _frame_pares())
    assert not resultado.empty, "sin veto, el par volvía a fusionarse (defecto histórico)"


def test_veto_no_toca_el_mismo_nit_con_dv_distinto() -> None:
    """Misma base y DV declarado distinto SÍ conserva la tolerancia de typo."""
    df = pd.DataFrame(
        {
            "NOMBRE_LIMPIO": ["ACME COLOMBIA SAS", "ACME COLOMBIA SAS"],
            "NIT_OK": ["9001899627", "9001899620"],  # misma base, DV distinto
            "NIT_BASE": ["900189962", "900189962"],
            "NIT_VALID": [True, True],
        }
    )
    scorer = VectorizedScorer(profile=_perfil())
    assert not scorer.score_pairs({(0, 1)}, df).empty
    assert scorer._vetos_nit_base == 0


def test_veto_no_aplica_si_algun_nit_es_invalido() -> None:
    """Ante la duda no se veta: sin DV validado, manda la tolerancia previa."""
    df = _frame_pares()
    df.loc[1, "NIT_VALID"] = False
    scorer = VectorizedScorer(profile=_perfil())
    assert not scorer.score_pairs({(0, 1)}, df).empty
    assert scorer._vetos_nit_base == 0


def test_sin_columnas_de_validacion_es_no_op() -> None:
    """Rutas que no pasan por NitProcessor no deben romperse."""
    df = _frame_pares().drop(columns=["NIT_BASE", "NIT_VALID"])
    scorer = VectorizedScorer(profile=_perfil())
    assert not scorer.score_pairs({(0, 1)}, df).empty


@pytest.mark.parametrize(
    ("valores", "esperado"),
    [
        (np.array([True, False]), [True, False]),
        (np.array(["1", "0", "true", "basura"]), [True, False, True, False]),
        (pd.Series([1, 0, None]).to_numpy(), [True, False, False]),
        (pd.Series(["", None], dtype="object").to_numpy(), [False, False]),
    ],
)
def test_coercion_booleana_robusta(valores, esperado) -> None:
    assert _a_booleano(valores).tolist() == esperado


def test_end_to_end_no_fusiona_nits_validos_distintos() -> None:
    """El caso real completo, por la fachada pública."""
    resultado = rl.dedupe(CASO_REAL.copy(), mode="AGRESIVO")
    correlativa = resultado.correlativa
    grupos = correlativa.groupby("ID_GRUPO")["NIT_BASE"].nunique()
    assert (grupos <= 1).all(), (
        "ningún grupo puede contener dos NIT base válidos distintos: "
        f"{correlativa[['NIT', 'RAZON_SOCIAL', 'ID_GRUPO']].to_dict('records')}"
    )
    # Y las dos parejas legítimas (mismo NIT en ambas fuentes) SÍ se unen.
    assert correlativa["ID_GRUPO"].nunique() == 3


# ── Las salvaguardas deben ser configurables por el camino documentado ──


def test_las_salvaguardas_estan_declaradas_en_todos_los_perfiles() -> None:
    """Si no están en el perfil, `ajustes_perfil` las rechaza como desconocidas.

    Defecto encontrado al ejecutar la plantilla: ambas se leían con
    `profile.get(...)` y funcionaban por defecto, pero no podían configurarse
    ni verse — que es tanto como no ser configurables.
    """
    from record_linkage.config.profiles import PERFILES_BASE

    for nombre, perfil in PERFILES_BASE.items():
        assert perfil.get("veto_nit_base_distinto") is True, nombre
        assert perfil.get("cannot_link_identificador") is True, nombre


def test_se_pueden_apagar_por_ajustes_de_perfil() -> None:
    from record_linkage.config.profiles import crear_config_orchestrator

    cfg = crear_config_orchestrator(
        perfil="prueba_rapida",
        validate=False,
        veto_nit_base_distinto=False,
        cannot_link_identificador=False,
    )
    perfil = cfg["profiles"]["prueba_rapida"]
    assert perfil["veto_nit_base_distinto"] is False
    assert perfil["cannot_link_identificador"] is False
