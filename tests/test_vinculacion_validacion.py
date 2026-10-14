"""Validación del motor automático contra una referencia (``flujo.vinculacion.validacion``).

Cada valor esperado salió de correr la función ORIGINAL del motor (la celda ``%%writefile`` del notebook 10) con las
mismas tablas: la extracción no cambia el comportamiento. Junto a cada caso va la regla que ejerce. Las empresas son
inventadas y las tablas se arman a mano con las columnas que lee la validación.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pytest

from record_linkage.flujo.vinculacion.validacion import validar_contra_referencia, wilson

#: Registros de dos destinos, en el orden de ``pos_b``.
PREP = {
    "ORBIS": pd.DataFrame(
        {
            "ID_REGISTRO": [f"ESB000000{k}" for k in range(1, 7)],
            "NOMBRE_ORIGINAL": [
                "Zelvora Alimentos SA",
                "Kramento Foods SL",
                "Orbelia Conservas",
                "Tavorín Marinas",
                "Quelmar",
                "Vantiko Chocolates",
            ],
        }
    ),
    "FDI": pd.DataFrame(
        {
            "ID_REGISTRO": ["FDI-F0002", "FDI-F0003"],
            "NOMBRE_ORIGINAL": ["Zelvora Foods", "Kramento"],
        }
    ),
}


def _auto(filas: list[tuple[str, int, int, str, str, str]]) -> pd.DataFrame:
    """Pares del motor automático: ``(fuente, ENT, pos_b, NIVEL_AUTO, BANDA_AUTO, MISMO_PAIS)``."""
    return pd.DataFrame(
        filas,
        columns=["FUENTE_DESTINO", "ENT", "pos_b", "NIVEL_AUTO", "BANDA_AUTO", "MISMO_PAIS"],
    )


def _ref(filas: list[tuple[str, int, int, str]]) -> pd.DataFrame:
    """Referencia ya traducida por ``mapear_decisiones``: ``(fuente, ENT, pos_b, DECISION)``."""
    return pd.DataFrame(filas, columns=["FUENTE_DESTINO", "ENT", "pos_b", "DECISION"])


#: Motor automático de la prueba principal: dos aciertos, tres falsos positivos y un candidato C.
AUTO = _auto(
    [
        ("ORBIS", 0, 0, "EMPRESA", "A", "SÍ"),  # acierto, nivel correcto
        ("ORBIS", 1, 1, "GRUPO", "B", "SÍ"),  # acierto, la referencia dice EMPRESA
        ("ORBIS", 2, 2, "EMPRESA", "B", "NO"),  # falso positivo: la referencia lo descarta
        ("ORBIS", 3, 3, "EMPRESA", "A", "SÍ"),  # falso positivo: la referencia no lo incluye
        ("ORBIS", 4, 4, "REVISAR", "C", "SÍ"),  # candidato C: no es un vínculo automático
        ("ORBIS", 1, 5, "GRUPO", "B", "NO"),  # falso positivo entre países, no incluido
    ]
)

#: Referencia de la prueba principal: cuatro vínculos y dos descartes.
REF = _ref(
    [
        ("ORBIS", 0, 0, "EMPRESA"),
        ("ORBIS", 1, 1, "EMPRESA"),
        ("ORBIS", 2, 2, "NO_VINCULAR"),
        ("ORBIS", 4, 4, "GRUPO"),  # el motor lo propuso como candidato C
        ("ORBIS", 5, 0, "EMPRESA"),  # el motor ni lo propuso
        ("ORBIS", 6, 1, "RELACION_PROPIEDAD"),  # descarte: no es EMPRESA ni GRUPO
    ]
)


def _sin_nan(fila: pd.Series) -> dict[str, Any]:
    """La fila como diccionario, con los NaN en ``None`` para poder compararla con ``==``."""
    return {
        k: (None if isinstance(v, float) and math.isnan(v) else v)
        for k, v in fila.to_dict().items()
    }


def _discrepancias(d: pd.DataFrame) -> list[tuple[Any, ...]]:
    """Las discrepancias como tuplas ``(TIPO, ENT, ID_DESTINO, NOMBRE_DESTINO, NIVEL_AUTO, REFERENCIA)``."""
    return [
        (r.TIPO, r.ENT, r.ID_DESTINO, r.NOMBRE_DESTINO, r.NIVEL_AUTO, r.REFERENCIA)
        for r in d.itertuples(index=False)
    ]


#: Separador de los intervalos de Wilson en el Excel (``0.12`` y ``0.77`` unidos por un guion largo, U+2013).
GUION = "\u2013"

FP = "Falso positivo (el motor vincula, la referencia no)"
FN = "Falso negativo (la referencia vincula, el motor no)"
FN_C = FN + " · el motor lo propuso como candidato C"
FN_NADA = FN + " · ni siquiera candidato"


# ── wilson ────────────────────────────────────────────────────────────────────


def test_wilson_sin_intentos_da_nan():
    """Con ``n = 0`` no hay proporción que acotar: los dos extremos son NaN."""
    inferior, superior = wilson(0, 0)
    assert math.isnan(inferior)
    assert math.isnan(superior)


@pytest.mark.parametrize(
    ("k", "n", "z", "esperado"),
    [
        pytest.param(0, 10, 1.96, (0.0, 0.278), id="ningun_acierto"),
        pytest.param(10, 10, 1.96, (0.722, 1.0), id="todos_aciertan"),
        pytest.param(5, 10, 1.96, (0.237, 0.763), id="mitad"),
        pytest.param(3, 4, 1.96, (0.301, 0.954), id="muestra_chica"),
        pytest.param(1, 1, 1.96, (0.207, 1.0), id="un_solo_acierto"),
        pytest.param(0, 1, 1.96, (0.0, 0.793), id="un_solo_fallo"),
        pytest.param(50, 100, 1.96, (0.404, 0.596), id="cien_intentos"),
        pytest.param(999, 1000, 1.96, (0.994, 1.0), id="mil_intentos_casi_todos"),
        pytest.param(1, 20, 2.576, (0.006, 0.318), id="z_de_99_por_ciento"),
    ],
)
def test_wilson_intervalo_de_referencia(k, n, z, esperado):
    """El intervalo de Wilson, redondeado a 3 decimales, coincide con el del original (``z`` por defecto 1,96)."""
    assert wilson(k, n, z) == esperado


def test_wilson_usa_z_196_por_defecto():
    """Sin ``z`` el intervalo es el del 95 %."""
    assert wilson(5, 10) == wilson(5, 10, 1.96) == (0.237, 0.763)


def test_wilson_siempre_queda_dentro_de_cero_y_uno():
    """Para cualquier ``k <= n`` el intervalo está acotado a [0, 1] y el inferior no pasa al superior."""
    for n in range(1, 31):
        for k in range(n + 1):
            inferior, superior = wilson(k, n)
            assert 0.0 <= inferior <= superior <= 1.0


# ── validar_contra_referencia ─────────────────────────────────────────────────


def test_metricas_de_un_destino():
    """Precisión, recall, F1, intervalos y conteos de un destino con aciertos, falsos y descartes."""
    metricas, _ = validar_contra_referencia(AUTO, REF, PREP, ["ORBIS"])
    assert len(metricas) == 1
    assert _sin_nan(metricas.iloc[0]) == {
        "FUENTE_DESTINO": "ORBIS",
        "VINCULOS_AUTOMATICOS_A_B": 5,  # el candidato C no cuenta
        "VINCULOS_REFERENCIA": 4,  # los dos descartes no cuentan
        "ACIERTOS": 2,
        "PRECISION": 0.4,
        "PRECISION_IC95": f"0.12{GUION}0.77",
        "RECALL": 0.5,
        "RECALL_IC95": f"0.15{GUION}0.85",
        "F1": 0.4444,
        "RECALL_CONTANDO_CANDIDATOS_C": 0.75,  # 3 de los 4 de la referencia son al menos candidatos
        "NIVEL_CORRECTO_EN_ACIERTOS": "1/2",  # uno de los dos aciertos tiene otro nivel
        "AUTOMATICOS_ENTRE_PAISES": 2,  # MISMO_PAIS == "NO" entre los vínculos automáticos
        "DESCARTES_EN_REFERENCIA": 2,
    }


def test_las_columnas_de_las_dos_tablas_son_las_del_contrato():
    """El orden de las columnas es parte del contrato (el Excel las lee por nombre y en ese orden)."""
    metricas, discrepancias = validar_contra_referencia(AUTO, REF, PREP, ["ORBIS"])
    assert list(metricas.columns) == [
        "FUENTE_DESTINO",
        "VINCULOS_AUTOMATICOS_A_B",
        "VINCULOS_REFERENCIA",
        "ACIERTOS",
        "PRECISION",
        "PRECISION_IC95",
        "RECALL",
        "RECALL_IC95",
        "F1",
        "RECALL_CONTANDO_CANDIDATOS_C",
        "NIVEL_CORRECTO_EN_ACIERTOS",
        "AUTOMATICOS_ENTRE_PAISES",
        "DESCARTES_EN_REFERENCIA",
    ]
    assert list(discrepancias.columns) == [
        "FUENTE_DESTINO",
        "TIPO",
        "ENT",
        "ID_DESTINO",
        "NOMBRE_DESTINO",
        "NIVEL_AUTO",
        "REFERENCIA",
    ]


def test_falsos_positivos_y_negativos_con_su_etiqueta():
    """Primero los falsos positivos y luego los negativos, cada grupo ordenado por (empresa, registro)."""
    _, d = validar_contra_referencia(AUTO, REF, PREP, ["ORBIS"])
    assert _discrepancias(d) == [
        (
            FP,
            1,
            "ESB0000006",
            "Vantiko Chocolates",
            "GRUPO",
            "no incluido",
        ),  # la referencia no lo trae
        (
            FP,
            2,
            "ESB0000003",
            "Orbelia Conservas",
            "EMPRESA",
            "descartado",
        ),  # la referencia lo descarta
        (FP, 3, "ESB0000004", "Tavorín Marinas", "EMPRESA", "no incluido"),
        (FN_C, 4, "ESB0000005", "Quelmar", "REVISAR", "GRUPO"),  # estaba entre los candidatos
        (FN_NADA, 5, "ESB0000001", "Zelvora Alimentos SA", "", "EMPRESA"),  # el motor ni lo propuso
    ]


def test_sin_referencia_la_precision_es_cero_y_el_recall_es_nan():
    """Sin vínculos de referencia todo vínculo automático es falso positivo y el recall no está definido."""
    metricas, d = validar_contra_referencia(AUTO, REF.iloc[0:0], PREP, ["ORBIS"])
    assert (
        _sin_nan(metricas.iloc[0])
        == {
            "FUENTE_DESTINO": "ORBIS",
            "VINCULOS_AUTOMATICOS_A_B": 5,
            "VINCULOS_REFERENCIA": 0,
            "ACIERTOS": 0,
            "PRECISION": 0.0,
            "PRECISION_IC95": f"0.00{GUION}0.43",
            "RECALL": None,
            "RECALL_IC95": f"nan{GUION}nan",
            "F1": 0.0,  # F3.1b (C11): sin aciertos y con vínculos automáticos que medir, el F1 es 0,0 (antes NaN)
            "RECALL_CONTANDO_CANDIDATOS_C": None,
            "NIVEL_CORRECTO_EN_ACIERTOS": "0/0",
            "AUTOMATICOS_ENTRE_PAISES": 2,
            "DESCARTES_EN_REFERENCIA": 0,
        }
    )
    assert [t[0] for t in _discrepancias(d)] == [FP] * 5
    assert {t[5] for t in _discrepancias(d)} == {"no incluido"}


def test_sin_vinculos_automaticos_la_precision_es_nan():
    """Sin vínculos automáticos la precisión no está definida y todo vínculo de la referencia es falso negativo."""
    metricas, d = validar_contra_referencia(AUTO.iloc[0:0], REF, PREP, ["ORBIS"])
    fila = _sin_nan(metricas.iloc[0])
    assert fila["PRECISION"] is None
    assert fila["PRECISION_IC95"] == f"nan{GUION}nan"
    assert fila["RECALL"] == 0.0
    assert fila["RECALL_IC95"] == f"0.00{GUION}0.49"
    assert fila["F1"] == 0.0  # F3.1b (C11): recall 0 con algo de referencia que medir; antes NaN
    assert fila["RECALL_CONTANDO_CANDIDATOS_C"] == 0.0
    assert [t[0] for t in _discrepancias(d)] == [FN_NADA] * 4  # sin candidatos, ni siquiera C


def test_f1_es_cero_si_la_precision_y_el_recall_valen_cero():
    """F3.1b (C11): con precisión y recall en 0 el F1 es 0,0 (el original devolvía NaN)."""
    auto = _auto([("ORBIS", 0, 1, "EMPRESA", "A", "SÍ")])
    ref = _ref([("ORBIS", 0, 0, "EMPRESA")])
    metricas, _ = validar_contra_referencia(auto, ref, PREP, ["ORBIS"])
    fila = _sin_nan(metricas.iloc[0])
    assert (fila["PRECISION"], fila["RECALL"], fila["ACIERTOS"]) == (0.0, 0.0, 0)
    assert fila["F1"] == 0.0


def test_una_fila_de_metricas_por_destino_en_el_orden_pedido():
    """Cada destino se valida con sus propios pares; la salida sigue el orden de ``destinos``."""
    auto = _auto([("ORBIS", 0, 0, "EMPRESA", "A", "SÍ"), ("FDI", 0, 0, "GRUPO", "B", "SÍ")])
    ref = _ref([("ORBIS", 0, 0, "EMPRESA"), ("FDI", 0, 0, "GRUPO"), ("FDI", 1, 1, "EMPRESA")])
    metricas, d = validar_contra_referencia(auto, ref, PREP, ["ORBIS", "FDI"])
    assert metricas["FUENTE_DESTINO"].tolist() == ["ORBIS", "FDI"]
    orbis, fdi = (_sin_nan(metricas.iloc[k]) for k in (0, 1))
    assert (fdi["RECALL"], fdi["PRECISION"], fdi["F1"]) == (0.5, 1.0, 0.6667)
    assert (orbis["RECALL"], orbis["PRECISION"], orbis["F1"]) == (1.0, 1.0, 1.0)
    # El falso negativo de FDI (ENT 1, registro 1) se nombra con los datos de SU destino.
    assert _discrepancias(d) == [(FN_NADA, 1, "FDI-F0003", "Kramento", "", "EMPRESA")]


def test_las_tasas_se_redondean_a_cuatro_decimales():
    """Dos aciertos de tres vínculos automáticos y tres de referencia: precisión, recall y F1 de 0,6667 (no 0,67)."""
    auto = _auto([("ORBIS", k, k, "EMPRESA", "A", "SÍ") for k in range(3)])
    ref = _ref([("ORBIS", 0, 0, "EMPRESA"), ("ORBIS", 1, 1, "EMPRESA"), ("ORBIS", 3, 3, "EMPRESA")])
    metricas, _ = validar_contra_referencia(auto, ref, PREP, ["ORBIS"])
    fila = _sin_nan(metricas.iloc[0])
    assert (fila["PRECISION"], fila["RECALL"], fila["F1"]) == (0.6667, 0.6667, 0.6667)
    assert fila["RECALL_CONTANDO_CANDIDATOS_C"] == 0.6667
    assert (fila["PRECISION_IC95"], fila["RECALL_IC95"]) == (f"0.21{GUION}0.94", f"0.21{GUION}0.94")


def test_los_falsos_negativos_salen_ordenados_por_empresa_y_registro():
    """Los falsos negativos se listan en orden de (empresa, registro), no en el de un conjunto."""
    ref = _ref([("ORBIS", 4, 1, "GRUPO"), ("ORBIS", 0, 5, "EMPRESA")])
    _, d = validar_contra_referencia(AUTO.iloc[0:0], ref, PREP, ["ORBIS"])
    assert [(t[1], t[2]) for t in _discrepancias(d)] == [(0, "ESB0000006"), (4, "ESB0000002")]


def test_sin_destinos_devuelve_tablas_vacias_y_sin_columnas():
    """Sin destinos que validar no hay filas ni columnas."""
    metricas, discrepancias = validar_contra_referencia(AUTO, REF, PREP, [])
    assert metricas.shape == (0, 0)
    assert discrepancias.shape == (0, 0)


def test_un_destino_sin_pares_ni_referencia_da_una_fila_de_nan_y_ceros():
    """Un destino sin nada que comparar sigue teniendo su fila: conteos en 0 y las tasas sin definir."""
    metricas, d = validar_contra_referencia(AUTO, REF, PREP, ["FDI"])
    fila = _sin_nan(metricas.iloc[0])
    assert fila["VINCULOS_AUTOMATICOS_A_B"] == fila["VINCULOS_REFERENCIA"] == fila["ACIERTOS"] == 0
    assert fila["PRECISION"] is None
    assert fila["RECALL"] is None
    assert d.empty


def test_sin_ningun_par_candidato_la_validacion_da_metricas_en_cero_en_vez_de_caer():
    """F3.1b (C10): sin ningún par ``L_auto`` no trae ``MISMO_PAIS``; con referencia antes caía con KeyError."""
    sin_pares = AUTO.drop(columns="MISMO_PAIS").iloc[0:0]
    metricas, d = validar_contra_referencia(sin_pares, REF, PREP, ["ORBIS"])
    fila = _sin_nan(metricas.iloc[0])
    assert (fila["VINCULOS_AUTOMATICOS_A_B"], fila["ACIERTOS"], fila["RECALL"], fila["F1"]) == (
        0,
        0,
        0.0,
        0.0,
    )
    assert fila["PRECISION"] is None
    assert [t[0] for t in _discrepancias(d)] == [FN_NADA] * 4


def test_un_vinculo_automatico_de_banda_c_o_de_otro_nivel_no_cuenta_como_vinculo():
    """Solo EMPRESA/GRUPO en banda A o B es un vínculo automático; lo demás es, a lo sumo, candidato."""
    auto = _auto(
        [
            ("ORBIS", 0, 0, "EMPRESA", "C", "SÍ"),  # banda C
            ("ORBIS", 1, 1, "RELACION_MATRIZ", "B", "SÍ"),  # otro nivel
            ("ORBIS", 2, 2, "EMPRESA", "A", "SÍ"),  # vínculo
        ]
    )
    ref = _ref([("ORBIS", 0, 0, "EMPRESA"), ("ORBIS", 1, 1, "GRUPO")])
    metricas, d = validar_contra_referencia(auto, ref, PREP, ["ORBIS"])
    fila = _sin_nan(metricas.iloc[0])
    assert fila["VINCULOS_AUTOMATICOS_A_B"] == 1
    assert fila["ACIERTOS"] == 0
    assert fila["RECALL_CONTANDO_CANDIDATOS_C"] == 1.0  # los dos de la referencia eran candidatos
    assert [t[0] for t in _discrepancias(d)] == [FP, FN_C, FN_C]
