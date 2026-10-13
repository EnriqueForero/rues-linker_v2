"""Pruebas de ``flujo.vinculacion.lectura``: lectura de archivos, contrato de columnas e identificadores.

Los valores esperados NO salen de razonar sobre el código: se observaron ejecutando el motor original
(la celda ``%%writefile`` del notebook 10) con estas mismas entradas. Cada prueba dice qué regla ejerce.
Las empresas son inventadas y todos los archivos viven en ``tmp_path``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.flujo.vinculacion.config import Fuente
from record_linkage.flujo.vinculacion.lectura import (
    TRANSFORMACIONES,
    formatear_hs6,
    leer_fuente,
    leer_tabla,
    sha256_archivo,
)


def _escribir(ruta: Path, texto: str, codificacion: str = "utf-8") -> Path:
    ruta.write_bytes(texto.encode(codificacion))
    return ruta


def _filas(df: pd.DataFrame) -> list[list]:
    return df.values.tolist()


# ── leer_tabla: CSV / TXT / TSV ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("sep", "extension"),
    [(";", "csv"), (",", "csv"), ("\t", "tsv"), ("\t", "txt"), (";", "TXT")],
)
def test_csv_txt_tsv_detectan_el_separador_y_leen_todo_como_texto(tmp_path, sep, extension):
    # Regla: el separador es el que más aparece en el comienzo del archivo; la extensión no distingue mayúsculas.
    ruta = _escribir(
        tmp_path / f"base.{extension}",
        f"Empresa{sep}País{sep}Valor\nZelvora SA{sep}España{sep}12\n",
    )
    df = leer_tabla(ruta, 0)
    assert df.columns.tolist() == ["Empresa", "País", "Valor"]
    assert _filas(df) == [["Zelvora SA", "España", "12"]]


def test_el_tabulador_solo_gana_si_supera_a_las_comas_y_a_los_puntos_y_coma(tmp_path):
    # Regla: 2 tabuladores > 1 coma y > 1 punto y coma (aunque haya ``,`` y ``;`` dentro de los valores).
    ruta = _escribir(tmp_path / "t.tsv", "Empresa\tPais\nZelvora SA, S.L.\tEspaña; Francia\n")
    assert _filas(leer_tabla(ruta, 0)) == [["Zelvora SA, S.L.", "España; Francia"]]


@pytest.mark.parametrize(
    ("contenido", "encabezados", "primera_fila"),
    [
        ("A,B,C\tD\n1,2,3\t4\n", ["A", "B", "C\tD"], ["1", "2", "3\t4"]),  # 1 tabulador < 2 comas
        (
            "A;B;C\tD\n1;2;3\t4\n",
            ["A", "B", "C\tD"],
            ["1", "2", "3\t4"],
        ),  # 1 tabulador < 2 ';' (sep ';')
    ],
)
def test_un_tabulador_suelto_no_gana_si_hay_mas_comas_o_mas_puntos_y_coma(
    tmp_path, contenido, encabezados, primera_fila
):
    # Regla: el tabulador necesita superar a las comas Y a los puntos y coma (no a uno solo de los dos).
    ruta = _escribir(tmp_path / "mixto.csv", contenido)
    df = leer_tabla(ruta, 0)
    assert df.columns.tolist() == encabezados
    assert df.iloc[0].tolist() == primera_fila


def test_en_empate_o_sin_separador_se_usa_la_coma(tmp_path):
    # Regla: si ``;`` no supera a ``,`` el separador es la coma (una sola columna, o empate 2 a 2).
    una_columna = _escribir(tmp_path / "uno.csv", "Empresa\nZelvora SA\n")
    assert leer_tabla(una_columna, 0).columns.tolist() == ["Empresa"]
    empate = _escribir(tmp_path / "empate.csv", "A;B,C\n1;2,3\n")
    df = leer_tabla(empate, 0)
    assert df.columns.tolist() == ["A;B", "C"]
    assert _filas(df) == [["1;2", "3"]]


def test_csv_utf8_con_bom_y_csv_cp1252_se_leen_igual(tmp_path):
    # Regla: utf-8-sig quita el BOM del primer encabezado; si el archivo no es UTF-8 se relee en cp1252.
    texto = "Empresa;País\nÑandú SA;España\n"
    con_bom = _escribir(tmp_path / "bom.csv", "﻿" + texto)
    en_cp1252 = _escribir(tmp_path / "cp.csv", texto, codificacion="cp1252")
    for ruta in (con_bom, en_cp1252):
        df = leer_tabla(ruta, 0)
        assert df.columns.tolist() == ["Empresa", "País"]
        assert _filas(df) == [["Ñandú SA", "España"]]


def test_los_textos_na_none_y_null_llegan_como_texto_no_como_ausentes(tmp_path):
    # Arreglo A2 (F3.1b): pandas los convertía en NaN al leer (¡'NA' es el ISO2 de Namibia!) y la fila
    # perdía su país sin aviso. Ahora solo la celda vacía es ausente.
    ruta = _escribir(
        tmp_path / "a.csv",
        "Empresa;País\nZelvora SA;NA\nKramento;N/A\nOrbelia;None\nTavorín;null\nQuelmar;Namibia\n",
    )
    df = leer_fuente(
        Fuente(clave="EXPORTADORES", archivo=str(ruta), col_nombre="Empresa", col_pais="País")
    )
    assert df["País"].tolist() == ["NA", "N/A", "None", "null", "Namibia"]


def test_csv_conserva_los_ceros_a_la_izquierda(tmp_path):
    # Regla: ``dtype=object`` en el CSV: un código con ceros no se convierte en número.
    ruta = _escribir(tmp_path / "ids.csv", "Id;Empresa\n000123;Zelvora SA\n")
    assert _filas(leer_tabla(ruta, 0)) == [["000123", "Zelvora SA"]]


# ── leer_tabla: Excel y Parquet ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("extension", ["xlsx", "xlsm"])
def test_excel_lee_la_hoja_por_nombre_y_por_posicion(tmp_path, extension):
    # Regla: ``hoja`` es el nombre o la posición; las celdas llegan como object (números sin convertir a texto).
    ruta = tmp_path / f"libro.{extension}"
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        pd.DataFrame({"Empresa": ["Zelvora SA"], "Valor": [12]}).to_excel(
            w, sheet_name="Hoja A", index=False
        )
        pd.DataFrame({"Empresa": ["Kramento SL"]}).to_excel(w, sheet_name="Hoja B", index=False)
    assert _filas(leer_tabla(ruta, "Hoja A")) == [["Zelvora SA", 12]]
    assert _filas(leer_tabla(ruta, "Hoja B")) == [["Kramento SL"]]
    assert _filas(leer_tabla(ruta, 1)) == [["Kramento SL"]]
    assert _filas(leer_tabla(ruta)) == [["Zelvora SA", 12]]  # por defecto, la primera hoja


def test_parquet_se_lee_tal_cual_sin_convertir_a_object(tmp_path):
    # Regla: Parquet conserva sus tipos (a diferencia de Excel y CSV, que se leen como object/texto).
    ruta = tmp_path / "base.parquet"
    pd.DataFrame({"Empresa": ["Zelvora SA"], "Valor": [12]}).to_parquet(ruta)
    df = leer_tabla(ruta, 0)
    assert _filas(df) == [["Zelvora SA", 12]]
    assert pd.api.types.is_integer_dtype(df["Valor"])


# ── leer_tabla: errores ──────────────────────────────────────────────────────────────────────────
def test_formato_no_soportado_nombra_la_extension(tmp_path):
    ruta = _escribir(tmp_path / "datos.json", "{}")
    with pytest.raises(ValueError, match=r"Formato no soportado: \.json"):
        leer_tabla(ruta, 0)


def test_archivo_inexistente_dice_que_paso_y_que_hacer(tmp_path):
    ruta = tmp_path / "nada.csv"
    with pytest.raises(FileNotFoundError) as error:
        leer_tabla(ruta, 0)
    mensaje = str(error.value)
    assert mensaje.startswith(f"Qué pasó: no existe {ruta}.")
    assert "Qué hacer: corrija la ruta en la configuración." in mensaje


# ── formatear_hs6 ────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (1905.9, "190590"),  # Excel guarda 1905.90 como 1905.9: se completa el cero de la derecha
        (901.11, "090111"),  # y se recupera el cero de la izquierda
        (1704.9, "170490"),
        (1806.32, "180632"),
        ("0901.11", "090111"),  # texto con punto decimal
        (" 1905.9 ", "190590"),  # se recortan espacios
        ("1905,9", "190590"),  # coma decimal
        ("190590", "190590"),  # ya de 6 dígitos: se deja como está
        ("190590.0", "190590"),
        (190590, "190590"),  # >= 10000: se toma como el entero de 5-6 dígitos
        (90111, "090111"),
        (90111.0, "090111"),
        (10000, "010000"),
        (9999.99, "999999"),  # justo debajo de 10000 sigue la regla del decimal
        (1905, "190500"),  # entero de 4 dígitos: partida de 4 con ceros a la derecha
        (5, "000500"),
        (1905.999, "190600"),  # se redondea a 2 decimales
        (12345678, "12345678"),  # más de 6 dígitos: no se recorta
        ("", ""),  # vacíos
        ("   ", ""),
        (None, ""),
        (float("nan"), ""),
        ("abc", "abc"),  # lo que no es un número queda como llegó
        ("19059a", "19059a"),
        ("19.05.90", "19.05.90"),
    ],
)
def test_formatear_hs6(entrada, esperado):
    assert formatear_hs6(entrada) == esperado


def test_transformaciones_disponibles():
    assert sorted(TRANSFORMACIONES) == ["hs6"]
    assert TRANSFORMACIONES["hs6"] is formatear_hs6


# ── leer_fuente: columnas de control y contrato ──────────────────────────────────────────────────
def test_leer_fuente_pone_fila_excel_id_registro_y_fuente_al_frente(tmp_path):
    # Regla: FILA_EXCEL = posición + 2 (el encabezado es la fila 1); ID sin col_id = <prefijo>-F<fila:04d>;
    # el prefijo por omisión son las 3 primeras letras de la clave.
    ruta = _escribir(
        tmp_path / "a.csv",
        "Empresa;País;HS6\nZelvora SA;España;1905.9\nKramento SL;Francia;901.11\n",
    )
    f = Fuente(
        clave="EXPORTADORES",
        archivo=str(ruta),
        col_nombre="Empresa",
        col_pais="País",
        cols_contexto=("HS6",),
        transformaciones=(("HS6", "hs6"),),
    )
    df = leer_fuente(f)
    assert df.columns.tolist() == ["FILA_EXCEL", "ID_REGISTRO", "FUENTE", "Empresa", "País", "HS6"]
    assert df["FILA_EXCEL"].tolist() == [2, 3]
    assert df["ID_REGISTRO"].tolist() == ["EXP-F0002", "EXP-F0003"]
    assert df["FUENTE"].tolist() == ["EXPORTADORES", "EXPORTADORES"]
    assert df["HS6"].tolist() == ["190590", "090111"]  # la transformación hs6 se aplicó


def test_sin_col_id_el_prefijo_del_id_es_el_declarado_y_la_fila_no_se_trunca(tmp_path):
    # Regla: prefijo_id explícito; el formato ``F{fila:04d}`` rellena a 4 dígitos y no recorta los de 5.
    filas = "\n".join(f"Empresa {n}" for n in range(10_000))
    ruta = _escribir(tmp_path / "grande.csv", "Empresa\n" + filas + "\n")
    df = leer_fuente(
        Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa", prefijo_id="ZEL")
    )
    assert df["ID_REGISTRO"].iloc[0] == "ZEL-F0002"
    assert df["ID_REGISTRO"].iloc[-1] == "ZEL-F10001"
    assert df["FILA_EXCEL"].iloc[-1] == 10_001


def test_filas_vacias_se_descartan_y_fila_excel_conserva_la_fila_real(tmp_path):
    # Regla: FILA_EXCEL se numera ANTES de descartar; «vacía» se juzga solo en las columnas DECLARADAS
    # (la fila 3 trae una nota en una columna no declarada y se descarta igual).
    ruta = tmp_path / "libro.xlsx"
    pd.DataFrame(
        {
            "Empresa": ["Zelvora SA", None, "Kramento SL", None, "Orbelia"],
            "País": ["España", None, "Francia", None, None],
            "Nota": [None, "solo una nota", None, None, "x"],
        }
    ).to_excel(ruta, index=False)
    f = Fuente(clave="EXPORTADORES", archivo=str(ruta), col_nombre="Empresa", col_pais="País")
    df = leer_fuente(f)
    assert df["FILA_EXCEL"].tolist() == [2, 4, 6]
    assert df["ID_REGISTRO"].tolist() == ["EXP-F0002", "EXP-F0004", "EXP-F0006"]
    assert df["Empresa"].tolist() == ["Zelvora SA", "Kramento SL", "Orbelia"]
    assert df.index.tolist() == [0, 1, 2]  # el índice se rehace


def test_col_id_se_recorta_y_pasa_a_id_registro(tmp_path):
    # Regla: con col_id el ID_REGISTRO es ese identificador sin espacios en los extremos.
    ruta = _escribir(tmp_path / "o.csv", "Id;Empresa\n  ESB0001 ;Zelvora SA\nESB0002;Kramento SL\n")
    f = Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa", col_id="Id")
    df = leer_fuente(f)
    assert df["ID_REGISTRO"].tolist() == ["ESB0001", "ESB0002"]
    assert df["Id"].tolist() == ["  ESB0001 ", "ESB0002"]  # la columna original no se toca


def test_col_id_con_vacios_falla_y_lista_las_filas(tmp_path):
    # Regla: un id vacío (o solo espacios) detiene la lectura; el mensaje cuenta las filas y da las de Excel.
    ruta = _escribir(
        tmp_path / "o.csv", "Id;Empresa\nA1;Zelvora SA\n ;Kramento SL\nA3;Orbelia\n;Tavorín\n"
    )
    f = Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa", col_id="Id")
    with pytest.raises(ValueError) as error:
        leer_fuente(f)
    assert str(error.value) == (
        "Qué pasó: 2 filas de ORBIS sin 'Id' (filas [3, 5]).\n"
        "Qué hacer: suelen ser filas de continuación de campos multivalor; exporte una fila por empresa."
    )


def test_col_id_con_vacios_lista_como_maximo_15_filas(tmp_path):
    filas = "\n".join(f";Empresa {n}" for n in range(20))
    ruta = _escribir(tmp_path / "o.csv", "Id;Empresa\n" + filas + "\n")
    f = Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa", col_id="Id")
    with pytest.raises(ValueError) as error:
        leer_fuente(f)
    primera_linea = str(error.value).splitlines()[0]
    assert primera_linea.startswith("Qué pasó: 20 filas de ORBIS sin 'Id'")
    assert primera_linea.endswith("(filas [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]).")


def test_col_id_repetido_falla_con_ejemplos_y_remedio(tmp_path):
    # Regla: el identificador nativo debe ser único; el mensaje da hasta 3 repetidos y el remedio.
    ruta = _escribir(
        tmp_path / "o.csv",
        "Id;Empresa\nA1;Zelvora SA\nA2;Kramento SL\nA1;Orbelia\nA2;Tavorín\nA2;Quelmar\nA3;Vantiko\n",
    )
    f = Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa", col_id="Id")
    with pytest.raises(ValueError) as error:
        leer_fuente(f)
    assert str(error.value) == (
        "Qué pasó: 'Id' no es único en ORBIS (p. ej. ['A1', 'A2', 'A2']).\n"
        "Qué hacer: declare col_id=None para usar la fila como identificador."
    )


def test_faltan_columnas_declaradas_lista_las_que_faltan_y_las_disponibles(tmp_path):
    # Regla: toda columna declarada en la Fuente debe estar en el archivo; KeyError con qué pasó / por qué / qué hacer.
    ruta = _escribir(tmp_path / "o.csv", "Id;Empresa\nA1;Zelvora SA\n")
    f = Fuente(
        clave="ORBIS",
        archivo=str(ruta),
        hoja=2,
        col_nombre="Empresa",
        col_id="Id",
        col_pais="Country",
        col_web="Web",
    )
    with pytest.raises(KeyError) as error:
        leer_fuente(f)
    assert error.value.args[0] == (
        "Qué pasó: a la fuente ORBIS (hoja 2) le faltan las columnas ['Country', 'Web'].\n"
        "Por qué importa: son las que declara su Fuente.\n"
        "Qué hacer: corrija la configuración. Columnas disponibles: ['Id', 'Empresa']"
    )


def test_transformacion_desconocida_lista_las_disponibles(tmp_path):
    ruta = _escribir(tmp_path / "o.csv", "Empresa\nZelvora SA\n")
    f = Fuente(
        clave="ORBIS",
        archivo=str(ruta),
        col_nombre="Empresa",
        transformaciones=(("Empresa", "zzz"),),
    )
    with pytest.raises(KeyError) as error:
        leer_fuente(f)
    assert error.value.args[0] == "Transformación desconocida 'zzz'. Disponibles: ['hs6']"


def test_transformacion_sobre_una_columna_inexistente_explica_que_hacer(tmp_path):
    # Arreglo A6 (F3.1b): antes era el KeyError('NoExiste') pelado de pandas.
    ruta = _escribir(tmp_path / "o.csv", "Empresa\nZelvora SA\n")
    f = Fuente(
        clave="ORBIS",
        archivo=str(ruta),
        col_nombre="Empresa",
        transformaciones=(("NoExiste", "hs6"),),
    )
    with pytest.raises(KeyError, match=r"(?s)Qué pasó.*'NoExiste'.*Qué hacer"):
        leer_fuente(f)


def test_los_encabezados_se_recortan_y_las_columnas_no_declaradas_se_conservan(tmp_path):
    # Regla: ``str(c).strip()`` en los encabezados; lo que la Fuente no declara viaja intacto.
    ruta = _escribir(tmp_path / "o.csv", " Empresa ;País;Extra\nZelvora SA;España;1\n")
    df = leer_fuente(Fuente(clave="ORBIS", archivo=str(ruta), col_nombre="Empresa"))
    assert df.columns.tolist() == [
        "FILA_EXCEL",
        "ID_REGISTRO",
        "FUENTE",
        "Empresa",
        "País",
        "Extra",
    ]
    assert _filas(df) == [[2, "ORB-F0002", "ORBIS", "Zelvora SA", "España", "1"]]


def test_fuente_sin_filas_devuelve_la_tabla_vacia_con_sus_columnas(tmp_path):
    ruta = _escribir(tmp_path / "vacio.csv", "Empresa;País\n")
    f = Fuente(clave="EXPORTADORES", archivo=str(ruta), col_nombre="Empresa", col_pais="País")
    df = leer_fuente(f)
    assert len(df) == 0
    assert df.columns.tolist() == ["FILA_EXCEL", "ID_REGISTRO", "FUENTE", "Empresa", "País"]


def test_en_un_csv_las_lineas_en_blanco_no_corren_fila_excel(tmp_path):
    # Arreglo A5 (F3.1b): read_csv omitía las líneas en blanco y FILA_EXCEL dejaba de ser la línea real del
    # archivo: las líneas reales son 2, 4 y 6; el motor original daba 2, 3 y 5.
    ruta = _escribir(
        tmp_path / "a.csv", "Empresa;País\nZelvora SA;España\n\nKramento SL;Francia\n;\nOrbelia;\n"
    )
    f = Fuente(clave="EXPORTADORES", archivo=str(ruta), col_nombre="Empresa", col_pais="País")
    assert leer_fuente(f)["FILA_EXCEL"].tolist() == [2, 4, 6]


# ── sha256_archivo ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("contenido", "huella"),
    [
        (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
    ],
)
def test_sha256_archivo_de_un_contenido_conocido(tmp_path, contenido, huella):
    # Regla: SHA-256 hexadecimal; acepta ruta como str o como Path.
    ruta = tmp_path / "archivo.bin"
    ruta.write_bytes(contenido)
    assert sha256_archivo(ruta) == huella
    assert sha256_archivo(str(ruta)) == huella


def test_sha256_archivo_lee_por_bloques_y_da_lo_mismo_que_hashlib(tmp_path):
    # Regla: el archivo se lee de a 1 MiB; 2,5 MiB obligan a tres bloques y el resultado no cambia.
    contenido = bytes(range(256)) * (10 * 1024)  # 2,5 MiB
    ruta = tmp_path / "grande.bin"
    ruta.write_bytes(contenido)
    assert len(contenido) > 2 * (1 << 20)
    assert sha256_archivo(ruta) == hashlib.sha256(contenido).hexdigest()


def test_sha256_archivo_acepta_la_ruta_con_barra_final_como_leer_tabla(tmp_path):
    # Cambio declarado (revisión g1, H1): el original abría la ruta con open() y ejecutar() reventaba AL FINAL con
    # NotADirectoryError si la ruta de una fuente traía barra final, aunque leer_tabla (que usa Path) ya la había
    # leído bien. Ahora la huella se calcula con la misma ruta normalizada que la lectura.
    ruta = tmp_path / "ancla.csv"
    ruta.write_bytes(b"abc")
    assert sha256_archivo(f"{ruta}/") == sha256_archivo(ruta)
