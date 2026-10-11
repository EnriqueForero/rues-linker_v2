"""matching.locales.registrar_locale_compuesto: una sola forma de componer un locale (F3.2)."""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.flujo import ConfigImportadores, registrar_locale
from record_linkage.matching.locales import registrar_locale_compuesto
from record_linkage.matching.normalizadores import LOCALES, normalizar_nombre

NOMBRE = "PRUEBA_LOCALE_COMPUESTO"


@pytest.fixture(autouse=True)
def _sin_residuos():
    """El registro es global por proceso: cada prueba deja ``LOCALES`` como lo encontró."""
    antes = set(LOCALES)
    yield
    for clave in set(LOCALES) - antes:
        del LOCALES[clave]


def test_hereda_los_sufijos_de_las_bases_y_suma_los_extra():
    registrar_locale_compuesto(
        NOMBRE, bases=("ES", "EN"), sufijos_extra=["gmbh ", " kg"], genericos=["HOLDING"]
    )
    sufijos = LOCALES[NOMBRE]["sufijos"]
    assert sufijos >= LOCALES["ES"]["sufijos"] | LOCALES["EN"]["sufijos"]
    assert {"GMBH", "KG"} <= sufijos  # extras normalizados a mayúsculas sin espacios de borde
    assert LOCALES[NOMBRE]["genericos"] == frozenset({"HOLDING"})


def test_devuelve_el_nombre_y_guarda_conjuntos_inmutables():
    assert registrar_locale_compuesto(NOMBRE, bases=("ES",)) == NOMBRE
    assert isinstance(LOCALES[NOMBRE]["sufijos"], frozenset)
    assert isinstance(LOCALES[NOMBRE]["genericos"], frozenset)


def test_es_idempotente():
    registrar_locale_compuesto(
        NOMBRE, bases=("ES", "EN"), sufijos_extra=["KG"], genericos=["HOLDING"]
    )
    primero = dict(LOCALES[NOMBRE])
    registrar_locale_compuesto(
        NOMBRE, bases=("ES", "EN"), sufijos_extra=["KG"], genericos=["HOLDING"]
    )
    assert LOCALES[NOMBRE] == primero


def test_un_registro_con_el_mismo_nombre_pisa_al_anterior():
    registrar_locale_compuesto(NOMBRE, bases=("ES",), genericos=["UNO"])
    registrar_locale_compuesto(NOMBRE, bases=("ES",), genericos=["DOS"])
    assert LOCALES[NOMBRE]["genericos"] == frozenset({"DOS"})


def test_una_base_desconocida_falla_con_mensaje_accionable():
    with pytest.raises(KeyError, match=r"no está declarado.*Disponibles.*Qué hacer"):
        registrar_locale_compuesto(NOMBRE, bases=("ES", "XX"))
    assert NOMBRE not in LOCALES  # no deja un locale a medias


def test_normalizar_nombre_usa_el_locale_compuesto():
    sin = normalizar_nombre(pd.Series(["Zelvora Alimentos Kg"]), locale="ES")
    registrar_locale_compuesto(NOMBRE, bases=("ES", "EN"), sufijos_extra=["KG"])
    con = normalizar_nombre(pd.Series(["Zelvora Alimentos Kg"]), locale=NOMBRE)
    assert sin.iloc[0] == "ZELVORA ALIMENTOS KG"
    assert con.iloc[0] == "ZELVORA ALIMENTOS"


def test_importadores_registra_lo_mismo_que_antes_de_compartir_la_funcion():
    cfg = ConfigImportadores(col_razon_social="X", col_pais="Y", verboso=False)
    esperado_sufijos: set[str] = set()
    for base in cfg.locales_base:
        esperado_sufijos |= set(LOCALES[base]["sufijos"])
    esperado_sufijos |= {str(s).strip().upper() for s in cfg.sufijos_extra}
    assert registrar_locale(cfg) == cfg.locale
    assert LOCALES[cfg.locale]["sufijos"] == frozenset(esperado_sufijos)
    assert LOCALES[cfg.locale]["genericos"] == cfg.genericos_todos
