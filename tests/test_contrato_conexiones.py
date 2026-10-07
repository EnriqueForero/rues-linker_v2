"""``conexiones`` es una tabla del contrato (F2.10).

Qué congela: la tabla ``conexiones`` (una fila por registro que comparte grupo
con otro; la deja ``deduplicate_unified`` junto a ``correlativa.parquet``) está
declarada en ``contrato.py`` como las demás: columnas fijas en ESE orden, tipos
``pyarrow``, esquema con el metadato ``contrato`` y filas en el diccionario. Sus
columnas compartidas con la correlativa y el golden son LOS MISMOS objetos
(una regla escrita una vez), no copias con otro significado.
"""

from __future__ import annotations

import pandas as pd
import pyarrow as pa

from record_linkage import contrato


def test_conexiones_es_tabla_del_contrato() -> None:
    assert "conexiones" in contrato.TABLAS
    assert contrato.TABLAS["conexiones"] is contrato.CONEXIONES
    assert contrato.COLUMNAS_CONEXIONES == (
        "ID_GRUPO",
        "RECORD_COUNT",
        "ORIGINAL_INDEX",
        "SRC",
        "NIT_FINAL",
        "RAZON_SOCIAL_FINAL",
        "NAME_SIMILARITY_SCORE",
        "NIT_DISTANCE",
    )
    assert "COLUMNAS_CONEXIONES" in contrato.__all__
    assert "esquema_conexiones" in contrato.__all__


def test_conexiones_reutiliza_las_columnas_de_correlativa_y_golden() -> None:
    por_nombre_correl = {c.nombre: c for c in contrato.CORRELATIVA}
    por_nombre_golden = {c.nombre: c for c in contrato.GOLDEN}
    for col in contrato.CONEXIONES:
        if col.nombre == "RECORD_COUNT":
            assert col is por_nombre_golden["RECORD_COUNT"]
        elif col.nombre == "ID_GRUPO":
            # ID_GRUPO vive en las dos; la de la correlativa es la del registro.
            assert col is por_nombre_correl["ID_GRUPO"]
        else:
            assert col is por_nombre_correl[col.nombre], col.nombre


def test_esquema_conexiones() -> None:
    esq = contrato.esquema_conexiones()
    assert esq.names == list(contrato.COLUMNAS_CONEXIONES)
    assert esq.field("ID_GRUPO").type == pa.int64()
    assert esq.field("RECORD_COUNT").type == pa.int64()
    assert esq.field("ORIGINAL_INDEX").type == pa.int64()
    assert esq.field("NAME_SIMILARITY_SCORE").type == pa.float64()
    assert esq.field("NIT_DISTANCE").type == pa.int64()
    assert esq.metadata[b"contrato"].decode() == contrato.VERSION_CONTRATO


def test_diccionario_describe_conexiones_y_sus_columnas_extra() -> None:
    df = pd.DataFrame(
        {
            "ID_GRUPO": [0, 0],
            "RECORD_COUNT": [2, 2],
            "ORIGINAL_INDEX": [0, 1],
            "SRC": ["DEDUP_SOURCE", "DEDUP_SOURCE"],
            "NIT_FINAL": ["9001234568", "9001234568"],
            "RAZON_SOCIAL_FINAL": ["EMPRESA INVENTADA SAS", "EMPRESA INVENTADA SAS"],
            "NAME_SIMILARITY_SCORE": [1.0, 0.9],
            "NIT_DISTANCE": [0, 0],
            "NIT": ["900123456", "900123456"],
            "NIT_OK": ["9001234568", "9001234568"],
        }
    )
    dic = contrato.diccionario({"conexiones": df}, columnas_fuente=["NIT"])
    filas = dic[dic["tabla"] == "conexiones"]
    assert list(filas["columna"]) == list(df.columns)
    assert filas.loc[filas["columna"] == "NIT", "origen"].eq("fuente").all()
    assert filas.loc[filas["columna"] == "NIT_OK", "origen"].eq("motor").all()
    assert filas.loc[filas["columna"] == "SRC", "alias_es"].eq("FUENTE").all()
    assert (filas["significado"].str.len() > 0).all()
