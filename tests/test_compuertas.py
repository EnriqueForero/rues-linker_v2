"""Las compuertas numéricas de la ruta (docs/evidencia/compuertas.json) contra la evidencia comprometida.

Regla: el «piso» de cada compuerta es lo que la evidencia que vive en el repositorio cumple hoy. Si una medición nueva se
compromete por debajo del piso, esta prueba falla: nadie baja una cifra sin declararlo (y sin bajar el piso en el JSON con su
razón). Los «objetivos» no se exigen aquí: son la meta de la ruta.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
COMPUERTAS = json.loads((RAIZ / "docs/evidencia/compuertas.json").read_text(encoding="utf-8"))


def _evidencia(seccion: str) -> dict:
    ruta = COMPUERTAS[seccion]["evidencia"]
    assert ruta, f"{seccion}: sin evidencia comprometida"
    return json.loads((RAIZ / ruta).read_text(encoding="utf-8"))


def test_el_json_tiene_las_secciones_de_la_ruta():
    assert {
        "banco_institucional",
        "escala_139k",
        "bloqueo",
        "verdad_real",
        "colab_4M",
        "ingenieria",
    } <= set(COMPUERTAS)
    for seccion, cuerpo in COMPUERTAS.items():
        if isinstance(cuerpo, dict) and "objetivo" in cuerpo:
            assert cuerpo["objetivo"], f"{seccion}: objetivo vacío"


def test_banco_institucional_cumple_su_piso():
    calidad = _evidencia("banco_institucional")["calidad"]
    piso = COMPUERTAS["banco_institucional"]["piso"]
    assert calidad["macro_f1"] >= piso["macro_f1"]
    assert calidad["precision"] >= piso["precision"]
    assert calidad["recall_por_regimen"]["SIN_ID"] >= piso["recall_SIN_ID"]
    assert calidad["fp_que_tocan_negativo"] <= piso["fp_que_tocan_negativo_max"]
    assert (
        calidad["grupo_mayor_sin_identificador"]["filas"]
        <= piso["grupo_mayor_sin_identificador_filas_max"]
    )


def test_escala_139k_cumple_su_piso():
    corrida = _evidencia("escala_139k")["tamanos"]["139k"]
    piso = COMPUERTAS["escala_139k"]["piso"]
    assert corrida["calidad"]["f1"] >= piso["f1"]
    assert corrida["recursos"]["candidatos"] <= piso["candidatos_max"]
    assert corrida["recursos"]["rss_pico_mib"] <= piso["rss_pico_mib_max"]


def test_bloqueo_cumple_su_piso():
    actual = _evidencia("bloqueo")["bloqueo_actual_lsh"]
    piso = COMPUERTAS["bloqueo"]["piso"]
    assert actual["recall_pares_SIN_ID"] >= piso["recall_pares_SIN_ID"]
    assert actual["recall_pares_CON_ID"] >= piso["recall_pares_CON_ID"]


def test_la_verdad_real_sigue_sin_medirse_o_cumple_su_objetivo():
    seccion = COMPUERTAS["verdad_real"]
    if seccion["evidencia"] is None:
        pytest.skip("verdad real sin medir: ver docs/PROTOCOLO_GROUND_TRUTH.md")
    cifras = _evidencia("verdad_real")
    assert cifras["n_etiquetados"] >= seccion["objetivo"]["pares_etiquetados_min"]
    assert cifras["acuerdo"]["kappa"] >= seccion["objetivo"]["kappa_min"]
