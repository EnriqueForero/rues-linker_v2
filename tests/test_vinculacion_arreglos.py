"""Arreglos declarados del motor de vinculación (F3.1b): cada prueba fija el comportamiento NUEVO de un hallazgo.

El paquete nació con paridad exacta contra el motor original, defectos incluidos (``docs/vinculacion/``). Aquí cada
defecto se corrige con su prueba, y el cambio se declara (``tests/lineas_base_vinculacion_cambios.json`` cuando altera
las tablas de los escenarios inventados). El id de cada prueba es el del hallazgo en HALLAZGOS_MOTOR_ORIGINAL.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

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
