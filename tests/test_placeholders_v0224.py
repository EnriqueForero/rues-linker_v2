"""Centinelas de nombre que la base real trajo y la librería no conocía (v0.22.4).

Medido sobre la vista ``snowflake_v2`` (355.681 filas): ``NO DISPONIBLE`` son 69
filas, una por país, con el **21,7 % del FOB**. Como no era placeholder, se
normalizaba a un nombre real y quedaba como "el importador más grande" de cada
país. ``A LA ORDEN`` / ``TO ORDER`` / ``TO THE ORDER`` son el consignatario
genérico del conocimiento de embarque, no una empresa.
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
from record_linkage.matching.normalizadores import PLACEHOLDERS, es_faltante

NUEVOS = ["NO DISPONIBLE", "A LA ORDEN", "TO ORDER", "TO THE ORDER", "CONFIDENCIAL", "RESERVADO"]


@pytest.mark.parametrize("valor", NUEVOS)
def test_es_placeholder_exacto_en_cualquier_caja(valor):
    assert valor in PLACEHOLDERS
    assert es_faltante(pd.Series([valor, valor.lower(), f"  {valor} "])).all()


def test_a_la_orden_de_alguien_no_es_placeholder():
    """Nombra a la parte consignataria: se conserva como nombre (pendiente decidir)."""
    assert not es_faltante(
        pd.Series(["TO ORDER OF ING BELGIUM", "A LA ORDEN DE BANCOLOMBIA"])
    ).any()


def test_no_disponible_no_se_convierte_en_el_importador_mas_grande():
    """El caso real: 'NO DISPONIBLE' con el mayor FOB del país."""
    filas = [
        ("NO DISPONIBLE", "ESTADOS UNIDOS", 38_361_069_449.74),
        ("NO DISPONIBLE", "PANAMA", 17_954_942_518.52),
        ("ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
        ("ACME TRADING L.L.C.", "Estados Unidos", 50.0),
        ("A LA ORDEN", "ESTADOS UNIDOS", 1_000.0),
        ("TO ORDER", "PANAMA", 10.0),
    ]
    df = pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])
    r = deduplicar_importadores(
        df,
        ConfigImportadores(
            col_razon_social="RAZON_SOCIAL",
            col_pais="PAIS",
            cols_metricas=("FOB",),
            col_peso_economico="FOB",
            verboso=False,
        ),
    )
    assert r.todo_ok, r.invariantes.to_string(index=False)
    c = r.correlativa
    centinelas = c[c.RAZON_SOCIAL.isin(["NO DISPONIBLE", "A LA ORDEN", "TO ORDER"])]
    # Cada uno en su propio grupo, marcado; nunca fusionados entre sí ni con ACME.
    assert centinelas.ID_IMPORTADOR.str.startswith("SINNOMBRE-").all()
    assert centinelas.ID_IMPORTADOR.nunique() == len(centinelas)
    assert c[c.RAZON_SOCIAL.str.startswith("ACME")].ID_IMPORTADOR.nunique() == 1
    # Y el FOB no desaparece: se conserva y queda visible en REVISION.
    assert abs(r.golden.FOB.sum() - df.FOB.sum()) < 1e-3
    rev = r.revision
    assert rev.MOTIVO_REVISION.str.contains("sin_nombre_utilizable").sum() == len(centinelas)
