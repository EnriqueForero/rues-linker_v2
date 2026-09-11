"""Tests de regresión para deduplicación de fuentes SIN NIT.

Contexto: caso real de importaciones (Corea del Sur) donde la fuente solo
trae Razón Social + Ciudad, sin NIT. Antes de v2.11.0 esto rompía en
``consolidate_groups_by_nit_balanced`` con::

    AttributeError: Can only use .str accessor with string values, not integer

porque, al no haber NITs válidos, ``groups_by_nit`` quedaba como una Series
vacía de dtype int64 y ``.str.len()`` fallaba. El fix usa ``.map(len)`` y
corto-circuita cuando la Series está vacía.

Bug origen: golden/containment.py:240. Fix: v2.11.0.
"""

from __future__ import annotations

import contextlib
import io

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified
from record_linkage.golden.containment import consolidate_groups_by_nit_balanced


def _silenciar(func, *args, **kwargs):
    """Ejecuta func capturando stdout (los pipelines son muy verbosos)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return func(*args, **kwargs)


def test_consolidacion_todos_nit_vacios_no_rompe():
    """consolidate_groups_by_nit_balanced no debe romper si todos los NIT están vacíos.

    Reproduce la causa raíz exacta del bug: valid_nits vacío -> groups_by_nit
    es una Series vacía int64 -> .str.len() lanzaba AttributeError.
    """
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [1, 2, 3],
            "NIT_FINAL": ["", "", ""],
            "NIT": ["", "", ""],
            "RAZON_SOCIAL_FINAL": ["EMPRESA A", "EMPRESA B", "EMPRESA C"],
            "RAZON_SOCIAL": ["EMPRESA A", "EMPRESA B", "EMPRESA C"],
        }
    )
    correlative = golden.copy()

    g_out, _c_out = _silenciar(
        consolidate_groups_by_nit_balanced,
        golden,
        correlative,
        verbose=False,
    )

    # Sin NITs válidos no hay nada que consolidar: devuelve los grupos intactos.
    assert len(g_out) == 3
    assert g_out["ID_GRUPO"].nunique() == 3


def test_dedup_unificada_sin_nit_corre_end_to_end():
    """Pipeline completo de deduplicación sobre datos sin NIT (solo nombre).

    Patrón del caso Corea: razones sociales con variantes de escritura y sin
    NIT. Debe correr de punta a punta y agrupar las variantes evidentes.
    """
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": [
                "WORLD FLORA CO LTD",
                "WORLD FLORA CO LTD",
                "WORLD FLORA CO. LTD.",
                "DONG SUH FOODS CORPORATION",
                "DONG SUH FOODS",
                "EMPRESA TOTALMENTE DISTINTA SA",
            ],
            "NIT": ["", "", "", "", "", ""],
            "FUENTE": ["IMP"] * 6,
        }
    )

    corr, _gold = _silenciar(
        deduplicate_unified,
        df_input=df,
        col_nit="NIT",
        col_name="RAZON_SOCIAL",
        mode="BALANCEADO",
        profile="deduplication_standard",
        output_dir="/tmp/test_dedup_sin_nit_out",
    )

    # Las 3 variantes de WORLD FLORA deben quedar en el mismo grupo.
    g_wf = corr.loc[corr["RAZON_SOCIAL"].str.contains("WORLD FLORA"), "ID_GRUPO"]
    assert g_wf.nunique() == 1, "Las variantes de WORLD FLORA deben unirse"

    # La empresa distinta no debe quedar con WORLD FLORA.
    g_distinta = corr.loc[
        corr["RAZON_SOCIAL"] == "EMPRESA TOTALMENTE DISTINTA SA", "ID_GRUPO"
    ].iloc[0]
    assert g_distinta not in set(g_wf), "Empresa distinta no debe unirse a WORLD FLORA"


def test_dedup_sin_nit_con_ciudad_extra_feature():
    """La ciudad como extra_feature debe ser aceptada en régimen sin NIT."""
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": [
                "ALPHA TRADING CO",
                "ALPHA TRADING CO",
                "BETA IMPORTS LTD",
            ],
            "CIUDAD": ["SEOUL", "SEOUL", "BUSAN"],
            "NIT": ["", "", ""],
            "FUENTE": ["IMP"] * 3,
        }
    )

    corr, _gold = _silenciar(
        deduplicate_unified,
        df_input=df,
        col_nit="NIT",
        col_name="RAZON_SOCIAL",
        mode="BALANCEADO",
        profile="deduplication_standard",
        output_dir="/tmp/test_dedup_sin_nit_ciudad_out",
        extra_features=[{"column": "CIUDAD", "weight": 0.20, "type": "token_set_ratio_signed"}],
    )

    # Las dos ALPHA (mismo nombre, misma ciudad) deben unirse.
    g_alpha = corr.loc[corr["RAZON_SOCIAL"] == "ALPHA TRADING CO", "ID_GRUPO"]
    assert g_alpha.nunique() == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
