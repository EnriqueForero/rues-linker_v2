"""Contrato de calidad del perfil ``fuentes_mixtas`` (v0.18.0).

Estas pruebas fijan en código la mejora que se midió, para que una regresión
futura falle aquí y no en producción. Los umbrales están por debajo de lo
medido —no clavados al cuarto decimal— para tolerar el ruido de una máquina
distinta sin dejar pasar una pérdida real.

Medido el 2026-08-29 sobre ``data/ground_truth/ground_truth_grande.csv``:

    perfil                 precision   recall      F1      recall SIN_NIT
    produccion_estandar      0,9760    0,9560    0,9659       0,8344
    fuentes_mixtas           0,9866    0,9733    0,9799       0,9165

Validación fuera de muestra (3 pliegues por grupo): ΔF1 medio +0,0105.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from record_linkage.config.profiles import PERFILES_BASE, crear_config_orchestrator
from record_linkage.evaluation.banco import EspecificacionBanco, correr_banco

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data" / "ground_truth" / "ground_truth_grande.csv"

#: Pisos de aceptación. Por debajo de esto, la mejora dejó de existir.
PISO_F1 = 0.975
PISO_PRECISION = 0.980
PISO_RECALL = 0.965
PISO_RECALL_SIN_NIT = 0.900
#: F1 de produccion_estandar sobre el mismo conjunto, medido.
F1_BASE = 0.9659


def test_el_perfil_existe_y_se_puede_construir() -> None:
    assert "fuentes_mixtas" in PERFILES_BASE
    config = crear_config_orchestrator("fuentes_mixtas")
    perfil = config["profiles"]["fuentes_mixtas"]
    assert perfil["lsh_threshold"] == 0.45
    assert perfil["similitud_compacta_min"] == 0.96
    assert perfil["idf_weight_blend_sin_identificador"] == 0.05


def test_las_perillas_nuevas_estan_en_todos_los_perfiles_del_motor() -> None:
    """Una perilla que existe en un perfil y no en otro es una trampa: el
    usuario la ajusta, la configuración la rechaza, y no sabe por qué."""
    perfiles_motor = [
        nombre
        for nombre, perfil in PERFILES_BASE.items()
        if "tolerancia_digitacion_identificador" in perfil
    ]
    assert perfiles_motor, "no se encontró ningún perfil del motor"
    for nombre in perfiles_motor:
        perfil = PERFILES_BASE[nombre]
        for perilla in (
            "similitud_compacta_min",
            "idf_weight_blend",
            "idf_weight_blend_sin_identificador",
            "idf_veto_min_sin_identificador",
        ):
            assert perilla in perfil, f"a {nombre} le falta {perilla}"


@pytest.mark.slow
def test_el_perfil_alcanza_la_calidad_medida(tmp_path: Path) -> None:
    corrida = correr_banco(
        EspecificacionBanco(
            etiqueta="prueba_mixtas",
            datos=DATOS,
            perfil="fuentes_mixtas",
            dir_trabajo=tmp_path / "trabajo",
            dir_evidencia=tmp_path / "evidencia",
        )
    )
    calidad = corrida.calidad
    assert calidad.f1 >= PISO_F1, f"F1 {calidad.f1} por debajo del piso {PISO_F1}"
    assert calidad.precision >= PISO_PRECISION
    assert calidad.recall >= PISO_RECALL
    assert calidad.recall_por_regimen["SIN_NIT"] >= PISO_RECALL_SIN_NIT
    assert calidad.f1 > F1_BASE, "el perfil dejó de superar a produccion_estandar"


@pytest.mark.slow
def test_el_perfil_estandar_conserva_su_linea_base(tmp_path: Path) -> None:
    """El perfil por defecto no cambió: las perillas nuevas están en 0."""
    corrida = correr_banco(
        EspecificacionBanco(
            etiqueta="prueba_estandar",
            datos=DATOS,
            perfil="produccion_estandar",
            dir_trabajo=tmp_path / "trabajo",
            dir_evidencia=tmp_path / "evidencia",
        )
    )
    assert corrida.calidad.f1 == pytest.approx(F1_BASE, abs=0.002)
