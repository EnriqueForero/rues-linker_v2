"""Línea base del banco congelada en una prueba (F0.2).

El banco (`record_linkage.evaluation.banco`) es el instrumento con el que se
acepta o se rechaza todo cambio del motor. Hasta ahora su línea base vivía en
un JSON de evidencia y en la memoria de quien lo corrió; nada fallaba si el
motor empezaba a producir otra partición. Estas pruebas la congelan:

* la **lenta** (`slow`) corre el banco completo sobre el conjunto
  institucional y exige la huella, el F1, el macro-F1 y los falsos positivos
  sobre negativos que se midieron en el commit base;
* la **rápida** lee `docs/evidencia/corrida_base_f0.json` y comprueba que lo
  que el JSON registra es lo mismo que la constante exige, para que la
  evidencia y la prueba no deriven cada una por su lado.

Las constantes viven en UN solo sitio (`tests/lineas_base.py`, `BANCO_F0`) y
la comparación también (`discrepancias_con_linea_base`): si una prueba y la
otra discreparan, sería porque miden cosas distintas, no porque una copia se
quedó vieja.

Si la huella cambia, la prueba falla y dice qué hacer: un cambio de
comportamiento se declara (CLAUDE.md §2, regla 4; plan F0, regla 3) con un ADR
y una entrada en CHANGELOG, y solo entonces se mueve la línea base.

Rutas: la prueba lee `docs/evidencia/corrida_base_f0.json` y
`data/benchmark/benchmark_institucional.csv.gz`; no se incluye a sí misma ni
escribe en `docs/evidencia` (la corrida lenta deposita su evidencia en un
directorio temporal).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lineas_base import BANCO_F0, LineaBaseBanco, discrepancias_con_linea_base

from record_linkage.evaluation.banco import EspecificacionBanco, correr_banco

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / BANCO_F0.datos
EVIDENCIA = RAIZ / BANCO_F0.evidencia


def _leer_evidencia() -> dict:
    assert EVIDENCIA.is_file(), (
        f"no existe {EVIDENCIA.relative_to(RAIZ)}: es la evidencia de la línea base "
        f"F0 y se versiona a propósito (docs/evidencia/README.md)."
    )
    return json.loads(EVIDENCIA.read_text(encoding="utf-8"))


# ── La constante y el JSON de evidencia dicen lo mismo ───────────────────


def test_la_constante_esta_documentada() -> None:
    """Una línea base sin fecha ni commit no se puede auditar."""
    assert isinstance(BANCO_F0, LineaBaseBanco)
    assert len(BANCO_F0.huella) == 64 and int(BANCO_F0.huella, 16) >= 0
    assert BANCO_F0.commit and BANCO_F0.fecha
    assert BANCO_F0.tolerancia_metricas > 0


def test_el_json_de_evidencia_coincide_con_la_constante() -> None:
    """`docs/evidencia/corrida_base_f0.json` registra exactamente `BANCO_F0`."""
    corrida = _leer_evidencia()
    calidad = corrida["calidad"]
    discrepancias = discrepancias_con_linea_base(
        BANCO_F0,
        huella=corrida["huella"],
        f1=calidad["f1"],
        macro_f1=calidad["macro_f1"],
        b3_f1=calidad["b3_f1"],
        fp_que_tocan_negativo=calidad["fp_que_tocan_negativo"],
        origen=str(BANCO_F0.evidencia),
    )
    assert not discrepancias, "\n".join(discrepancias)


def test_el_json_de_evidencia_se_midio_con_la_misma_especificacion() -> None:
    """Si el JSON se corrió con otro perfil o con ajustes, no es esta línea base."""
    espec = _leer_evidencia()["especificacion"]
    assert Path(espec["datos"]).parts[-3:] == BANCO_F0.datos.parts, espec["datos"]
    assert espec["perfil"] == BANCO_F0.perfil
    assert espec["confiables"] == []
    assert espec["variables_extra"] is None
    assert espec["ajustes_perfil"] is None
    assert espec["perfil_multicampo"] is None


# ── El comparador detecta la deriva (prueba de la prueba) ────────────────


def test_la_comparacion_detecta_un_cambio_de_huella() -> None:
    """Una compuerta que no puede fallar no es una compuerta."""
    otra_huella = "0" * 64
    discrepancias = discrepancias_con_linea_base(
        BANCO_F0,
        huella=otra_huella,
        f1=BANCO_F0.f1,
        macro_f1=BANCO_F0.macro_f1,
        b3_f1=BANCO_F0.b3_f1,
        fp_que_tocan_negativo=BANCO_F0.fp_que_tocan_negativo,
        origen="prueba",
    )
    assert len(discrepancias) == 1
    texto = discrepancias[0]
    assert "la huella del banco cambió" in texto
    assert "ADR" in texto and "CHANGELOG" in texto and "regla 3" in texto


def test_la_comparacion_detecta_metricas_fuera_de_tolerancia() -> None:
    discrepancias = discrepancias_con_linea_base(
        BANCO_F0,
        huella=BANCO_F0.huella,
        f1=BANCO_F0.f1 + 2 * BANCO_F0.tolerancia_metricas,
        macro_f1=BANCO_F0.macro_f1,
        b3_f1=BANCO_F0.b3_f1,
        fp_que_tocan_negativo=BANCO_F0.fp_que_tocan_negativo + 1,
        origen="prueba",
    )
    assert len(discrepancias) == 2
    assert any("F1" in d and "fuera de tolerancia" in d for d in discrepancias)
    assert any("fp_que_tocan_negativo" in d for d in discrepancias)


def test_la_comparacion_tolera_el_ruido_de_redondeo() -> None:
    """± 1e-4 es ruido de redondeo a cuatro decimales, no un cambio de motor."""
    discrepancias = discrepancias_con_linea_base(
        BANCO_F0,
        huella=BANCO_F0.huella,
        f1=BANCO_F0.f1 + BANCO_F0.tolerancia_metricas / 2,
        macro_f1=BANCO_F0.macro_f1 - BANCO_F0.tolerancia_metricas / 2,
        b3_f1=BANCO_F0.b3_f1,
        fp_que_tocan_negativo=BANCO_F0.fp_que_tocan_negativo,
        origen="prueba",
    )
    assert discrepancias == []


# ── El banco real reproduce la línea base ────────────────────────────────


@pytest.mark.slow
def test_el_banco_reproduce_la_linea_base_f0(tmp_path: Path) -> None:
    """Corre el banco completo (≈ 55–60 s) y exige la línea base de `BANCO_F0`.

    Las carpetas de trabajo y de evidencia son temporales: esta prueba mide,
    no publica. La evidencia oficial sigue siendo
    `docs/evidencia/corrida_base_f0.json`, que se regenera con
    `scripts/banco.py` cuando un ADR mueve la línea base.
    """
    assert DATOS.is_file(), f"no existe el conjunto de referencia {DATOS.relative_to(RAIZ)}"
    corrida = correr_banco(
        EspecificacionBanco(
            etiqueta="linea_base_f0",
            datos=DATOS,
            perfil=BANCO_F0.perfil,
            dir_trabajo=tmp_path / "trabajo",
            dir_evidencia=tmp_path / "evidencia",
        )
    )
    calidad = corrida.calidad
    discrepancias = discrepancias_con_linea_base(
        BANCO_F0,
        huella=corrida.huella,
        f1=calidad.f1,
        macro_f1=calidad.macro_f1,
        b3_f1=calidad.b3_f1,
        fp_que_tocan_negativo=calidad.fp_que_tocan_negativo,
        origen=f"corrida del banco sobre {BANCO_F0.datos}",
    )
    assert not discrepancias, "\n".join(discrepancias)
    assert calidad.registros == 30486, "el conjunto de referencia cambió de tamaño"
    assert not (RAIZ / "docs" / "evidencia" / "corrida_linea_base_f0.json").exists(), (
        "la prueba escribió en docs/evidencia: debe usar solo directorios temporales"
    )
