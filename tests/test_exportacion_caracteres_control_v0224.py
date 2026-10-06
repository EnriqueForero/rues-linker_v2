"""Exportar no puede caerse por un byte de control en una razón social (v0.22.4).

Origen: la corrida real de importadores (355.681 filas) completó 14 minutos de
emparejamiento con las 10 invariantes en OK y cayó al escribir el XLSX:
``openpyxl.utils.exceptions.IllegalCharacterError: COMPAÃ\\x1aIA DE GALLETAS
POZUELO DCR SA``. Diez razones sociales de la base traían ``\\x1a`` —el
sustituto que deja un decodificador ante un byte inválido; mojibake de "Ñ"—
y openpyxl rechaza cualquier control fuera de tab/CR/LF. El Parquet ya estaba
escrito (checkpoint primero, por diseño), pero el entregable no. Y en la misma
corrida, ``METRICAS`` no cabía en Parquet porque su columna ``valor`` mezclaba
enteros y textos ("41.2%").
"""

from __future__ import annotations

import pandas as pd
import pytest

from record_linkage.exporters._spreadsheet import (
    CONTROL_CHARACTERS_RE,
    escape_spreadsheet_value,
    prepare_spreadsheet_data,
    strip_control_characters,
)
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores
from record_linkage.pipeline.result import PipelineResult

MOJIBAKE = "COMPAÃ\x1aIA DE GALLETAS POZUELO DCR SA"


def _tabla() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "RAZON_SOCIAL": [MOJIBAKE, "ACME\x0bTRADING", "\x1a=SUMA(A1)", "NORMAL S.A.", None],
            "FOB": [1.5, -2.0, 3.0, 4.0, 5.0],
            "PAIS": ["CRI", "USA", "USA", "PAN", "PAN"],
        }
    )


def test_el_conjunto_es_el_de_openpyxl() -> None:
    """Carácter por carácter: lo que quitamos es exactamente lo que openpyxl rechaza."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    for codigo in range(0x100):
        ch = chr(codigo)
        assert bool(CONTROL_CHARACTERS_RE.search(ch)) == bool(ILLEGAL_CHARACTERS_RE.search(ch)), (
            hex(codigo)
        )


def test_strip_quita_controles_y_conserva_lo_demas() -> None:
    assert strip_control_characters(MOJIBAKE) == "COMPAÃIA DE GALLETAS POZUELO DCR SA"
    assert strip_control_characters("ESPAÑA\tS.A.\n") == "ESPAÑA\tS.A.\n"  # tab y LF se admiten
    assert strip_control_characters(12.5) == 12.5
    assert strip_control_characters(None) is None
    assert escape_spreadsheet_value("\x1a=SUMA(A1)") == "'=SUMA(A1)"


def test_prepare_limpia_sin_mutar_y_conserva_numericos() -> None:
    original = _tabla()
    copia = original.copy(deep=True)
    listo = prepare_spreadsheet_data(original)
    pd.testing.assert_frame_equal(original, copia)  # la fuente no se toca
    assert listo["RAZON_SOCIAL"].tolist()[:4] == [
        "COMPAÃIA DE GALLETAS POZUELO DCR SA",
        "ACMETRADING",
        "'=SUMA(A1)",
        "NORMAL S.A.",
    ]
    assert pd.isna(listo["RAZON_SOCIAL"].iloc[4])
    assert listo["FOB"].dtype == original["FOB"].dtype
    assert listo["FOB"].tolist() == original["FOB"].tolist()  # el -2.0 sigue siendo número
    assert listo["PAIS"].tolist() == original["PAIS"].tolist()


def test_columna_limpia_no_se_copia() -> None:
    df = pd.DataFrame({"A": ["x", "y"], "N": [1, 2]})
    assert prepare_spreadsheet_data(df) is df


def test_contenedores_y_bytes_quedan_como_el_texto_de_to_csv() -> None:
    """F1.11 ronda 3: un dict/list/tuple/set/bytes/ndarray en una celda (struct,
    list o binary de parquet) no lo admite ninguna hoja de cálculo. Se vuelve el
    MISMO texto que ``to_csv`` escribe (``str``): csv.gz y xlsx dicen lo mismo y
    la salida CSV no cambia ni un byte. La fuente no se muta."""
    import numpy as np

    df = pd.DataFrame(
        {
            "O": [{"a": 1}, [1, 2], b"abc", (3, 4), {5}, np.array([6, 7]), None],
            "M": [1, "x", [b"=y"], None, 2.5, bytearray(b"z"), "f"],
            "N": [1, 2, 3, 4, 5, 6, 7],
            "S": pd.array(["a", None, "b", "c", "d", "e", "f"], dtype="string"),
        }
    )
    copia = df.copy(deep=True)
    out = prepare_spreadsheet_data(df)
    assert out.to_csv(index=False) == df.to_csv(index=False)
    assert out["O"].tolist()[:6] == ["{'a': 1}", "[1, 2]", "b'abc'", "(3, 4)", "{5}", "[6 7]"]
    assert pd.isna(out["O"].iloc[6])
    assert out["M"].tolist()[:3] == [1, "x", "[b'=y']"]
    assert out["M"].iloc[5] == "bytearray(b'z')"
    # El texto de un contenedor pasa DESPUÉS por la neutralización, como cualquier otro.
    sola = prepare_spreadsheet_data(pd.DataFrame({"C": [[1], "=f", None]}))
    assert sola["C"].tolist()[:2] == ["[1]", "'=f"]
    assert out["N"].to_numpy(copy=False) is df["N"].to_numpy(copy=False) or (
        out["N"].to_numpy(copy=False).base is df["N"].to_numpy(copy=False).base
    )
    pd.testing.assert_frame_equal(df, copia)  # la fuente intacta: el dict sigue siendo dict
    assert df["O"].iloc[0] == {"a": 1}


def test_to_excel_escribe_la_razon_social_con_mojibake(tmp_path) -> None:
    """La ruta que usa el notebook 07: PipelineResult.to_excel con openpyxl."""
    resultado = PipelineResult(work_dir=tmp_path, extra={"CORRELATIVA": _tabla()})
    ruta = resultado.to_excel(tmp_path / "salida.xlsx", include=("CORRELATIVA",))
    leido = pd.read_excel(ruta, sheet_name="CORRELATIVA")
    assert leido["RAZON_SOCIAL"].iloc[0] == "COMPAÃIA DE GALLETAS POZUELO DCR SA"
    assert leido["FOB"].tolist() == [1.5, -2.0, 3.0, 4.0, 5.0]


def test_metricas_de_importadores_caben_en_parquet(tmp_path) -> None:
    pytest.importorskip("pyarrow")
    base = pd.DataFrame(
        {
            "RAZON_SOCIAL": ["ACME LLC", "ACME L.L.C.", "BETA INC", "BETA INC", MOJIBAKE],
            "PAIS": ["ESTADOS UNIDOS", "Estados Unidos", "ALEMANIA", "ALEMANIA", "COSTA RICA"],
            "FOB": [10.0, 20.0, 5.0, 7.0, 1.0],
        }
    )
    cfg = ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
    )
    r = deduplicar_importadores(base, cfg)
    assert r.todo_ok
    assert r.metricas["valor"].map(type).eq(str).all()
    for nombre, tabla in r.tablas().items():
        tabla.to_parquet(tmp_path / f"{nombre}.parquet", index=False)  # ninguna debe fallar
    # Y el XLSX completo, con la grafía mojibake en la correlativa, se escribe.
    PipelineResult(work_dir=tmp_path, extra=r.tablas()).to_excel(
        tmp_path / "todo.xlsx", include=tuple(r.tablas())
    )
