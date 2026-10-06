"""F1.14 — Tipos del golden y NAME_SIMILARITY_SCORE normalizado contra normalizado (C38).

Qué se protege
--------------
1. Tras ``linkage()``, los conteos del golden (``SOURCES_COUNT``,
   ``RECORD_COUNT``, ``NAME_VARIATIONS``, ``NIT_VARIATIONS``) son ``int64`` y
   ``REQUIRES_REVIEW`` es ``bool``. SQLite no tiene booleano y el ``0/1`` se
   colaba hasta el parquet publicado.
2. ``NAME_SIMILARITY_SCORE`` compara el nombre de la fila y el adoptado bajo la
   MISMA normalización (la huella del selector de golden): forma societaria,
   puntuación, mayúsculas y tildes no cuentan como diferencia. Un nombre
   realmente distinto sigue dando un puntaje bajo.
3. La regla de tipado vive una sola vez (``golden.tipos.tipar_golden``) y no
   repara en silencio: un conteo con nulos es un error con mensaje accionable
   (modo estricto) o queda sin tipar y con advertencia (modo tolerante del
   orquestador, mientras F1.1 cierra la fuente de esos nulos).

Empresas inventadas; ningún dato licenciado entra aquí.
"""

from __future__ import annotations

import logging
import tempfile
import warnings

import numpy as np
import pandas as pd
import pytest

from record_linkage.api import linkage
from record_linkage.golden.columnas_finales import garantizar_columnas_finales
from record_linkage.golden.selector import AdvancedValueSelector, huella_de_nombre
from record_linkage.golden.tipos import (
    TIPOS_METRICAS_GOLDEN,
    GoldenSinTiparError,
    tipar_golden,
)

CONTEOS = ("SOURCES_COUNT", "RECORD_COUNT", "NAME_VARIATIONS", "NIT_VARIATIONS")

# Mismo NIT y nombre con variación de forma legal/puntuación/tildes: el motor
# los une. «DEL SUR» es un nombre realmente distinto que comparte el NIT.
FUENTE_SINTETICA = pd.DataFrame(
    {
        "NIT": [
            "900123456",
            "900123456",
            "900123456",
            "800555111",
            "700000001",
            "700000001",
        ],
        "RAZON_SOCIAL": [
            "COMERCIALIZADORA ANDINA S.A.S.",
            "Comercializadora Andina SAS",
            "COMERCIALIZADORA ANDINA DEL SUR SAS",
            "GLOBEX LTDA",
            "FERRETERIA EL TORNILLO",
            "FERRETERÍA EL TORNILLO & CIA LTDA",
        ],
        "CIUDAD": ["BOGOTA"] * 6,
    }
)


@pytest.fixture(scope="module")
def resultado() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Corre ``linkage()`` UNA vez (sin L6) sobre el conjunto sintético."""
    with tempfile.TemporaryDirectory() as carpeta, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = linkage(
            sources={"RUES": FUENTE_SINTETICA.copy()},
            work_dir=carpeta,
            skip_reporting=True,
        )
        return res["golden"], res["correlative"]


# ── 1. Tipos del golden tras linkage() ─────────────────────────────────────


def test_los_conteos_del_golden_son_enteros(resultado) -> None:
    golden, _ = resultado
    for columna in CONTEOS:
        assert golden[columna].dtype == np.dtype("int64"), (
            f"{columna} salió {golden[columna].dtype}, debía ser int64"
        )


def test_requires_review_es_booleano(resultado) -> None:
    golden, _ = resultado
    assert golden["REQUIRES_REVIEW"].dtype == np.dtype("bool")


def test_el_golden_no_trae_nulos_en_las_metricas(resultado) -> None:
    golden, _ = resultado
    assert not golden[list(TIPOS_METRICAS_GOLDEN)].isna().any().any()


# ── 2. NAME_SIMILARITY_SCORE normalizado contra normalizado ────────────────


def _fila(correlativa: pd.DataFrame, razon_social: str) -> pd.Series:
    filas = correlativa.loc[correlativa["RAZON_SOCIAL"] == razon_social]
    assert len(filas) == 1, f"{razon_social!r} debía aparecer una vez en la correlativa"
    return filas.iloc[0]


def test_la_forma_legal_y_la_puntuacion_no_son_diferencia(resultado) -> None:
    """«S.A.S.» y «SAS», mayúsculas o no: el mismo nombre → 1,0."""
    _, correlativa = resultado
    original = _fila(correlativa, "COMERCIALIZADORA ANDINA S.A.S.")
    variante = _fila(correlativa, "Comercializadora Andina SAS")
    assert original["ID_GRUPO"] == variante["ID_GRUPO"], "el motor debía unirlas (mismo NIT)"
    assert original["NAME_SIMILARITY_SCORE"] == pytest.approx(1.0)
    assert variante["NAME_SIMILARITY_SCORE"] == pytest.approx(1.0)


def test_las_tildes_y_el_sufijo_cia_ltda_no_son_diferencia(resultado) -> None:
    _, correlativa = resultado
    simple = _fila(correlativa, "FERRETERIA EL TORNILLO")
    completa = _fila(correlativa, "FERRETERÍA EL TORNILLO & CIA LTDA")
    assert simple["ID_GRUPO"] == completa["ID_GRUPO"]
    assert simple["NAME_SIMILARITY_SCORE"] == pytest.approx(1.0)
    assert completa["NAME_SIMILARITY_SCORE"] == pytest.approx(1.0)


def test_un_nombre_realmente_distinto_puntua_bajo(resultado) -> None:
    _, correlativa = resultado
    del_sur = _fila(correlativa, "COMERCIALIZADORA ANDINA DEL SUR SAS")
    adoptado = _fila(correlativa, "COMERCIALIZADORA ANDINA S.A.S.")
    assert del_sur["ID_GRUPO"] == adoptado["ID_GRUPO"], "comparte NIT: el motor lo une"
    assert del_sur["NAME_SIMILARITY_SCORE"] < 0.9


def test_el_puntaje_esta_acotado_y_es_flotante(resultado) -> None:
    _, correlativa = resultado
    puntaje = correlativa["NAME_SIMILARITY_SCORE"]
    assert puntaje.dtype == np.dtype("float64")
    assert puntaje.between(0.0, 1.0).all()


# ── 2b. La misma regla, a nivel de unidad ──────────────────────────────────


def test_la_huella_es_la_del_selector_de_golden() -> None:
    """Una regla, una vez: la huella pública es la que usa el selector."""
    nombres = pd.Series(
        [
            "COMERCIALIZADORA ANDINA S.A.S.",
            "Comercializadora Andina SAS",
            "FERRETERÍA EL TORNILLO & CIA LTDA",
            "SASTRERIA LA ELEGANTE",
            None,
            "",
        ]
    )
    selector = AdvancedValueSelector({})
    assert huella_de_nombre(nombres).tolist() == selector._fingerprint_series(nombres).tolist()
    assert huella_de_nombre(nombres).tolist() == [
        "COMERCIALIZADORAANDINA",
        "COMERCIALIZADORAANDINA",
        "FERRETERIAELTORNILLO",
        "SASTRERIALAELEGANTE",
        "",
        "",
    ]


def test_garantizar_compara_razon_social_normalizada_contra_adoptada_normalizada() -> None:
    correlativa = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0, 0, 1],
            "NIT": ["900123456"] * 3 + ["800555111"],
            "RAZON_SOCIAL": [
                "COMERCIALIZADORA ANDINA S.A.S.",
                "Comercializadora Andina SAS",
                "COMERCIALIZADORA ANDINA DEL SUR SAS",
                "Distribuidora del Norte Ltda.",
            ],
            # NOMBRE_LIMPIO trae el residuo «S S» del limpiador: por eso NO es
            # la base de la comparación cuando RAZON_SOCIAL está disponible.
            "NOMBRE_LIMPIO": [
                "COMERCIALIZADORA ANDINA S S",
                "COMERCIALIZADORA ANDINA",
                "COMERCIALIZADORA ANDINA SUR",
                "DISTRIBUIDORA NORTE",
            ],
        }
    )
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [0, 1],
            "NIT_FINAL": ["900123456", "800555111"],
            "RAZON_SOCIAL_FINAL": [
                "COMERCIALIZADORA ANDINA S.A.S.",
                "DISTRIBUIDORA DEL NORTE LIMITADA",
            ],
        }
    )
    salida, reporte = garantizar_columnas_finales(correlativa, golden)
    puntajes = salida["NAME_SIMILARITY_SCORE"].tolist()
    assert puntajes[0] == pytest.approx(1.0)
    assert puntajes[1] == pytest.approx(1.0), "S.A.S. vs SAS no es diferencia"
    assert puntajes[2] < 0.9, "DEL SUR sí es otro nombre"
    assert puntajes[3] == pytest.approx(1.0), "Ltda. vs LIMITADA, tildes y mayúsculas"
    assert "RAZON_SOCIAL" in reporte.origen["NAME_SIMILARITY_SCORE"]
    assert "normalizad" in reporte.origen["NAME_SIMILARITY_SCORE"]


def test_sin_razon_social_se_usa_nombre_limpio_normalizado() -> None:
    correlativa = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0],
            "NIT": ["900123456", "900123456"],
            "NOMBRE_LIMPIO": ["ACME SAS", "ACME S A S"],
        }
    )
    golden = pd.DataFrame(
        {"ID_GRUPO": [0], "NIT_FINAL": ["900123456"], "RAZON_SOCIAL_FINAL": ["ACME S.A.S."]}
    )
    salida, reporte = garantizar_columnas_finales(correlativa, golden)
    assert salida["NAME_SIMILARITY_SCORE"].tolist()[0] == pytest.approx(1.0)
    assert "NOMBRE_LIMPIO" in reporte.origen["NAME_SIMILARITY_SCORE"]


def test_un_nombre_que_es_solo_forma_societaria_no_puntua_cero_contra_si_mismo() -> None:
    correlativa = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0, 1],
            "NIT": ["900123456", "900123456", "800555111"],
            "RAZON_SOCIAL": ["LTDA", "ACME LTDA", ""],
        }
    )
    golden = pd.DataFrame(
        {
            "ID_GRUPO": [0, 1],
            "NIT_FINAL": ["900123456", "800555111"],
            "RAZON_SOCIAL_FINAL": ["LTDA", "BETA SAS"],
        }
    )
    salida, _ = garantizar_columnas_finales(correlativa, golden)
    puntajes = salida["NAME_SIMILARITY_SCORE"].tolist()
    assert puntajes[0] == pytest.approx(1.0), "idéntico al adoptado, aunque sea solo forma legal"
    assert puntajes[1] < 0.9, "«ACME» contra el crudo «LTDA» no se parecen"
    assert puntajes[2] == 0.0, "sin nombre no hay similitud"


def test_la_regla_del_diagnostico_no_esta_duplicada_en_el_generador() -> None:
    """``_add_diagnostic_metrics`` era código muerto con la semántica vieja."""
    from record_linkage.golden.generator import GoldenRecordGeneratorV7

    assert not hasattr(GoldenRecordGeneratorV7, "_add_diagnostic_metrics")


# ── 3. tipar_golden: una regla, sin reparaciones silenciosas ───────────────


def _golden_crudo() -> pd.DataFrame:
    """Como sale de SQLite: conteos int64 y REQUIRES_REVIEW en 0/1."""
    return pd.DataFrame(
        {
            "ID_GRUPO": [0, 1, 2],
            "NIT_FINAL": ["900123456", "800555111", "700000001"],
            "SOURCES_COUNT": [2, 1, 1],
            "RECORD_COUNT": [3, 1, 2],
            "NAME_VARIATIONS": [3, 1, 2],
            "NIT_VARIATIONS": [1, 1, 1],
            "REQUIRES_REVIEW": [0, 1, 0],
        }
    )


def test_tipar_golden_convierte_el_cero_uno_a_booleano() -> None:
    tipado = tipar_golden(_golden_crudo())
    assert tipado["REQUIRES_REVIEW"].dtype == np.dtype("bool")
    assert tipado["REQUIRES_REVIEW"].tolist() == [False, True, False]
    for columna in CONTEOS:
        assert tipado[columna].dtype == np.dtype("int64")


def test_tipar_golden_acepta_flotantes_enteros_y_es_idempotente() -> None:
    crudo = _golden_crudo().astype({"SOURCES_COUNT": "float64", "REQUIRES_REVIEW": "float64"})
    una_vez = tipar_golden(crudo)
    dos_veces = tipar_golden(una_vez)
    assert una_vez["SOURCES_COUNT"].dtype == np.dtype("int64")
    assert una_vez["SOURCES_COUNT"].tolist() == [2, 1, 1]
    pd.testing.assert_frame_equal(una_vez, dos_veces)


def test_tipar_golden_acepta_mezcla_de_bool_e_int_en_requires_review() -> None:
    """Tras una consolidación que recalcula filas, bool e int conviven en object."""
    crudo = _golden_crudo()
    crudo["REQUIRES_REVIEW"] = pd.Series([True, 0, 1], dtype=object)
    tipado = tipar_golden(crudo)
    assert tipado["REQUIRES_REVIEW"].dtype == np.dtype("bool")
    assert tipado["REQUIRES_REVIEW"].tolist() == [True, False, True]


def test_tipar_golden_estricto_falla_con_nulos_y_dice_que_hacer() -> None:
    crudo = _golden_crudo().astype({"RECORD_COUNT": "float64"})
    crudo.loc[1, "RECORD_COUNT"] = np.nan
    with pytest.raises(GoldenSinTiparError) as excinfo:
        tipar_golden(crudo)
    mensaje = str(excinfo.value)
    assert "RECORD_COUNT" in mensaje
    for marca in ("Qué pasó", "Por qué importa", "Qué hacer"):
        assert marca in mensaje


def test_tipar_golden_tolerante_deja_la_columna_con_nulos_y_avisa(
    caplog: pytest.LogCaptureFixture,
) -> None:
    crudo = _golden_crudo().astype({"RECORD_COUNT": "float64"})
    crudo.loc[1, "RECORD_COUNT"] = np.nan
    with caplog.at_level(logging.WARNING):
        tipado = tipar_golden(crudo, estricto=False)
    assert tipado["RECORD_COUNT"].dtype == np.dtype("float64"), "no se inventa un valor"
    assert tipado["REQUIRES_REVIEW"].dtype == np.dtype("bool"), "lo demás sí se tipa"
    assert any("RECORD_COUNT" in r.getMessage() for r in caplog.records)


def test_tipar_golden_ignora_columnas_ausentes_y_no_toca_el_resto() -> None:
    parcial = pd.DataFrame({"ID_GRUPO": [0], "CONFIDENCE_SCORE": [0.85], "RECORD_COUNT": [1]})
    tipado = tipar_golden(parcial)
    assert tipado["RECORD_COUNT"].dtype == np.dtype("int64")
    assert tipado["CONFIDENCE_SCORE"].dtype == np.dtype("float64")
    assert "REQUIRES_REVIEW" not in tipado.columns


def test_tipar_golden_rechaza_un_valor_que_no_es_cero_ni_uno() -> None:
    crudo = _golden_crudo()
    crudo["REQUIRES_REVIEW"] = [0, 2, 0]
    with pytest.raises(GoldenSinTiparError, match="REQUIRES_REVIEW"):
        tipar_golden(crudo)
