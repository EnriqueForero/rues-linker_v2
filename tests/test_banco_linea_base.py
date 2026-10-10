"""Línea base del banco congelada en una prueba (F0.2).

El banco (`record_linkage.evaluation.banco`) es el instrumento con el que se
acepta o se rechaza todo cambio del motor. Hasta ahora su línea base vivía en
un JSON de evidencia y en la memoria de quien lo corrió; nada fallaba si el
motor empezaba a producir otra partición. Estas pruebas la congelan:

* la **lenta** (`slow`) corre el banco completo sobre el conjunto
  institucional y exige la huella, el F1, el macro-F1 y los falsos positivos
  sobre negativos que se midieron en el commit base;
* la **rápida** lee el JSON de evidencia de la línea base vigente y comprueba
  que lo que el JSON registra es lo mismo que la constante exige, para que la
  evidencia y la prueba no deriven cada una por su lado;
* la **de historia** recorre TODAS las líneas base conocidas (`BANCO_F0`,
  `BANCO_F2`) y exige que cada JSON siga coincidiendo con su constante: mover
  la línea base añade una, no reescribe las anteriores.

Las constantes viven en UN solo sitio (`tests/lineas_base.py`) y la
comparación también (`discrepancias_con_linea_base`): si una prueba y la otra
discreparan, sería porque miden cosas distintas, no porque una copia se quedó
vieja. Las pruebas del tronco leen `BANCO_VIGENTE` (hoy `BANCO_F2`, tras la
cobertura por estrellas de F2.1, ADR-0011); `BANCO_F0` queda como historia y
la usa la prueba de paridad con la perilla apagada
(`tests/test_cobertura_sin_identificador_f21.py`).

Si la huella cambia, la prueba falla y dice qué hacer: un cambio de
comportamiento se declara (CLAUDE.md §2, regla 4; plan F0, regla 3) con un ADR
y una entrada en CHANGELOG, y solo entonces se mueve la línea base (JSON nuevo
+ constante nueva + `BANCO_VIGENTE` apuntando a ella).

Rutas: la prueba lee `docs/evidencia/corrida_base_f0.json`,
`docs/evidencia/corrida_base_f2.json` y
`data/benchmark/benchmark_institucional.csv.gz`; no se incluye a sí misma ni
escribe en `docs/evidencia` (la corrida lenta deposita su evidencia en un
directorio temporal).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from lineas_base import (
    BANCO_F0,
    BANCO_F2,
    BANCO_VIGENTE,
    LineaBaseBanco,
    discrepancias_con_linea_base,
)

from record_linkage.evaluation.banco import SEMILLA, EspecificacionBanco, correr_banco

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / BANCO_VIGENTE.datos


def _leer_evidencia(linea: LineaBaseBanco = BANCO_VIGENTE) -> dict:
    evidencia = RAIZ / linea.evidencia
    assert evidencia.is_file(), (
        f"no existe {linea.evidencia}: es la evidencia de la línea base medida en "
        f"{linea.commit} ({linea.fecha}) y se versiona a propósito (docs/evidencia/README.md)."
    )
    return json.loads(evidencia.read_text(encoding="utf-8"))


# ── La constante y el JSON de evidencia dicen lo mismo ───────────────────


def test_la_constante_esta_documentada() -> None:
    """Una línea base sin fecha ni commit no se puede auditar."""
    assert isinstance(BANCO_VIGENTE, LineaBaseBanco)
    assert len(BANCO_VIGENTE.huella) == 64 and int(BANCO_VIGENTE.huella, 16) >= 0
    assert BANCO_VIGENTE.commit and BANCO_VIGENTE.fecha
    assert BANCO_VIGENTE.tolerancia_metricas > 0


def test_el_json_de_evidencia_coincide_con_la_constante() -> None:
    """El JSON de evidencia vigente (`BANCO_VIGENTE.evidencia`) registra exactamente la constante."""
    corrida = _leer_evidencia()
    calidad = corrida["calidad"]
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=corrida["huella"],
        f1=calidad["f1"],
        macro_f1=calidad["macro_f1"],
        b3_f1=calidad["b3_f1"],
        fp_que_tocan_negativo=calidad["fp_que_tocan_negativo"],
        origen=str(BANCO_VIGENTE.evidencia),
    )
    assert not discrepancias, "\n".join(discrepancias)


def test_el_json_de_evidencia_se_midio_con_la_misma_especificacion() -> None:
    """Si el JSON se corrió con otro perfil o con ajustes, no es esta línea base."""
    espec = _leer_evidencia()["especificacion"]
    assert Path(espec["datos"]).parts[-3:] == BANCO_VIGENTE.datos.parts, espec["datos"]
    assert espec["perfil"] == BANCO_VIGENTE.perfil
    assert espec["confiables"] == []
    assert espec["variables_extra"] is None
    assert espec["ajustes_perfil"] is None
    assert espec["perfil_multicampo"] is None
    assert espec["semilla"] == SEMILLA, (
        f"el JSON se midió con la semilla {espec['semilla']} y el banco usa {SEMILLA}: "
        "otra semilla es otra partición y no es esta línea base"
    )


# ── El comparador detecta la deriva (prueba de la prueba) ────────────────


def test_la_comparacion_detecta_un_cambio_de_huella() -> None:
    """Una compuerta que no puede fallar no es una compuerta."""
    otra_huella = "0" * 64
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=otra_huella,
        f1=BANCO_VIGENTE.f1,
        macro_f1=BANCO_VIGENTE.macro_f1,
        b3_f1=BANCO_VIGENTE.b3_f1,
        fp_que_tocan_negativo=BANCO_VIGENTE.fp_que_tocan_negativo,
        origen="prueba",
    )
    assert len(discrepancias) == 1
    texto = discrepancias[0]
    assert "la huella del banco cambió" in texto
    assert "ADR" in texto and "CHANGELOG" in texto and "regla 3" in texto


def test_la_comparacion_detecta_metricas_fuera_de_tolerancia() -> None:
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=BANCO_VIGENTE.huella,
        f1=BANCO_VIGENTE.f1 + 2 * BANCO_VIGENTE.tolerancia_metricas,
        macro_f1=BANCO_VIGENTE.macro_f1,
        b3_f1=BANCO_VIGENTE.b3_f1,
        fp_que_tocan_negativo=BANCO_VIGENTE.fp_que_tocan_negativo + 1,
        origen="prueba",
    )
    assert len(discrepancias) == 2
    assert any("F1" in d and "fuera de tolerancia" in d for d in discrepancias)
    assert any("fp_que_tocan_negativo" in d for d in discrepancias)


def test_la_comparacion_tolera_el_ruido_de_redondeo() -> None:
    """± 1e-4 es ruido de redondeo a cuatro decimales, no un cambio de motor."""
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=BANCO_VIGENTE.huella,
        f1=BANCO_VIGENTE.f1 + BANCO_VIGENTE.tolerancia_metricas / 2,
        macro_f1=BANCO_VIGENTE.macro_f1 - BANCO_VIGENTE.tolerancia_metricas / 2,
        b3_f1=BANCO_VIGENTE.b3_f1,
        fp_que_tocan_negativo=BANCO_VIGENTE.fp_que_tocan_negativo,
        origen="prueba",
    )
    assert discrepancias == []


# ── El banco real reproduce la línea base ────────────────────────────────


@pytest.mark.slow
def test_el_banco_reproduce_la_linea_base_vigente(tmp_path: Path) -> None:
    """Corre el banco completo (≈ 60 s) y exige la línea base de `BANCO_VIGENTE`.

    Las carpetas de trabajo y de evidencia son temporales: esta prueba mide,
    no publica. La evidencia oficial sigue siendo el JSON de
    `BANCO_VIGENTE.evidencia`, que el coordinador genera con
    `scripts/banco.py` cuando un ADR mueve la línea base.
    """
    assert DATOS.is_file(), f"no existe el conjunto de referencia {DATOS.relative_to(RAIZ)}"
    corrida = correr_banco(
        EspecificacionBanco(
            etiqueta="linea_base_vigente",
            datos=DATOS,
            perfil=BANCO_VIGENTE.perfil,
            dir_trabajo=tmp_path / "trabajo",
            dir_evidencia=tmp_path / "evidencia",
        )
    )
    calidad = corrida.calidad
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=corrida.huella,
        f1=calidad.f1,
        macro_f1=calidad.macro_f1,
        b3_f1=calidad.b3_f1,
        fp_que_tocan_negativo=calidad.fp_que_tocan_negativo,
        origen=f"corrida del banco sobre {BANCO_VIGENTE.datos}",
    )
    assert not discrepancias, "\n".join(discrepancias)
    assert calidad.registros == 30486, "el conjunto de referencia cambió de tamaño"
    # `correr_banco` no escribe el JSON de la corrida (eso lo hace `Corrida.guardar`,
    # desde scripts/banco.py); lo que sí deposita en `dir_evidencia` es la predicción
    # en parquet. Se vigila ese artefacto real: que esté en el temporal y no en el repo.
    prediccion = "prediccion_linea_base_vigente.parquet"
    assert (tmp_path / "evidencia" / prediccion).is_file(), (
        "correr_banco debía dejar la predicción en el directorio temporal"
    )
    assert not (RAIZ / "docs" / "evidencia" / prediccion).exists(), (
        "la prueba escribió en docs/evidencia: debe usar solo directorios temporales"
    )


def test_la_comparacion_no_deja_pasar_un_nan() -> None:
    """Una métrica que dejó de calcularse (NaN) es una discrepancia, no un pase silencioso."""
    discrepancias = discrepancias_con_linea_base(
        BANCO_VIGENTE,
        huella=BANCO_VIGENTE.huella,
        f1=float("nan"),
        macro_f1=BANCO_VIGENTE.macro_f1,
        b3_f1=BANCO_VIGENTE.b3_f1,
        fp_que_tocan_negativo=BANCO_VIGENTE.fp_que_tocan_negativo,
        origen="prueba",
    )
    assert len(discrepancias) == 1 and "no se calculó" in discrepancias[0]


# ── La historia tampoco deriva: cada JSON coincide con su constante ──────

LINEAS_BASE_CONOCIDAS = (BANCO_F0, BANCO_F2)


@pytest.mark.parametrize("linea", LINEAS_BASE_CONOCIDAS, ids=lambda lb: lb.evidencia.stem)
def test_cada_linea_base_conocida_coincide_con_su_json(linea: LineaBaseBanco) -> None:
    """Una línea base nueva no reescribe las anteriores: cada JSON sigue a su constante."""
    assert isinstance(linea, LineaBaseBanco)
    corrida = _leer_evidencia(linea)
    calidad = corrida["calidad"]
    discrepancias = discrepancias_con_linea_base(
        linea,
        huella=corrida["huella"],
        f1=calidad["f1"],
        macro_f1=calidad["macro_f1"],
        b3_f1=calidad["b3_f1"],
        fp_que_tocan_negativo=calidad["fp_que_tocan_negativo"],
        origen=str(linea.evidencia),
    )
    assert not discrepancias, "\n".join(discrepancias)
