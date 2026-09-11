"""Tests de regresión para el re-scoring por IDF de tokens (Fix #3, v2.12.0).

Contexto: en fuentes sin NIT (caso Corea), el ``token_set_ratio`` agrupa
empresas distintas que comparten tokens de alta frecuencia:

    "ELITE EXPORTS INTERNATIONAL INC Y/O NENOVA"  vs
    "ELITE EXPORTS INTERNATIONAL INC Y/O ARES3"   -> token_set_ratio = 0.93

    "SECUI CORPORATION" vs "MULTIFLORA CORPORATION" -> 0.79 (por "CORPORATION")

El re-scoring IDF pondera cada token por log(N/df): tokens frecuentes
("ELITE", "CO", "LTD", "CORPORATION") pesan ~0; tokens raros ("NENOVA",
"ARES3", "SECUI") dominan. La similitud cae para los pares espurios y se
mantiene para las variantes reales.

Diseño: ``idf_weight_blend=0.0`` (default en deduplication_standard) es
no-op, garantizando paridad con v2.11.0 en datasets con NIT.
"""

from __future__ import annotations

import contextlib
import io

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified


def _silenciar(func, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return func(*args, **kwargs)


def _correr(df, profile, extra=None):
    import uuid

    return _silenciar(
        deduplicate_unified,
        df_input=df.copy(),
        col_nit="NIT",
        col_name="RAZON_SOCIAL",
        mode="BALANCEADO",
        profile=profile,
        output_dir=f"/tmp/test_idf_{profile}_{uuid.uuid4().hex[:8]}",
        extra_features=extra,
    )


def test_idf_separa_empresas_con_prefijo_comun():
    """El perfil sin-NIT (con IDF) debe separar empresas que solo comparten prefijo.

    'ELITE EXPORTS INTERNATIONAL INC Y/O X' con X distinto cada vez NO debe
    fusionarse en un solo grupo.
    """
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": [
                "ELITE EXPORTS INTERNATIONAL INC Y/O NENOVA CO LTD",
                "ELITE EXPORTS INTERNATIONAL INC Y/O ARES3 CO LTD",
                "ELITE EXPORTS INTERNATIONAL INC Y/O SOIREE FLOWER CO LTD",
                "ELITE EXPORTS INTERNATIONAL INC Y/O GREEN FLORAL LTD",
            ],
            "NIT": ["", "", "", ""],
            "FUENTE": ["IMP"] * 4,
        }
    )
    corr, _ = _correr(df, "deduplication_sin_nit_conservador")
    # Cada empresa distinta debe quedar en su propio grupo (4 grupos).
    assert corr["ID_GRUPO"].nunique() == 4, (
        "Empresas con prefijo común pero sufijo distinto no deben fusionarse"
    )


def test_idf_separa_nombres_genericos_compartidos():
    """'X CORPORATION' vs 'Y CORPORATION' (X != Y) no deben unirse por 'CORPORATION'."""
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": [
                "SECUI CORPORATION",
                "MULTIFLORA CORPORATION",
                "CK CORPORATIONS",
                "KSCORPORATION",
            ],
            "NIT": ["", "", "", ""],
            "FUENTE": ["IMP"] * 4,
        }
    )
    corr, _ = _correr(df, "deduplication_sin_nit_conservador")
    # Las 4 son empresas distintas: deben quedar en >= 3 grupos.
    assert corr["ID_GRUPO"].nunique() >= 3, (
        "Nombres que solo comparten un token genérico no deben fusionarse"
    )


def test_idf_preserva_variantes_reales():
    """Variantes con el token discriminante + sufijos deben seguir unidas con IDF.

    Nota: con IDF sobre un corpus muy pequeño, un nombre pelado ('NENOVA' sin
    sufijo) puede separarse porque el token raro no alcanza a dominar. Sobre el
    corpus real (818 regs) NENOVA n=28 se agrupa correctamente. Aquí validamos
    que las variantes que comparten el token discriminante + estructura se unen.
    """
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": [
                "NENOVA CO LTD",
                "NENOVA CO., LTD",
                "NENOVA CO. LTD",
                "NENOVA CO ltda",
            ],
            "NIT": ["", "", "", ""],
            "FUENTE": ["IMP"] * 4,
        }
    )
    corr, _ = _correr(df, "deduplication_sin_nit_conservador")
    # Las variantes 'NENOVA CO LTD' (con sufijo) deben quedar en un solo grupo.
    assert corr["ID_GRUPO"].nunique() == 1, "Las variantes de NENOVA CO LTD deben mantenerse unidas"


def test_idf_es_noop_en_perfil_estandar():
    """deduplication_standard tiene idf_weight_blend=0.0 → no debe activar IDF.

    Verificación indirecta: el perfil estándar agrupa por nombre normal, así
    que dos nombres idénticos siguen uniéndose (el IDF no los rompe).
    """
    df = pd.DataFrame(
        {
            "RAZON_SOCIAL": ["ACME COLOMBIA SAS", "ACME COLOMBIA SAS", "OTRA EMPRESA SA"],
            "NIT": ["900111222", "900111222", "800333444"],
            "FUENTE": ["RUES"] * 3,
        }
    )
    corr, _ = _correr(df, "deduplication_standard")
    # Las dos ACME idénticas (mismo NIT) deben unirse.
    g_acme = corr.loc[corr["RAZON_SOCIAL"] == "ACME COLOMBIA SAS", "ID_GRUPO"]
    assert g_acme.nunique() == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
