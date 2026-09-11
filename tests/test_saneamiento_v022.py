"""Saneamiento previo a normalizar (v0.22.1)."""

from __future__ import annotations

import pandas as pd

from record_linkage.processing.saneamiento import (
    ascii_mayusculas,
    sanear_texto,
    unir_iniciales,
)


def test_entidades_html_y_controles_desaparecen():
    s = pd.Series(["&#147;ACME&#148; LLC", "ACME\x00 LLC", "ACME" + "\u00a0" + "LLC"])
    out = sanear_texto(s)
    assert not out.str.contains("&#").any()
    assert not out.str.contains("\x00").any()
    assert (out.str.count(" ") <= 1).all()


def test_codigo_de_cliente_inicial_se_quita():
    s = pd.Series(["13158733- CONSTRUCTORA SCHEKER", "(41X01)- KLEVER GUEVARA"])
    out = sanear_texto(s)
    assert out.iloc[0] == "CONSTRUCTORA SCHEKER"
    assert out.iloc[1] == "KLEVER GUEVARA"


def test_care_of_se_quita():
    out = sanear_texto(pd.Series(["JAEL VIDAL C/O ZOOOLOGISTICS BV"]))
    assert "C/O" not in out.iloc[0]


def test_el_orden_importa_tildes_antes_que_puntuacion():
    """Invertir el orden borraba la É de AMÉRICA y tumbaba la canonización.

    El patrón de puntuación se evalúa con semántica ASCII (pandas 3 delega en
    Arrow/RE2). Esta prueba fija el orden correcto para siempre.
    """
    s = pd.Series(["ESTADOS UNIDOS DE AMÉRICA"])
    correcto = unir_iniciales(ascii_mayusculas(sanear_texto(s)))
    assert correcto.iloc[0] == "ESTADOS UNIDOS DE AMERICA"


def test_iniciales_sueltas_se_reunen():
    s = pd.Series(["O.M.G QUIMICOS C.A", "U.S PLASTICS TRADING", "D.R. WAKEFIELD"])
    out = unir_iniciales(ascii_mayusculas(sanear_texto(s)))
    assert out.iloc[0] == "OMG QUIMICOS CA"
    assert out.iloc[1] == "US PLASTICS TRADING"
    assert out.iloc[2].startswith("DR WAKEFIELD")


def test_unir_iniciales_se_puede_apagar():
    s = pd.Series(["O.M.G QUIMICOS"])
    limpio = ascii_mayusculas(sanear_texto(s))
    assert unir_iniciales(limpio, activo=False).iloc[0] == "O M G QUIMICOS"


def test_nulos_y_vacios_no_revientan():
    s = pd.Series([None, "", "   ", pd.NA], dtype="object")
    out = unir_iniciales(ascii_mayusculas(sanear_texto(s)))
    assert (out == "").all()


def test_prelimpieza_es_configurable():
    s = pd.Series(["EMPRESA XYZ SUCURSAL"])
    out = sanear_texto(s, prelimpieza=((r"\bSUCURSAL\b", ""),))
    assert out.iloc[0] == "EMPRESA XYZ"
