"""Canonización de países contra el catálogo declarado (v0.22.1)."""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.paises import (
    CATALOGO_PAISES,
    AliasAmbiguo,
    canonizar_pais,
    indice_paises,
    sugerir_alias_pais,
)


def test_catalogo_sin_iso_ni_nombre_duplicado():
    """Un ISO o un nombre repetido haría ambigua la salida."""
    iso = [c[0] for c in CATALOGO_PAISES]
    nombres = [c[1] for c in CATALOGO_PAISES]
    assert len(iso) == len(set(iso))
    assert len(nombres) == len(set(nombres))


def test_indice_detecta_alias_ambiguo():
    catalogo = (("AAA", "PAIS A", ("COMUN",)), ("BBB", "PAIS B", ("COMUN",)))
    with pytest.raises(AliasAmbiguo, match="COMUN"):
        indice_paises(catalogo)


@pytest.mark.parametrize(
    ("grafia", "iso"),
    [
        # Los pares que NINGUNA similitud de cadenas resuelve: son la razón de
        # que el catálogo sea declarado y no inferido.
        ("TÜRKIYE", "TUR"),
        ("Turquía", "TUR"),
        ("CHEQUIA", "CZE"),
        ("República Checa", "CZE"),
        ("YIBUTI", "DJI"),
        ("DJIBOUTI", "DJI"),
        ("COSTA DE MARFIL", "CIV"),
        ("COTE D'IVOIRE", "CIV"),
        ("ESTADOS UNIDOS DE AMÉRICA", "USA"),
        ("Estados Unidos", "USA"),
        ("FEDERACION DE RUSIA", "RUS"),
        ("Rusia", "RUS"),
    ],
)
def test_grafias_dificiles_mapean_al_mismo_pais(grafia, iso):
    r = canonizar_pais(pd.Series([grafia]))
    assert r.tabla["PAIS_ISO3"].iloc[0] == iso


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # Se parecen muchísimo y son países distintos: el fuzzy matching los
        # uniría. Esta prueba es el contraejemplo permanente.
        ("GUINEA", "GUINEA ECUATORIAL"),
        ("GUINEA", "GUINEA-BISSAU"),
        ("CONGO", "REPUBLICA DEMOCRATICA DEL CONGO"),
        ("COREA DEL SUR", "COREA DEL NORTE"),
        ("SUDAN", "SUDAFRICA"),
    ],
)
def test_paises_parecidos_no_se_confunden(a, b):
    r = canonizar_pais(pd.Series([a, b]))
    assert r.tabla["PAIS_ISO3"].iloc[0] != r.tabla["PAIS_ISO3"].iloc[1]


def test_zona_franca_no_es_pais():
    s = pd.Series(
        [
            "ZONA FRANCA PERMANENTE BOGOTA",
            "ZFP DE BARRANQUILLA S.A.",
            "ZFPE FEMSA",
            "COLOMBIA",
        ]
    )
    r = canonizar_pais(s)
    assert list(r.tabla["PAIS_METODO"]) == [
        "zona_franca",
        "zona_franca",
        "zona_franca",
        "catalogo_exacto",
    ]
    assert r.tabla["PAIS_FINAL"].iloc[:3].nunique() == 1


def test_lo_no_clasificado_se_marca_nunca_se_adivina():
    r = canonizar_pais(pd.Series(["NARNIA", "ECUADOR"]))
    assert r.tabla["PAIS_METODO"].iloc[0] == "sin_clasificar"
    assert r.tabla["PAIS_FINAL"].iloc[0] == "SIN CLASIFICAR"
    assert r.cobertura == pytest.approx(0.5)


def test_sugerencia_es_solo_sugerencia():
    """NARNIA se parece a ARMENIA: por eso la sugerencia no se aplica sola."""
    r = canonizar_pais(pd.Series(["NARNIA"]))
    sug = sugerir_alias_pais(r.sin_clasificar)
    assert len(sug) == 1
    assert sug["similitud"].iloc[0] > 0.8  # alta, y aun así incorrecta
    assert r.tabla["PAIS_ISO3"].iloc[0] == "ZZZ"


def test_puntuacion_y_tildes_no_cambian_el_resultado():
    s = pd.Series(["SUDAFRICA, REPUBLICA DE", "SUDÁFRICA REPÚBLICA DE", "Sudafrica"])
    r = canonizar_pais(s)
    assert r.tabla["PAIS_ISO3"].nunique() == 1


def test_serie_vacia_no_revienta():
    r = canonizar_pais(pd.Series([], dtype="object"))
    assert r.tabla.empty
    assert r.cobertura == 1.0


# ── agrupar_sin_clasificar (v0.22.3) ─────────────────────────────────────


def test_agrupar_sin_clasificar_true_es_el_historico():
    r = canonizar_pais(pd.Series(["NO DEFINIDO", "VARIOS", "ALEMANIA"]))
    assert r.tabla.PAIS_FINAL.tolist()[:2] == ["SIN CLASIFICAR", "SIN CLASIFICAR"]
    assert r.tabla.PAIS_METODO.tolist()[:2] == ["sin_clasificar", "sin_clasificar"]


def test_agrupar_sin_clasificar_false_conserva_la_grafia_en_la_etiqueta():
    """Dos grafías sin clasificar no pueden compartir PAIS_FINAL: aguas abajo
    es el campo categórico que veta la fusión."""
    r = canonizar_pais(
        pd.Series(["NO DEFINIDO", "VARIOS", "ALEMANIA"]), agrupar_sin_clasificar=False
    )
    assert r.tabla.PAIS_FINAL.tolist()[:2] == [
        "SIN CLASIFICAR: NO DEFINIDO",
        "SIN CLASIFICAR: VARIOS",
    ]
    assert r.tabla.PAIS_ISO3.tolist()[:2] == ["ZZZ", "ZZZ"]
    assert r.tabla.PAIS_METODO.tolist()[:2] == ["sin_clasificar", "sin_clasificar"]
    assert r.n_canonicos == 1, "las sin clasificar no cuentan como canónicos"


# ── Las 19 grafías de la primera corrida real sobre snowflake_v2 (v0.22.4) ──
#
# Medidas, no supuestas: son exactamente las que `PAIS_ESTANDAR` trajo y el
# catálogo 0.22.3 no reconocía. 18 son países o territorios ISO 3166-1; la
# sugerencia automática acertaba en 6 y erraba en 12 (`IRAN → IRLANDA`,
# `AFGANISTAN → ALBANIA`, `LIECHTENSTEIN → BELICE`): por eso es ayuda y no regla.

GRAFIAS_SNOWFLAKE_V2 = [
    ("ISLAS VIRGENES ESTADOUNIDENSES", "VIR"),
    ("BOSNIA", "BIH"),
    ("ZIMBABUE", "ZWE"),
    ("IRAN", "IRN"),
    ("REPUBLICA DE MACEDONIA", "MKD"),
    ("FIYI", "FJI"),
    ("GUINEA-BISAU", "GNB"),
    ("AFGANISTAN", "AFG"),
    ("LIECHTENSTEIN", "LIE"),
    ("VANUATU", "VUT"),
    ("GIBRALTAR", "GIB"),
    ("ISLA NORFOLK", "NFK"),
    ("ISLAS SALOMON", "SLB"),
    ("SANTO TOME Y PRINCIPE", "STP"),
    ("CIUDAD DEL VATICANO", "VAT"),
    ("LESOTO", "LSO"),
    ("ISLAS MARIANAS DEL NORTE", "MNP"),
    ("SAMOA", "WSM"),
]


@pytest.mark.parametrize(("grafia", "iso"), GRAFIAS_SNOWFLAKE_V2)
def test_las_grafias_de_snowflake_v2_mapean(grafia, iso):
    r = canonizar_pais(pd.Series([grafia]))
    assert r.tabla.PAIS_ISO3.item() == iso, r.tabla.to_dict("records")
    assert r.tabla.PAIS_METODO.item() in ("catalogo_exacto", "catalogo_laxo")


def test_samoa_y_samoa_americana_siguen_siendo_distintas():
    r = canonizar_pais(pd.Series(["SAMOA", "SAMOA AMERICANA"]))
    assert r.tabla.PAIS_ISO3.tolist() == ["WSM", "ASM"]


def test_otros_no_es_pais_y_sigue_sin_clasificar():
    r = canonizar_pais(pd.Series(["OTROS"]))
    assert r.tabla.PAIS_METODO.item() == "sin_clasificar"


def test_el_catalogo_crecio_a_214_paises():
    assert len(CATALOGO_PAISES) == 214
    assert sum(1 + len(a) for _, _, a in CATALOGO_PAISES) == 378
