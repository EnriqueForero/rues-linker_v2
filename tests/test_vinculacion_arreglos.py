"""Arreglos declarados del motor de vinculación (F3.1b): cada prueba fija el comportamiento NUEVO de un hallazgo.

El paquete nació con paridad exacta contra el motor original, defectos incluidos (``docs/vinculacion/``). Aquí cada
defecto se corrige con su prueba, y el cambio se declara (``tests/lineas_base_vinculacion_cambios.json`` cuando altera
las tablas de los escenarios inventados). El id de cada prueba es el del hallazgo en HALLAZGOS_MOTOR_ORIGINAL.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo.vinculacion import controles
from record_linkage.flujo.vinculacion.normalizar import (
    digitos_telefono,
    dominio_email,
    dominio_web,
    id_comparable,
)

AUSENTES = [None, np.nan, np.float32("nan"), pd.NA, pd.NaT]


# ── g1-O1 · un ausente de pandas nunca es el texto «NA» ─────────────────────────────────────────────
@pytest.mark.parametrize("ausente", AUSENTES, ids=repr)
@pytest.mark.parametrize("extractor", [id_comparable, dominio_web, dominio_email, digitos_telefono])
def test_g1_o1_los_extractores_tratan_cualquier_ausente_de_pandas_como_vacio(extractor, ausente):
    # Antes: id_comparable(pd.NA) daba 'NA' y dos empresas sin NIF «compartían identificador» (EMPRESA/A).
    assert extractor(ausente) == ""


# ── g1-O2 · un número entero guardado como float conserva sus dígitos ───────────────────────────────
@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (900123456.0, "900123456"),
        (np.float64(1234567890.0), "1234567890"),
        (900123456, "900123456"),
    ],
)
def test_g1_o2_id_comparable_no_agrega_un_cero_a_un_entero_en_float(valor, esperado):
    # Antes: 900123456.0 → '9001234560' y no coincidía con el mismo NIT escrito como texto.
    assert id_comparable(valor) == esperado == id_comparable(str(int(valor)))


@pytest.mark.parametrize(
    "valor", [3001234567.0, np.float64(3001234567.0), 3001234567, "300 123 4567"]
)
def test_g1_o2_el_telefono_en_float_da_los_mismos_ocho_digitos_que_en_texto(valor):
    # Antes: 3001234567.0 → '12345670' (el '.0' aportaba un dígito) frente a '01234567' del texto.
    assert digitos_telefono(valor) == "01234567"


def test_g1_o2_un_float_con_decimales_se_conserva_como_viene():
    assert id_comparable(12.5) == "125"
    assert digitos_telefono(12.5) == ""


# ── C1 + M-a · el identificador igual no cruza países y justifica el A en la invariante ─────────────
def _pares_a(evidencia: str, sobra: str = "ALIMENTOS") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "NIVEL_AUTO": ["EMPRESA"], "BANDA_AUTO": ["A"], "DECISION": [""], "MISMO_PAIS": ["SÍ"],
            "TIPO_SUP_B": ["NOMBRE"], "SOBRA_EXPORTADOR": [sobra], "SOBRA_DESTINO": [""],
            "NIVEL_FINAL": ["EMPRESA"], "EVIDENCIA_EXTRA": [evidencia],
        }
    )  # fmt: skip


def _estados(pares: pd.DataFrame) -> dict[str, str]:
    c = controles._Controles()
    controles._controles_de_pares(c, pares)
    return {f["CONTROL"]: f["ESTADO"] for f in c.filas}


def test_m_a_un_a_promovido_por_identificador_no_incumple_la_invariante_de_palabras_alineadas():
    # Antes: toda subida por identificador con palabras que sobran dejaba esta invariante en FALLA.
    estados = _estados(_pares_a("mismo identificador"))
    assert estados["Todo vínculo automático A tiene todas las palabras alineadas"] == "OK"


def test_m_a_un_a_con_palabras_sin_alinear_y_sin_identificador_sigue_incumpliendo():
    estados = _estados(_pares_a(""))
    assert estados["Todo vínculo automático A tiene todas las palabras alineadas"] == "FALLA"
