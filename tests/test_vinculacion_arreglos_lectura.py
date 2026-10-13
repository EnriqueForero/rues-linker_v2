"""Arreglos declarados de F3.1b en la lectura de las fuentes y en la normalización de sus columnas.

Cada prueba fija el comportamiento NUEVO de un hallazgo de ``docs/vinculacion/HALLAZGOS_MOTOR_ORIGINAL.md``
(el id va en el nombre). Los cambios que alteran las tablas de los escenarios inventados se declaran además en
``tests/lineas_base_vinculacion_cambios.json``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.flujo.vinculacion import lectura
from record_linkage.flujo.vinculacion.config import ConfigVinculacion, Fuente
from record_linkage.flujo.vinculacion.lectura import (
    formatear_hs6,
    leer_fuente,
    leer_tabla,
)
from record_linkage.flujo.vinculacion.normalizar import Normalizador, dominio_web, preparar_fuente


def _csv(tmp_path: Path, contenido: str, nombre: str = "base.csv") -> Path:
    ruta = tmp_path / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def _config() -> ConfigVinculacion:
    return ConfigVinculacion(
        ancla=Fuente(clave="ANCLA", archivo="no_se_lee.xlsx", col_nombre="Empresa", col_pais="Pais")
    )


def _fuente_nombre_y_pais() -> Fuente:
    return Fuente(clave="ANCLA", archivo="no_se_lee.xlsx", col_nombre="Empresa", col_pais="Pais")


def _entrada(**columnas) -> pd.DataFrame:
    """Lo que entrega ``leer_fuente``: ``FILA_EXCEL``, ``ID_REGISTRO`` y las columnas declaradas, como object."""
    n = len(next(iter(columnas.values())))
    df = pd.DataFrame(columnas, dtype=object)
    df.insert(0, "FILA_EXCEL", np.arange(2, n + 2))
    df.insert(1, "ID_REGISTRO", [f"ANC-F{k:04d}" for k in range(2, n + 2)])
    return df


def _fuente_csv(ruta: Path, **cambios) -> Fuente:
    parametros = {
        "clave": "BASE",
        "archivo": str(ruta),
        "col_nombre": "Empresa",
        "col_pais": "Pais",
        "prefijo_id": "BAS",
    }
    return Fuente(**{**parametros, **cambios})


# ── A2 · «NA» (Namibia) no se pierde en una columna de país; los demás marcadores de ausente siguen igual ────────
PLACEHOLDERS_DE_AUSENTE = ["N/A", "None", "null", "NULL", "NaN", "n/a", "#N/A"]


def test_a2_csv_conserva_el_texto_na_y_sigue_leyendo_como_ausentes_los_otros_marcadores(tmp_path):
    # Antes: la celda de país 'NA' llegaba vacía sin aviso (pandas la convierte en NaN por defecto).
    filas = "".join(f"E{k};{t}\n" for k, t in enumerate(["NA", *PLACEHOLDERS_DE_AUSENTE]))
    tabla = lectura._leer_csv(_csv(tmp_path, "Empresa;Pais\n" + filas + "VACIA;\n"))
    assert tabla["Pais"].iloc[0] == "NA"
    assert (
        tabla["Pais"].iloc[1:].isna().all()
    )  # 'N/A', 'None', 'null'… y la celda vacía: ausentes, como antes


def test_a2_excel_conserva_el_texto_na_y_sigue_leyendo_como_ausentes_los_otros_marcadores(tmp_path):
    ruta = tmp_path / "base.xlsx"
    pd.DataFrame(
        {"Empresa": ["A", "B", "C", "D"], "Pais": ["NA", "N/A", "None", None]}, dtype=object
    ).to_excel(ruta, index=False)
    tabla = leer_tabla(ruta)
    assert tabla["Pais"].iloc[0] == "NA"
    assert tabla["Pais"].iloc[1:].isna().all()


def test_a2_leer_fuente_conserva_na_solo_en_las_columnas_de_pais(tmp_path):
    ruta = _csv(
        tmp_path,
        "Empresa;Pais;HQ;NIF;Ciudad\nKronos;NA;NA;NA;NA\nValdoria;N/A;N/A;N/A;N/A\n",
    )
    f = _fuente_csv(
        ruta,
        cols_pais_extra=("HQ",),
        col_id_comparable="NIF",
        col_ciudad="Ciudad",
    )
    df = leer_fuente(f)
    assert df["Pais"].iloc[0] == df["HQ"].iloc[0] == "NA"  # Namibia
    # Fuera de las columnas de país 'NA' es «no disponible», como lo leía pandas: sin esto dos empresas sin NIF
    # «compartirían identificador» (id_comparable('NA') = 'NA').
    assert pd.isna(df["NIF"].iloc[0]) and pd.isna(df["Ciudad"].iloc[0])
    assert df[["Pais", "HQ", "NIF", "Ciudad"]].iloc[1].isna().all()


def test_a2_un_parquet_no_pasa_por_la_conversion_de_pandas(tmp_path):
    ruta = tmp_path / "base.parquet"
    pd.DataFrame({"Empresa": ["Kronos"], "Pais": ["NA"], "NIF": ["NA"]}).to_parquet(ruta)
    f = _fuente_csv(ruta, col_id_comparable="NIF")
    df = leer_fuente(f)
    assert (
        df["NIF"].iloc[0] == "NA"
    )  # el Parquet lo trae como texto y así se queda (el original tampoco lo tocaba)


def test_a2_un_na_en_la_columna_de_pais_sin_alias_se_rechaza_con_un_mensaje_que_dice_que_hacer():
    # Antes: el país quedaba vacío sin aviso. Ahora el catálogo lo rechaza y el mensaje ofrece las dos salidas.
    df = _entrada(Empresa=["Kronos"], Pais=["NA"])
    with pytest.raises(
        ValueError, match=r"(?s)Qué pasó.*Qué hacer.*ALIAS_PAISES.*'NA' significa «sin dato»"
    ) as e:
        preparar_fuente(df, _fuente_nombre_y_pais(), Normalizador(_config()))
    assert "SIN PAIS" not in str(e.value)


# ── A1 · el separador se decide leyendo solo el comienzo del archivo ─────────────────────────────────
def test_a1_el_separador_se_detecta_sin_leer_el_archivo_entero(tmp_path, monkeypatch):
    ruta = _csv(tmp_path, "Empresa\tPais\n" + "Kronos\tESPAÑA\n" * 2000)
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("leyó el archivo entero"))
    assert list(lectura._leer_csv(ruta).columns) == ["Empresa", "Pais"]


# ── A5 · FILA_EXCEL de un CSV con líneas en blanco es la línea real del archivo ─────────────────────
def test_a5_las_lineas_en_blanco_de_un_csv_no_descuadran_la_numeracion(tmp_path):
    # Antes: read_csv omitía la línea 3 y «Valdoria» (línea 4) quedaba como fila 3.
    ruta = _csv(tmp_path, "Empresa;Pais\nKronos;ESPAÑA\n\nValdoria;FRANCIA\n")
    df = leer_fuente(_fuente_csv(ruta))
    assert df["FILA_EXCEL"].tolist() == [2, 4]
    assert df["ID_REGISTRO"].tolist() == ["BAS-F0002", "BAS-F0004"]
    assert df["Empresa"].tolist() == ["Kronos", "Valdoria"]


def test_a5_las_lineas_en_blanco_antes_del_encabezado_se_ignoran(tmp_path):
    ruta = _csv(tmp_path, "\n\nEmpresa;Pais\nKronos;ESPAÑA\n")
    df = leer_fuente(_fuente_csv(ruta))
    assert df["Empresa"].tolist() == ["Kronos"]
    assert df["FILA_EXCEL"].tolist() == [2]


def test_a5_un_csv_sin_lineas_en_blanco_numera_igual_que_antes(tmp_path):
    ruta = _csv(tmp_path, "Empresa;Pais\nKronos;ESPAÑA\nValdoria;FRANCIA\n")
    assert leer_fuente(_fuente_csv(ruta))["FILA_EXCEL"].tolist() == [2, 3]


# ── A3 · un .xls sin xlrd falla con un mensaje que dice qué hacer ───────────────────────────────────
def test_a3_un_xls_sin_xlrd_explica_que_hacer(tmp_path, monkeypatch):
    ruta = tmp_path / "vieja.xls"
    ruta.write_bytes(b"no importa")

    def sin_xlrd(*_a, **_k):
        raise ImportError("Missing optional dependency 'xlrd'")

    monkeypatch.setattr(pd, "read_excel", sin_xlrd)
    with pytest.raises(ImportError, match=r"(?s)Qué pasó.*\.xls.*Qué hacer.*\.xlsx"):
        leer_tabla(ruta)


def test_a3_un_xlsx_sin_openpyxl_no_se_disfraza_de_xls(tmp_path, monkeypatch):
    ruta = tmp_path / "nueva.xlsx"
    ruta.write_bytes(b"no importa")

    def sin_motor(*_a, **_k):
        raise ImportError("Missing optional dependency 'openpyxl'")

    monkeypatch.setattr(pd, "read_excel", sin_motor)
    with pytest.raises(ImportError, match="openpyxl"):
        leer_tabla(ruta)


# ── A4 · la partida arancelaria no inventa valores con nan, inf, NA o negativos ─────────────────────
@pytest.mark.parametrize(
    "valor", [None, np.nan, pd.NA, pd.NaT, "nan", "NaN", "inf", "-inf", "Infinity", "", "  "]
)
def test_a4_hs6_trata_como_vacio_lo_que_no_es_un_numero_finito(valor):
    # Antes: 'nan' → '000nan', 'inf' → OverflowError, pd.NA → '<NA>'.
    assert formatear_hs6(valor) == ""


def test_a4_hs6_un_negativo_queda_como_llego():
    # Antes: '-5' → '-00500'; una partida negativa no existe, no se «completa».
    assert formatear_hs6("-5") == "-5"
    assert formatear_hs6(-5.0) == "-5.0"


def test_a4_hs6_no_cambia_los_casos_validos():
    assert formatear_hs6(1905.9) == "190590"
    assert formatear_hs6("901.11") == "090111"
    assert formatear_hs6("190590") == "190590"
    assert formatear_hs6(190590) == "190590"


# ── A6 · la columna de una transformación debe existir ──────────────────────────────────────────────
def test_a6_con_la_columna_presente_la_transformacion_se_aplica(tmp_path):
    ruta = _csv(tmp_path, "Empresa;Pais;Partida\nKronos;ESPAÑA;1905.9\n")
    f = _fuente_csv(ruta, cols_contexto=("Partida",), transformaciones=(("Partida", "hs6"),))
    assert leer_fuente(f)["Partida"].tolist() == ["190590"]


# ── g1-O4 · las columnas reservadas del motor no pueden venir en la base ────────────────────────────
@pytest.mark.parametrize("reservada", ["FUENTE", "ID_REGISTRO", "FILA_EXCEL"])
def test_g1_o4_una_columna_reservada_se_rechaza_con_un_mensaje_claro(tmp_path, reservada):
    # Antes: ValueError «cannot insert FUENTE, already exists», sin decir qué hacer.
    ruta = _csv(tmp_path, f"Empresa;Pais;{reservada}\nKronos;ESPAÑA;x\n")
    with pytest.raises(ValueError, match=r"(?s)Qué pasó.*reservad.*Qué hacer") as e:
        leer_fuente(_fuente_csv(ruta))
    assert f"['{reservada}']" in str(e.value)


# ── A12 · una fuente sin filas es una tabla vacía, no un error ──────────────────────────────────────
def test_a12_leer_fuente_de_un_csv_solo_con_encabezado_da_una_tabla_vacia(tmp_path):
    ruta = _csv(tmp_path, "Empresa;Pais\n")
    df = leer_fuente(_fuente_csv(ruta))
    assert len(df) == 0
    assert list(df.columns[:3]) == ["FILA_EXCEL", "ID_REGISTRO", "FUENTE"]


# ── A11 · el marcador «SIN PAIS» no aparece como si lo hubiera escrito el usuario ───────────────────
def test_a11_con_todas_las_celdas_de_pais_vacias_no_hay_error_y_el_pais_queda_vacio():
    salida = preparar_fuente(
        _entrada(Empresa=["A"], Pais=[""]), _fuente_nombre_y_pais(), Normalizador(_config())
    )
    assert salida["PAIS_ISO3"].tolist() == [""]
    assert salida["PAIS_CANONICO"].tolist() == [""]


# ── A8 · los segundos niveles públicos (gob.es, mil.co, or.jp…) no se confunden con el dominio del sitio ──
@pytest.mark.parametrize(
    ("web", "esperado"),
    [
        ("https://tienda.zelvora.gob.es/inicio", "zelvora.gob.es"),
        ("www.kramento.gob.mx", "kramento.gob.mx"),
        ("portal.orbelia.mil.co", "orbelia.mil.co"),
        ("http://sede.tavorin.or.jp", "tavorin.or.jp"),
        ("shop.quelmar.ne.jp", "quelmar.ne.jp"),
        ("www.valdoria.go.kr", "valdoria.go.kr"),
        # lo de siempre no cambia:
        ("tienda.zelvora.co.uk", "zelvora.co.uk"),
        ("www.kramento.es", "kramento.es"),
        ("a.b.c.zelvora.com", "zelvora.com"),
    ],
)
def test_a8_el_dominio_registrable_conserva_la_etiqueta_propia_bajo_un_segundo_nivel(web, esperado):
    # Antes: 'tienda.zelvora.gob.es' → 'gob.es', y dos sitios distintos bajo gob.xx daban el mismo dominio
    # (un falso vínculo por web).
    assert dominio_web(web) == esperado


def test_a8_dos_sitios_distintos_bajo_el_mismo_segundo_nivel_no_comparten_dominio():
    assert dominio_web("zelvora.gob.es") != dominio_web("kramento.gob.es")


# ── A10 · g1-O6 · separadores iniciales, usuario:clave@ e IP en una celda de sitio web ───────────────
@pytest.mark.parametrize(
    ("web", "esperado"),
    [
        (";zelvora.es", "zelvora.es"),
        (" , | zelvora.es; kramento.es", "zelvora.es"),
        ("https://usuario:clave@zelvora.es/ruta", "zelvora.es"),
        ("usuario@zelvora.es", "zelvora.es"),
        ("192.168.1.1", ""),
        ("http://10.0.0.5:8080/admin", ""),
        ("zelvora.es:8080", "zelvora.es"),
    ],
)
def test_a10_el_sitio_web_ignora_separadores_iniciales_usuario_e_ip(web, esperado):
    # Antes: ';zelvora.es' → '', 'https://usuario:clave@zelvora.es' → '' y '192.168.1.1' → '1.1'.
    assert dominio_web(web) == esperado
