"""Restricción cannot-link por identificador válido (v0.14.0)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.cannot_link import (
    ReporteCannotLink,
    aplicar_cannot_link_identificador,
)

#: Caso real de la corrida RUES x Exportaciones DANE: un registro sin NIT
#: unía por transitividad dos NIT válidos distintos.
CASO_PUENTE = pd.DataFrame(
    {
        "ID_GRUPO": [5856, 5856, 5856, 5856],
        "RAZON_SOCIAL": [
            "ARTESANIAS M & M E U",
            "CONSTRUCTORA Y SUMINISTROS  M & M LTDA",
            "ARTESANIAS M & M E.U",
            "COMERCIAL COLOMBIA M&M SAS",
        ],
        "NIT_BASE": ["900393694", "", "900393694", "901491955"],
        "NIT_VALID": [1, 0, 1, 1],
    }
)


def test_separa_el_caso_real_y_aisla_el_puente() -> None:
    salida, reporte = aplicar_cannot_link_identificador(CASO_PUENTE)
    grupos = salida["ID_GRUPO"].tolist()
    # Las dos filas de ARTESANIAS (mismo NIT) siguen juntas.
    assert grupos[0] == grupos[2]
    # COMERCIAL COLOMBIA se separa, y el puente queda solo.
    assert len({grupos[0], grupos[1], grupos[3]}) == 3
    assert reporte.grupos_en_conflicto == 1
    assert reporte.puentes_aislados == 1
    assert reporte.hubo_cambios


def test_no_toca_lo_que_ya_es_coherente() -> None:
    df = pd.DataFrame(
        {
            "ID_GRUPO": [1, 1, 2],
            "NIT_BASE": ["900111222", "900111222", "800333444"],
            "NIT_VALID": [1, 1, 1],
        }
    )
    salida, reporte = aplicar_cannot_link_identificador(df)
    assert salida["ID_GRUPO"].tolist() == [1, 1, 2]
    assert not reporte.hubo_cambios
    assert reporte.resumen() == "cannot-link: sin conflictos de identificador"


def test_identificadores_invalidos_no_generan_conflicto() -> None:
    """Dos NIT distintos pero NO validados: no hay evidencia dura, no se parte."""
    df = pd.DataFrame(
        {
            "ID_GRUPO": [3, 3],
            "NIT_BASE": ["900111222", "900111333"],
            "NIT_VALID": [0, 0],
        }
    )
    _, reporte = aplicar_cannot_link_identificador(df)
    assert not reporte.hubo_cambios


def test_conserva_cardinalidad_y_no_muta_la_entrada() -> None:
    original = CASO_PUENTE.copy(deep=True)
    salida, _ = aplicar_cannot_link_identificador(CASO_PUENTE)
    assert len(salida) == len(CASO_PUENTE)
    pd.testing.assert_frame_equal(CASO_PUENTE, original)


def test_es_determinista_e_idempotente() -> None:
    primera, _ = aplicar_cannot_link_identificador(CASO_PUENTE)
    segunda, _ = aplicar_cannot_link_identificador(CASO_PUENTE)
    assert primera["ID_GRUPO"].tolist() == segunda["ID_GRUPO"].tolist()
    tercera, reporte = aplicar_cannot_link_identificador(primera)
    assert tercera["ID_GRUPO"].tolist() == primera["ID_GRUPO"].tolist()
    assert not reporte.hubo_cambios, "una segunda pasada no debe cambiar nada"


def test_el_grupo_mayoritario_conserva_su_etiqueta() -> None:
    df = pd.DataFrame(
        {
            "ID_GRUPO": [9, 9, 9],
            "NIT_BASE": ["800111222", "800111222", "900999888"],
            "NIT_VALID": [1, 1, 1],
        }
    )
    salida, _ = aplicar_cannot_link_identificador(df)
    assert salida["ID_GRUPO"].tolist()[:2] == [9, 9]
    assert salida["ID_GRUPO"].iloc[2] != 9


@pytest.mark.parametrize("validez", [[True, True], ["1", "1"], ["true", "TRUE"], [1.0, 1.0]])
def test_validez_en_cualquier_representacion(validez) -> None:
    df = pd.DataFrame(
        {"ID_GRUPO": [1, 1], "NIT_BASE": ["900111222", "800333444"], "NIT_VALID": validez}
    )
    _, reporte = aplicar_cannot_link_identificador(df)
    assert reporte.grupos_en_conflicto == 1


def test_sin_columna_de_validez_usa_identificador_no_vacio() -> None:
    df = pd.DataFrame({"ID_GRUPO": [1, 1, 1], "NIT_BASE": ["900111222", "", "800333444"]})
    _, reporte = aplicar_cannot_link_identificador(df, columna_valido=None)
    assert reporte.grupos_en_conflicto == 1
    assert reporte.puentes_aislados == 1


def test_falta_de_columna_falla_con_mensaje_accionable() -> None:
    with pytest.raises(KeyError, match="NIT_BASE"):
        aplicar_cannot_link_identificador(pd.DataFrame({"ID_GRUPO": [1]}))


def test_reporte_vacio_es_falsy_en_resumen() -> None:
    assert not ReporteCannotLink().hubo_cambios


def test_escala_lineal_sin_conflictos() -> None:
    """100k filas sin conflicto: la ruta rápida no debe recorrer grupos."""
    n = 100_000
    df = pd.DataFrame(
        {
            "ID_GRUPO": np.arange(n) // 2,
            "NIT_BASE": np.repeat([f"9{i:08d}" for i in range(n // 2)], 2),
            "NIT_VALID": 1,
        }
    )
    salida, reporte = aplicar_cannot_link_identificador(df)
    assert not reporte.hubo_cambios
    assert salida["ID_GRUPO"].nunique() == n // 2
