"""Esquema, normalizadores, bloqueo y salvaguardas del motor multicampo (F2).

Cubre: validación fail-fast del esquema (F2.1), normalización por locale sin
inferencia de corpus con piso anti-percolación (F2.3), política de faltantes
y salvaguarda de override (F2.4), y completitud del bloqueo componible (F2.5).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from record_linkage.matching.campos import (
    CampoSpec,
    EsquemaCampos,
    PoliticaFaltante,
    TipoCampo,
    esquema_rues,
)
from record_linkage.matching.motor_bloqueo import (
    BloqueoComponible,
    LlaveExacta,
    LSHTexto,
)
from record_linkage.matching.normalizadores import (
    PLACEHOLDERS,
    es_faltante,
    normalizar_direccion,
    normalizar_nombre,
    normalizar_telefono,
)


def test_identificador_float_no_fabrica_cero_y_nan_es_determinista() -> None:
    from record_linkage.matching.normalizadores import normalizar_identificador

    raw = pd.Series([890002474.0, np.nan, 1.5, float(2**53 + 2)])
    esperado = ["890002474", "", "", ""]
    assert normalizar_identificador(raw).tolist() == esperado
    assert normalizar_identificador(raw).tolist() == esperado


# ─── F2.1: tipos y validación del esquema ──────────────────────────────────


def test_campo_peso_invalido() -> None:
    with pytest.raises(ValueError, match="peso"):
        CampoSpec("X", TipoCampo.CIUDAD, peso=0.0)


def test_campo_locale_invalido() -> None:
    with pytest.raises(KeyError, match="locale"):
        CampoSpec("NOMBRE", TipoCampo.NOMBRE_EMPRESA, locale="FR")


def test_esquema_campos_duplicados() -> None:
    with pytest.raises(ValueError, match="duplicados"):
        EsquemaCampos(
            campos=[
                CampoSpec("NIT", TipoCampo.IDENTIFICADOR),
                CampoSpec("NIT", TipoCampo.CIUDAD),
            ]
        )


def test_esquema_valida_columnas_faltantes_accionable() -> None:
    esq = esquema_rues()
    df = pd.DataFrame({"NIT": ["1"], "OTRA": ["x"]})
    with pytest.raises(ValueError) as exc:
        esq.validar(df)
    msg = str(exc.value)
    assert "Qué pasó" in msg and "RAZON_SOCIAL" in msg and "Qué hacer" in msg


def test_identificador_veta_por_defecto() -> None:
    """Un identificador veta discrepancias por defecto; otros tipos no."""
    assert CampoSpec("NIT", TipoCampo.IDENTIFICADOR).veta_discrepancia is True
    assert CampoSpec("CIUDAD", TipoCampo.CIUDAD).veta_discrepancia is False


# ─── F2.3: normalizadores por locale con piso anti-percolación ─────────────


def test_normalizar_nombre_quita_sufijo_no_generico() -> None:
    s = normalizar_nombre(pd.Series(["ACME COLOMBIA S.A.S."]), quitar_genericos=False)
    assert s.iloc[0] == "ACME COLOMBIA"


def test_normalizar_nombre_piso_min_tokens() -> None:
    """Quitar genéricos NUNCA deja el nombre por debajo de min_tokens (F2.3)."""
    # "INVERSIONES GLOBAL" son ambos genéricos: quitarlos vaciaría el nombre.
    s = normalizar_nombre(
        pd.Series(["INVERSIONES GLOBAL"]),
        quitar_genericos=True,
        min_tokens=2,
    )
    # Se conserva el original (el recorte habría violado el piso).
    assert s.iloc[0] == "INVERSIONES GLOBAL"


def test_normalizar_telefono_quita_prefijo_pais() -> None:
    s = normalizar_telefono(pd.Series(["+57 601 7502020", "3001234567"]))
    assert s.iloc[0] == "6017502020"
    assert s.iloc[1] == "3001234567"


def test_normalizar_direccion_abreviaturas() -> None:
    s = normalizar_direccion(pd.Series(["CL 100 # 15 - 20", "CRA 7 No 80-10"]))
    assert s.iloc[0].startswith("CALLE 100")
    assert "CARRERA 7" in s.iloc[1]


def test_placeholders_son_faltante() -> None:
    serie = pd.Series(["SIN DATO", "N/A", "0", "empresa real", None])
    mask = es_faltante(serie)
    assert mask.tolist() == [True, True, True, False, True]
    assert "SIN DATO" in PLACEHOLDERS


# ─── F2.4: política de faltantes y salvaguarda de override ─────────────────


def test_motor_faltante_no_dispara_override() -> None:
    """Dos registros con NIT faltante NO se fusionan solo por eso (F2.4)."""
    from record_linkage.matching.campos import esquema_multicampo_completo
    from record_linkage.matching.motor_multicampo import evaluar_esquema

    df = pd.DataFrame(
        {
            "NIT": ["", ""],  # ambos faltantes
            "RAZON_SOCIAL": ["EMPRESA UNO SAS", "EMPRESA DOS SAS"],
            "TELEFONO": ["", ""],
            "EMAIL": ["", ""],
            "DIRECCION": ["", ""],
            "CIUDAD": ["BOGOTA", "MEDELLIN"],
            "LAT": [np.nan, np.nan],
            "LON": [np.nan, np.nan],
        }
    )
    esq = esquema_multicampo_completo()
    bloq = BloqueoComponible([LSHTexto("RAZON_SOCIAL", umbral=0.3, permutaciones=32)])
    res = evaluar_esquema(df, esq, bloq)
    # Ningún override por faltantes: no deben fusionarse.
    assert res.n_fusiones == 0


def test_politica_bloquear_impide_fusion() -> None:
    from record_linkage.matching.motor_multicampo import evaluar_esquema

    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME S.A.S."],
            "CIUDAD": ["BOGOTA", ""],  # falta en uno
        }
    )
    esq = EsquemaCampos(
        campos=[
            CampoSpec("NIT", TipoCampo.IDENTIFICADOR, peso=3.0),
            CampoSpec("RAZON_SOCIAL", TipoCampo.NOMBRE_EMPRESA, peso=2.0),
            CampoSpec(
                "CIUDAD",
                TipoCampo.CIUDAD,
                peso=1.0,
                faltante=PoliticaFaltante.BLOQUEAR,
            ),
        ],
        umbral_score=0.5,
        min_concordancias=1,
    )
    bloq = BloqueoComponible([LlaveExacta("NIT")])
    res = evaluar_esquema(df, esq, bloq)
    assert res.n_fusiones == 0  # CIUDAD BLOQUEAR con faltante ⇒ no fusiona


# ─── F2.5: bloqueo componible y completitud ────────────────────────────────


def test_llave_exacta_omite_grupo_degenerado() -> None:
    """Una llave con demasiados iguales se omite (evita O(n²))."""
    v = {"K": np.array(["A"] * 10 + ["B", "B"])}
    est = LlaveExacta("K", max_grupo=5)
    pares = est.pares(v)
    # El grupo de 10 "A" se omite; el de 2 "B" pasa.
    assert len(pares) == 1
    assert est.grupos_omitidos and est.grupos_omitidos[0][1] == 10


def test_lsh_no_tiene_acantilado_al_superar_max_grupo() -> None:
    """501 textos iguales conservan conectividad sin materializar O(n²)."""
    valores = {"N": np.full(501, "EMPRESA IDENTICA", dtype=object)}
    est = LSHTexto("N", umbral=0.4, permutaciones=32, max_grupo=500)

    pares = est.pares(valores)

    assert len(pares) == 500
    assert est.grupos_degradados
    assert np.array_equal(pares[:, 0], np.arange(500))
    assert np.array_equal(pares[:, 1], np.arange(1, 501))


def test_presupuesto_se_aplica_antes_de_materializar_grupo_cuadratico() -> None:
    from record_linkage.matching.motor_bloqueo import CandidateBudgetExceeded

    valores = {"K": np.full(2_000, "MISMA_LLAVE", dtype=object)}
    est = LlaveExacta("K", max_grupo=2_000)

    with pytest.raises(CandidateBudgetExceeded, match="1,999,000"):
        est.pares(valores, max_pares=10_000)


def test_llave_exacta_ignora_faltantes() -> None:
    v = {"K": np.array(["", "", "X", "X"])}
    pares = LlaveExacta("K").pares(v)
    assert len(pares) == 1  # solo el par de "X"; los "" no generan pares


def test_bloqueo_pares_canonicos_unicos() -> None:
    """La unión no repite pares y respeta i < j."""
    v = {
        "K": np.array(["A", "A", "B"]),
        "N": np.array(["ACME", "ACME", "OTRA"]),
    }
    bloq = BloqueoComponible([LlaveExacta("K"), LSHTexto("N", umbral=0.3, permutaciones=32)])
    union, _ = bloq.pares(v)
    assert np.all(union[:, 0] < union[:, 1])
    assert len(np.unique(union, axis=0)) == len(union)
