"""La normalización no puede depender de la semilla de hash del proceso (v0.22.3).

Medido: `AVIATECA SOCIEDAD ANONIMA SUCURSAL COLOMBIA` normalizaba a "AVIATECA"
en un proceso y a "AVIATECA ANONIMA" en otro, según PYTHONHASHSEED. La misma
base de 211.949 filas daba 99.897 o 99.898 importadores según la sesión, y
ninguna prueba lo veía porque `test_es_determinista` corre en UN proceso.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pandas as pd
import pytest

from record_linkage.flujo import ConfigImportadores, registrar_locale
from record_linkage.matching.normalizadores import normalizar_nombre

APILADOS = [
    "AVIATECA SOCIEDAD ANONIMA SUCURSAL COLOMBIA",
    "AVIATECA S.A.",
    "AVIATECA SOCIEDAD ANONIMA",
    "ACME TRADING LLC SUCURSAL COLOMBIA",
]


def _locale() -> str:
    return registrar_locale(ConfigImportadores(col_razon_social="X", col_pais="Y", verboso=False))


def test_los_sufijos_apilados_se_reducen_hasta_el_punto_fijo():
    """Quitar el último sufijo destapa el anterior: hay que seguir hasta que no quede."""
    out = normalizar_nombre(pd.Series(APILADOS), locale=_locale(), quitar_genericos=False)
    assert out.tolist() == ["AVIATECA", "AVIATECA", "AVIATECA", "ACME TRADING"], out.tolist()


def _en_subproceso(codigo: str, semilla: int) -> list:
    env = {**os.environ, "PYTHONHASHSEED": str(semilla)}
    r = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(codigo)], env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


CODIGO_NORMALIZAR = f"""
import json, pandas as pd
from record_linkage.flujo import ConfigImportadores, registrar_locale
from record_linkage.matching.normalizadores import normalizar_nombre
loc = registrar_locale(ConfigImportadores(col_razon_social="X", col_pais="Y", verboso=False))
print(json.dumps(normalizar_nombre(pd.Series({APILADOS!r}), locale=loc, quitar_genericos=False).tolist()))
"""

CODIGO_DEDUPLICAR = """
import json, pandas as pd
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
filas = [
    ("AVIATECA SOCIEDAD ANONIMA SUCURSAL COLOMBIA", "GUATEMALA", 1.0),
    ("AVIATECA S.A.", "GUATEMALA", 5.0),
    ("AVIATECA SOCIEDAD ANONIMA", "GUATEMALA", 2.0),
    ("AVIATECA", "GUATEMALA", 3.0),
    ("ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
    ("ACME TRADING L.L.C.", "Estados Unidos", 50.0),
    ("BETA LOGISTICS INC", "ALEMANIA", 70.0),
]
df = pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])
r = deduplicar_importadores(df, ConfigImportadores(col_razon_social="RAZON_SOCIAL", col_pais="PAIS",
    cols_metricas=("FOB",), col_peso_economico="FOB", verboso=False))
c = r.correlativa.sort_values(["RAZON_SOCIAL", "PAIS"])
av = c[c.RAZON_SOCIAL.str.startswith("AVIATECA")]
print(json.dumps([c.ID_IMPORTADOR.tolist(), c.RAZON_SOCIAL_FINAL.tolist(), av.NOMBRE_NORM.tolist(), av.ID_IMPORTADOR.tolist()]))
"""


@pytest.mark.parametrize("semillas", [(0, 1), (2, 3)])
def test_normalizar_es_identico_entre_procesos_con_distinta_semilla(semillas):
    a, b = (_en_subproceso(CODIGO_NORMALIZAR, s) for s in semillas)
    assert a == b, f"PYTHONHASHSEED={semillas[0]} → {a}\\nPYTHONHASHSEED={semillas[1]} → {b}"
    assert a == ["AVIATECA", "AVIATECA", "AVIATECA", "ACME TRADING"]


def test_deduplicar_es_identico_entre_procesos_con_distinta_semilla():
    """La prueba que faltaba: `test_es_determinista` corre en un solo proceso."""
    a, b, c = (_en_subproceso(CODIGO_DEDUPLICAR, s) for s in (0, 1, 7))
    assert a == b == c, f"seed0={a}\\nseed1={b}\\nseed7={c}"
    _ids, _finales, normas_av, ids_av = a
    assert len(ids_av) == 4 and len(set(ids_av)) == 1, (
        f"las cuatro grafías de AVIATECA son un solo importador: {ids_av}"
    )
    assert set(normas_av) == {"AVIATECA"}, normas_av
