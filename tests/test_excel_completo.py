"""F1.11 · Excel completo hasta 1.048.576 filas o ``<base>_LEEME.xlsx``; nunca un recorte.

Qué congela
-----------
* ``exporters.excel.escribir_excel_o_leeme``:
  (a) 1.100.000 filas sintéticas de 3 columnas → NO se escribe ningún xlsx
      de datos; se escribe ``correlativa_LEEME.xlsx`` con el conteo de filas
      y el nombre del parquet. Se mide tiempo y RAM (``MuestreadorRecursos``)
      y se exige que la escritura no duplique la tabla en memoria;
  (b) 120.000 filas → ``correlativa.xlsx`` completo con 120.000 filas de
      datos (contadas con openpyxl ``read_only=True``), también cuando la
      fuente es un ``pq.ParquetFile`` (flujo desde disco) y con hoja LEEME
      delante (alias de v1);
  (c) un valor que empieza por ``=`` o trae ``\\x1a`` se neutraliza, un
      ausente queda como celda vacía y un número negativo sigue siendo número;
* ``DataExportStrategy`` (alias de v1 de L6) ya NO escribe ``_MUESTRA_<n>k``:
  por encima del límite deja ``tabla_correlativa_LEEME.xlsx`` /
  ``golden_records_LEEME.xlsx`` y lo registra en ``omitidos`` con el MISMO
  motivo que el estándar (``motivo_no_cabe``, puntos de millar); la hoja de
  datos del alias sigue llamándose ``datos`` (F1.10); la perilla
  ``export_settings.excel_max_rows`` ya no recorta nada y se avisa;
* revisión F1.11: una tabla de un solo bloque (todas int, todas fecha, una
  columna de texto) se escribe (``to_numpy`` de pandas 3 es de solo lectura);
  una celda > 32.767 caracteres o una fecha con zona horaria falla con
  ``EscrituraSalidaError`` sin pedir otra corrida (el escritor del estándar la
  deja en ``omitidos``, ver ``test_escritor.py``); el LEEME cita la ruta
  relativa real del parquet;
* revisión F1.11 ronda 3: un dict/list/tuple/bytes/ndarray en una celda (columna
  extra de la fuente que sobrevive al parquet: ``struct``, ``list``, ``binary``)
  se escribe como el MISMO texto que ``csv.gz`` (la regla vive en
  ``prepare_spreadsheet_data``), y un objeto que ni así se representa falla con
  ``EscrituraSalidaError`` que nombra la columna (nunca un ``TypeError`` que
  tumbe la carpeta); un ``timedelta`` se escribe como texto (no como la fecha
  1900-01-01) y una fecha anterior a 1900 como texto ISO (no desplazada un día);
  la RAM de la escritura se acota en absoluto (64 MiB: openpyxl necesitaba
  +144 MiB para la misma tabla de 120.000 filas);
* en ``src/`` no queda ningún ``_MUESTRA_`` ni ``excel_limit``: una sola
  regla (``LIMITE_FILAS_EXCEL`` del escritor).

Por qué ``pd.ExcelWriter(engine="xlsxwriter", constant_memory=True)`` NO se usa
tal cual: pandas escribe las celdas COLUMNA a columna y ``constant_memory``
descarta cualquier celda de una fila ya volcada, así que el libro queda con
una sola columna completa (medido: 3 columnas → solo la última con datos). El
módulo escribe las filas con ``xlsxwriter`` directamente, por lotes; la prueba
(b) es la que lo vigila.

Los datos son sintéticos (numpy); ninguna empresa real entra aquí.
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import xlsxwriter
from openpyxl import load_workbook

from record_linkage.evaluation.banco import MuestreadorRecursos
from record_linkage.exporters import escritor, excel
from record_linkage.exporters.excel import (
    LIMITE_FILAS_EXCEL,
    escribir_excel_o_leeme,
    leeme_no_cabe,
    motivo_no_cabe,
)
from record_linkage.pipeline.errores import EscrituraSalidaError
from record_linkage.reporting import strategies
from record_linkage.reporting.strategies import DataExportStrategy

RAIZ = Path(__file__).resolve().parent.parent
SRC = RAIZ / "src" / "record_linkage"
N_GRANDE = 1_100_000
N_CABE = 120_000


def _sintetico(n: int, semilla: int = 42) -> pd.DataFrame:
    """n filas × 3 columnas inventadas: entero, decimal y texto (sin nombres reales)."""
    rng = np.random.default_rng(semilla)
    enteros = rng.integers(0, 1_000_000, size=n)
    return pd.DataFrame(
        {
            "ID": np.arange(n, dtype=np.int64),
            "VALOR": rng.random(n),
            "CODIGO": pd.Series(enteros).astype("string").radd("C-"),
        }
    )


def _contar_filas(ruta: Path, hoja: str) -> int:
    libro = load_workbook(ruta, read_only=True)
    try:
        return sum(1 for _ in libro[hoja].iter_rows(values_only=True))
    finally:
        libro.close()


def _texto_leeme(ruta: Path) -> str:
    libro = load_workbook(ruta, read_only=True)
    try:
        assert libro.sheetnames == ["LEEME"]
        celdas = [str(c) for fila in libro["LEEME"].iter_rows(values_only=True) for c in fila if c]
    finally:
        libro.close()
    return "\n".join(celdas)


# ─────────────────────────────────────────────────────────────────────────────
# (a) No cabe → LEEME, sin recorte
# ─────────────────────────────────────────────────────────────────────────────


def test_limite_es_el_del_escritor() -> None:
    """Una sola regla: la constante vive en ``exporters.excel`` y el escritor la reexporta."""
    assert LIMITE_FILAS_EXCEL == 1_048_575
    assert escritor.LIMITE_FILAS_EXCEL is LIMITE_FILAS_EXCEL


def test_1_1_millones_de_filas_no_escribe_xlsx_de_datos_sino_leeme(tmp_path: Path) -> None:
    df = _sintetico(N_GRANDE)
    assert len(df) > LIMITE_FILAS_EXCEL
    mib_tabla = df.memory_usage(deep=True).sum() / 1024**2
    ruta = tmp_path / "correlativa.xlsx"

    inicio = time.perf_counter()
    with MuestreadorRecursos(0.05) as muestreador:
        escrito = escribir_excel_o_leeme(df, ruta)
    segundos = time.perf_counter() - inicio

    assert escrito == tmp_path / "correlativa_LEEME.xlsx"
    assert not ruta.exists(), "no se escribe ningún xlsx de datos: ni completo ni recortado"
    assert not list(tmp_path.glob("*MUESTRA*"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["correlativa_LEEME.xlsx"]
    texto = _texto_leeme(escrito)
    assert "1.100.000" in texto
    assert "correlativa.parquet" in texto
    assert "read_parquet" in texto and "duckdb" in texto.lower() and "Power Query" in texto
    assert "1.048.576" in texto
    # Se mide y se reporta. La cota es ABSOLUTA: «< mib_tabla» no podía fallar
    # (este camino solo mira len(df)); 64 MiB sí vigila que nadie vuelva a tocar
    # la tabla (33 MiB) ni a armar un libro en memoria por este camino.
    crecimiento = muestreador.pico_mib - muestreador.inicial_mib
    assert crecimiento < 64, (
        f"el LEEME no debe tocar la tabla: +{crecimiento:.0f} MiB (tabla {mib_tabla:.0f} MiB)"
    )
    assert segundos < 60, f"{segundos:.1f} s: márquela slow"


def test_justo_en_el_limite_cabe_y_una_mas_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel, "LIMITE_FILAS_EXCEL", 5)
    cabe = escribir_excel_o_leeme(_sintetico(5), tmp_path / "a.xlsx")
    assert cabe == tmp_path / "a.xlsx" and _contar_filas(cabe, "DATOS") == 6
    no_cabe = escribir_excel_o_leeme(_sintetico(6), tmp_path / "b.xlsx")
    assert no_cabe == tmp_path / "b_LEEME.xlsx" and not (tmp_path / "b.xlsx").exists()
    assert "b.parquet" in _texto_leeme(no_cabe)
    # ``limite`` explícito manda sobre la constante.
    explicito = escribir_excel_o_leeme(_sintetico(6), tmp_path / "c.xlsx", limite=6)
    assert explicito == tmp_path / "c.xlsx"


# ─────────────────────────────────────────────────────────────────────────────
# (b) Cabe → Excel completo, en flujo
# ─────────────────────────────────────────────────────────────────────────────


def test_120k_filas_escribe_el_excel_completo(tmp_path: Path) -> None:
    df = _sintetico(N_CABE)
    ruta = tmp_path / "correlativa.xlsx"

    inicio = time.perf_counter()
    with MuestreadorRecursos(0.05) as muestreador:
        escrito = escribir_excel_o_leeme(df, ruta)
    segundos = time.perf_counter() - inicio

    assert escrito == ruta and ruta.is_file()
    assert not (tmp_path / "correlativa_LEEME.xlsx").exists()
    libro = load_workbook(ruta, read_only=True)
    try:
        assert libro.sheetnames == ["DATOS"]
        filas = libro["DATOS"].iter_rows(values_only=True)
        assert next(filas) == ("ID", "VALOR", "CODIGO")
        primera = next(filas)
        assert primera[0] == 0 and isinstance(primera[1], float) and primera[2].startswith("C-")
        n = 2 + sum(1 for _ in filas)
    finally:
        libro.close()
    assert n == N_CABE + 1  # 120.000 de datos + encabezado
    # Todas las columnas llegan completas (constant_memory con pandas las perdía).
    assert _contar_filas(ruta, "DATOS") == N_CABE + 1
    ultima = pd.read_excel(ruta, sheet_name="DATOS", skiprows=N_CABE, nrows=1, header=None)
    assert ultima.iloc[0, 0] == N_CABE - 1 and str(ultima.iloc[0, 2]).startswith("C-")
    assert segundos < 60, f"{segundos:.1f} s"
    assert muestreador.muestras >= 1
    # Este camino SÍ recorre la tabla: la RAM crece con el lote (50.000 filas), no
    # con la tabla. Medido: openpyxl (libro entero en memoria) necesitaba +144 MiB
    # para esta misma tabla; el flujo, < 10 MiB. La cota es absoluta porque la
    # tabla pesa 3,6 MiB y «< mib_tabla» sería ruido del asignador, no una guardia.
    crecimiento = muestreador.pico_mib - muestreador.inicial_mib
    assert crecimiento < 64, f"la escritura armó la tabla en RAM: +{crecimiento:.0f} MiB"


def test_desde_parquet_en_flujo_y_con_leeme_delante(tmp_path: Path) -> None:
    """La fuente puede ser un ``pq.ParquetFile`` (L6 desde disco): lotes, no la tabla entera."""
    df = _sintetico(N_CABE)
    parquet = tmp_path / "tabla_correlativa.parquet"
    escritor.escribir_parquet(df, parquet)
    del df
    ruta = tmp_path / "tabla_correlativa.xlsx"
    escrito = escribir_excel_o_leeme(
        pq.ParquetFile(parquet),
        ruta,
        leeme=escritor.leeme_alias_v1("excel/correlativa.xlsx"),
        filas_por_lote=7_000,
    )
    assert escrito == ruta
    libro = load_workbook(ruta, read_only=True)
    try:
        assert libro.sheetnames == ["LEEME", "DATOS"]
        assert "excel/correlativa.xlsx" in "\n".join(
            str(c) for fila in libro["LEEME"].iter_rows(values_only=True) for c in fila if c
        )
    finally:
        libro.close()
    assert _contar_filas(ruta, "DATOS") == N_CABE + 1
    # Por encima del límite, el parquet también va a LEEME (sin leerlo entero).
    no_cabe = escribir_excel_o_leeme(pq.ParquetFile(parquet), ruta, limite=10)
    assert no_cabe == tmp_path / "tabla_correlativa_LEEME.xlsx"
    assert "tabla_correlativa.parquet" in _texto_leeme(no_cabe)
    assert "120.000" in _texto_leeme(no_cabe)


def test_tabla_vacia_deja_solo_el_encabezado(tmp_path: Path) -> None:
    ruta = escribir_excel_o_leeme(_sintetico(0), tmp_path / "vacia.xlsx", hoja="golden")
    assert _contar_filas(ruta, "golden") == 1


def _filas(ruta: Path, hoja: str = "DATOS") -> list[tuple]:
    libro = load_workbook(ruta, read_only=True)
    try:
        return list(libro[hoja].iter_rows(values_only=True))
    finally:
        libro.close()


def test_tabla_de_un_solo_bloque_numerico_se_escribe(tmp_path: Path) -> None:
    """Revisión F1.11 (1): con pandas 3 ``to_numpy`` devuelve un arreglo de SOLO
    LECTURA cuando el frame tiene un único bloque (todas int, todas float, una
    columna de texto…); la conversión de ausentes debe trabajar sobre una copia."""
    ruta = escribir_excel_o_leeme(pd.DataFrame({"A": [1, 2]}), tmp_path / "int.xlsx")
    assert _filas(ruta) == [("A",), (1,), (2,)]
    tres = pd.DataFrame({"A": [1.5, np.nan, 3.0], "B": [np.nan, 2.0, 3.0], "C": [1.0, 2.0, np.nan]})
    assert _filas(escribir_excel_o_leeme(tres, tmp_path / "float.xlsx")) == [
        ("A", "B", "C"),
        (1.5, None, 1.0),
        (None, 2.0, 2.0),
        (3.0, 3.0, None),
    ]
    texto = pd.DataFrame({"T": pd.array(["a", None, "=b"], dtype="string")})
    assert _filas(escribir_excel_o_leeme(texto, tmp_path / "texto.xlsx")) == [
        ("T",),
        ("a",),
        (None,),
        ("'=b",),
    ]


def test_tabla_de_solo_fechas_con_nat_deja_celdas_vacias(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "DESDE": pd.to_datetime(["2026-10-06 14:30:59", None, "2026-01-01"], format="ISO8601"),
            "HASTA": pd.to_datetime([None, "2026-02-02", "2026-03-03"], format="ISO8601"),
        }
    )
    filas = _filas(escribir_excel_o_leeme(df, tmp_path / "fechas.xlsx"))
    assert len(filas) == 4 and filas[0] == ("DESDE", "HASTA")
    assert str(filas[1][0]).startswith("2026-10-06 14:30:59") and filas[1][1] is None
    assert filas[2][0] is None and str(filas[2][1]).startswith("2026-02-02")
    assert filas[3] == (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-03"))


def test_fecha_con_zona_horaria_falla_con_mensaje_y_sin_restos(tmp_path: Path) -> None:
    """Excel no admite zona horaria; no se quita en silencio: se dice qué columna y qué hacer."""
    df = pd.DataFrame(
        {"ID": [1, 2], "CUANDO": pd.to_datetime(["2026-01-01", "2026-01-02"]).tz_localize("UTC")}
    )
    with pytest.raises(EscrituraSalidaError, match=r"CUANDO.*zona horaria"):
        escribir_excel_o_leeme(df, tmp_path / "tz.xlsx")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "zonas",
    [
        pytest.param([datetime.timezone.utc] * 3, id="utc"),
        pytest.param([datetime.timezone(datetime.timedelta(hours=-5))] * 3, id="menos_cinco"),
        pytest.param(
            [datetime.timezone.utc, datetime.timezone(datetime.timedelta(hours=-5)), None],
            id="offsets_distintos_y_nulo",
        ),
    ],
)
def test_columna_object_con_datetime_con_zona_horaria_falla_con_mensaje_y_sin_restos(
    tmp_path: Path, zonas: list[datetime.timezone | None]
) -> None:
    """Revisión F1.11 ronda 4 (medio): una columna ``object`` de ``datetime`` con
    ``tzinfo`` (un driver de base de datos por fila, un JSON de API; con offsets
    distintos pandas la deja en object) hacía que ``_fechas_antiguas`` comparara una
    serie tz-aware con un Timestamp naive FUERA del ``try`` → ``TypeError`` crudo que
    tumbaba la carpeta. Debe salir por la misma puerta que ``DatetimeTZDtype``:
    ``EscrituraSalidaError`` con «zona horaria» y el nombre de la columna, sin restos."""
    valores = [
        datetime.datetime(2026, 1, 1 + i, 10, 0, tzinfo=z) if z is not None else None
        for i, z in enumerate(zonas)
    ]
    df = pd.DataFrame({"ID": [1, 2, 3], "CUANDO": pd.Series(valores, dtype=object)})
    assert df["CUANDO"].dtype == object  # el caso del hallazgo: NO es DatetimeTZDtype
    with pytest.raises(EscrituraSalidaError, match=r"CUANDO.*zona horaria") as info:
        escribir_excel_o_leeme(df, tmp_path / "tz_object.xlsx")
    assert "parquet" in str(info.value) and "linkage()" not in str(info.value)
    assert not list(tmp_path.iterdir())


def test_columna_object_con_fechas_naive_y_una_con_zona_no_sale_como_typeerror(
    tmp_path: Path,
) -> None:
    """La variante más rara: naive y tz-aware MEZCLADOS en una columna object (pandas no
    la infiere como tz-aware). xlsxwriter la rechaza al escribir y el error debe seguir
    siendo ``EscrituraSalidaError`` nombrando la columna, nunca ``TypeError``."""
    df = pd.DataFrame(
        {
            "ID": [1, 2],
            "CUANDO": pd.Series(
                [
                    datetime.datetime(2026, 1, 1, 10, 0),
                    datetime.datetime(2026, 1, 2, 10, 0, tzinfo=datetime.timezone.utc),
                ],
                dtype=object,
            ),
        }
    )
    with pytest.raises(EscrituraSalidaError, match=r"CUANDO"):
        escribir_excel_o_leeme(df, tmp_path / "tz_mezcla.xlsx")
    assert not list(tmp_path.iterdir())


def test_celda_de_mas_de_32767_caracteres_falla_sin_pedir_otra_corrida(tmp_path: Path) -> None:
    df = pd.DataFrame({"OBS": ["x" * 40_000], "N": [1]})
    with pytest.raises(EscrituraSalidaError, match=r"32\.767") as info:
        escribir_excel_o_leeme(df, tmp_path / "largo.xlsx")
    assert "linkage()" not in str(info.value)  # la carpeta se publica; no se repite la corrida
    assert "parquet" in str(info.value)
    assert not list(tmp_path.iterdir())


def test_contenedores_y_bytes_se_escriben_como_el_texto_de_csv(tmp_path: Path) -> None:
    """Revisión F1.11 ronda 3 (medio): una columna extra de la fuente con dict/list/
    tuple/bytes/ndarray (struct, list o binary de parquet; JSON de una API) no tumba
    el Excel: se escribe como el MISMO texto que ``csv.gz`` (``str``), sin mutar la
    fuente. La regla vive en ``prepare_spreadsheet_data`` (una regla, una vez)."""
    df = pd.DataFrame(
        {
            "ID": [1, 2, 3, 4, 5, 6],
            "EXTRA": [{"a": 1}, [1, 2], b"abc", (3, 4), np.array([5, 6]), None],
            "MEZCLA": [7, {"b": "=x"}, "texto", None, 8.5, [b"z"]],
        }
    )
    copia = df.copy(deep=True)
    filas = _filas(escribir_excel_o_leeme(df, tmp_path / "obj.xlsx"))
    assert [f[1] for f in filas[1:]] == ["{'a': 1}", "[1, 2]", "b'abc'", "(3, 4)", "[5 6]", None]
    assert [f[2] for f in filas[1:]] == [7, "{'b': '=x'}", "texto", None, 8.5, "[b'z']"]
    # El mismo texto que csv.gz escribe (pandas usa str() para los objetos).
    esperado = df.to_csv(index=False).splitlines()[1:]
    assert [f"{f[0]},{f[1] or ''},{f[2] if f[2] is not None else ''}" for f in filas[1:]] == [
        linea.replace('"', "") for linea in esperado
    ]
    assert df["EXTRA"][0] == {"a": 1} and df["EXTRA"][2] == b"abc"  # la fuente no se muta
    pd.testing.assert_frame_equal(df, copia)


def test_objeto_que_excel_no_admite_falla_nombrando_la_columna_y_sin_restos(
    tmp_path: Path,
) -> None:
    """Lo que ni como texto se representa (un objeto cualquiera) no sale como
    ``TypeError`` de xlsxwriter sino como ``EscrituraSalidaError`` que nombra fila,
    columna y tipo: el escritor del estándar lo deja en ``omitidos`` y publica."""
    df = pd.DataFrame({"ID": [1, 2], "RARA": [None, object()]})
    with pytest.raises(EscrituraSalidaError, match=r"fila 2.*columna RARA.*tipo object") as info:
        escribir_excel_o_leeme(df, tmp_path / "raro.xlsx")
    assert "parquet" in str(info.value) and "linkage()" not in str(info.value)
    assert "astype('string')" in str(info.value)
    assert not list(tmp_path.iterdir())


def test_fallo_de_xlsxwriter_al_cerrar_tampoco_deja_restos_ni_typeerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """xlsxwriter también puede rechazar una celda al VOLCAR la hoja (``close``);
    esa vía tampoco sale como ``TypeError``."""

    def _cerrar_roto(self: object) -> None:
        raise TypeError("Unsupported type <class 'x'> in write()")

    monkeypatch.setattr(xlsxwriter.Workbook, "close", _cerrar_roto)
    with pytest.raises(EscrituraSalidaError, match=r"volcar.*cierre\.xlsx"):
        escribir_excel_o_leeme(_sintetico(3), tmp_path / "cierre.xlsx")
    assert not list(tmp_path.iterdir())


def test_timedelta_se_escribe_como_texto_no_como_fecha_de_1900(tmp_path: Path) -> None:
    """Revisión F1.11 ronda 3 (bajo): xlsxwriter escribía ``1 days`` como la fecha
    1900-01-01. Ahora va como el texto que ``csv.gz`` lleva (``1 days 00:00:00``);
    un ausente sigue vacío."""
    df = pd.DataFrame({"ID": [1, 2, 3], "T": pd.to_timedelta(["1 days", "01:02:03.5", None])})
    filas = _filas(escribir_excel_o_leeme(df, tmp_path / "td.xlsx"))
    assert [f[1] for f in filas[1:]] == ["1 days 00:00:00", "0 days 01:02:03.500000", None]
    assert filas[1][1] == df.to_csv(index=False).splitlines()[1].split(",")[1]


def test_fecha_anterior_a_1900_se_escribe_como_texto_iso(tmp_path: Path) -> None:
    """Revisión F1.11 ronda 3 (bajo): antes de 1900 Excel no tiene serial y xlsxwriter
    escribía uno negativo que se leía un día antes; el 1900-01-01 lo escribe como
    serial 0 («solo hora») y enero-febrero de 1900 cargan con el defecto del año
    bisiesto. Todo lo anterior a 1900-03-01 va como texto ISO; el resto sigue
    siendo fecha de Excel."""
    df = pd.DataFrame(
        {
            "F": pd.to_datetime(
                [
                    "1899-01-01",
                    "1899-12-31 10:30:00",
                    "1900-01-01",
                    "1900-02-28",
                    "1900-03-01",
                    None,
                    "2026-01-02",
                ],
                format="ISO8601",
            )
        }
    )
    filas = _filas(escribir_excel_o_leeme(df, tmp_path / "f.xlsx"))
    assert [f[0] for f in filas[1:]] == [
        "1899-01-01 00:00:00",
        "1899-12-31 10:30:00",
        "1900-01-01 00:00:00",
        "1900-02-28 00:00:00",
        datetime.datetime(1900, 3, 1),
        None,
        datetime.datetime(2026, 1, 2),
    ]
    # Una columna date32 de parquet llega por lotes como datetime.date: misma regla.
    parquet = tmp_path / "fechas.parquet"
    pq.write_table(
        pa.table({"D": pa.array([datetime.date(1899, 1, 1), None, datetime.date(2026, 1, 2)])}),
        parquet,
    )
    filas = _filas(escribir_excel_o_leeme(pq.ParquetFile(parquet), tmp_path / "d.xlsx"))
    assert [f[0] for f in filas[1:]] == ["1899-01-01", None, datetime.datetime(2026, 1, 2)]


def test_motivo_no_cabe_se_redacta_una_vez_con_puntos_de_millar() -> None:
    motivo = motivo_no_cabe(1_100_000, "correlativa_LEEME.xlsx")
    assert "1.100.000" in motivo and "1.048.575" in motivo and "correlativa_LEEME.xlsx" in motivo
    assert "," not in motivo.replace(", ", "")  # nunca el separador inglés 1,100,000
    assert motivo_no_cabe(11, "a_LEEME.xlsx", limite=10).startswith("11 filas superan")
    assert escritor.miles(1_048_576) == "1.048.576"
    assert not hasattr(escritor, "_miles")  # la copia de escritor.py desapareció


def test_leeme_cita_la_ruta_relativa_del_parquet() -> None:
    hoja = leeme_no_cabe("correlativa", 5, limite=4, ruta_parquet="../correlativa.parquet")
    texto = "\n".join(hoja["LEEME"])
    assert "pd.read_parquet('../correlativa.parquet')" in texto
    assert "SELECT * FROM '../correlativa.parquet'" in texto
    assert "correlativa.parquet tiene 5 filas" in texto
    # Sin ruta explícita, el parquet se cita por su nombre (los alias de v1 lo tienen al lado).
    assert "pd.read_parquet('golden.parquet')" in "\n".join(leeme_no_cabe("golden", 5)["LEEME"])


# ─────────────────────────────────────────────────────────────────────────────
# (c) Neutralización: una regla escrita una vez (prepare_spreadsheet_data)
# ─────────────────────────────────────────────────────────────────────────────


def test_formulas_controles_y_ausentes(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "=HEADER": ["=2+2", "COMPA\x1aIA", None, "normal"],
            "N": [-7, 1, 2, 3],
            "F": [1.5, np.nan, 2.5, np.inf],
            "T": pd.to_datetime(
                ["2026-10-06 14:30:59", None, "2026-01-01", "2026-01-02"], format="ISO8601"
            ),
            "B": [True, False, True, False],
        }
    )
    copia = df.copy(deep=True)
    ruta = escribir_excel_o_leeme(df, tmp_path / "x.xlsx")
    pd.testing.assert_frame_equal(df, copia)  # la fuente no se muta
    libro = load_workbook(ruta, read_only=True, data_only=False)
    try:
        hoja = libro["DATOS"]
        filas = list(hoja.iter_rows(values_only=True))
    finally:
        libro.close()
    assert filas[0][0] == "'=HEADER"
    assert filas[1][0] == "'=2+2"
    assert filas[2][0] == "COMPAIA"  # el \x1a desaparece
    assert filas[3][0] is None  # ausente → celda vacía, no "nan" ni "None"
    assert filas[1][1] == -7 and isinstance(filas[1][1], int)
    assert filas[2][2] is None  # NaN → vacía
    assert filas[4][2] == "inf"  # ±inf → texto, como to_csv; nunca la fórmula =1/0
    assert filas[2][3] is None  # NaT → vacía
    assert str(filas[1][3]).startswith("2026-10-06 14:30:59")
    assert filas[1][4] is True and filas[2][4] is False
    # openpyxl no marca ninguna celda como fórmula.
    libro = load_workbook(ruta, read_only=False, data_only=False)
    try:
        assert all(c.data_type != "f" for fila in libro["DATOS"].iter_rows() for c in fila)
    finally:
        libro.close()


# ─────────────────────────────────────────────────────────────────────────────
# L6 (alias de v1): nunca más _MUESTRA_<n>k
# ─────────────────────────────────────────────────────────────────────────────


def _archivos(files: list[Path]) -> list[str]:
    return sorted(p.name for p in files)


def test_l6_desde_memoria_no_recorta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(excel, "LIMITE_FILAS_EXCEL", 10)
    estrategia = DataExportStrategy()
    log = logging.getLogger(__name__)
    files = estrategia._export_from_memory(_sintetico(11), "tabla_correlativa", tmp_path, log)
    assert _archivos(files) == [
        "tabla_correlativa.csv.gz",
        "tabla_correlativa.parquet",
        "tabla_correlativa_LEEME.xlsx",
    ]
    assert not list(tmp_path.glob("*MUESTRA*"))
    texto = _texto_leeme(tmp_path / "tabla_correlativa_LEEME.xlsx")
    assert "tabla_correlativa.parquet" in texto and "11" in texto
    assert "excel/correlativa.xlsx" in texto  # el alias remite al archivo nuevo
    assert [o.artefacto for o in estrategia.omitidos] == ["tabla_correlativa.xlsx"]
    # El motivo es el mismo texto que escribe el estándar (una regla, una redacción).
    assert estrategia.omitidos[0].motivo == motivo_no_cabe(11, "tabla_correlativa_LEEME.xlsx")
    assert "(10 de datos)" in estrategia.omitidos[0].motivo
    # Cabe → completo, con LEEME delante (alias) y la hoja «datos» de F1.10
    # (pd.read_excel(sheet_name="datos") distingue mayúsculas: el alias no cambia).
    files = estrategia._export_from_memory(_sintetico(10), "golden_records", tmp_path, log)
    assert "golden_records.xlsx" in _archivos(files)
    libro = load_workbook(tmp_path / "golden_records.xlsx", read_only=True)
    try:
        assert libro.sheetnames == ["LEEME", "datos"]
    finally:
        libro.close()
    assert _contar_filas(tmp_path / "golden_records.xlsx", "datos") == 11


def test_l6_desde_disco_no_recorta_ni_lee_entero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(excel, "LIMITE_FILAS_EXCEL", 10)
    fuente = tmp_path / "golden.parquet"
    escritor.escribir_parquet(_sintetico(11), fuente)
    estrategia = DataExportStrategy()
    log = logging.getLogger(__name__)
    files = estrategia._export_from_disk_streaming(fuente, "golden_records", tmp_path, log)
    assert _archivos(files) == [
        "golden_records.csv.gz",
        "golden_records.parquet",
        "golden_records_LEEME.xlsx",
    ]
    assert not list(tmp_path.glob("*MUESTRA*"))
    assert "golden_records.parquet" in _texto_leeme(tmp_path / "golden_records_LEEME.xlsx")
    assert [o.artefacto for o in estrategia.omitidos] == ["golden_records.xlsx"]


def test_excel_max_rows_ya_no_recorta_y_se_avisa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """La perilla de v1 pedía recortar a N filas; ahora no hace nada y se dice."""
    monkeypatch.setattr(strategies, "_PERILLA_EXCEL_AVISADA", False)
    ctx = strategies.ReportingContext(
        correlative_df=_sintetico(30),
        golden_df=_sintetico(3),
        metrics={},
        config={"export_settings": {"excel_max_rows": 20}, "reporting_use_checkpoints": False},
        output_dir=tmp_path,
        start_time=time.time(),
    )
    with caplog.at_level(logging.WARNING, logger="record_linkage.reporting.strategies"):
        files = DataExportStrategy().execute(ctx, logging.getLogger(__name__))
    assert "tabla_correlativa.xlsx" in _archivos(files)
    assert _contar_filas(tmp_path / "tabla_correlativa.xlsx", "datos") == 31
    assert not list(tmp_path.glob("*MUESTRA*"))
    avisos = [r.getMessage() for r in caplog.records if "excel_max_rows" in r.getMessage()]
    assert len(avisos) == 1 and "1.048.575" in avisos[0]  # puntos de millar, como el LEEME
    assert not hasattr(DataExportStrategy, "EXCEL_ROW_LIMIT")


def test_ningun_modulo_de_src_recorta_ni_tiene_limite_propio() -> None:
    """``grep -rn MUESTRA_100k src`` → 0; ``excel_limit`` tampoco. Una sola regla."""
    # ``_MUESTRA_`` seguido de dígito, ``*``, ``<`` o ``{``: el nombre del recorte
    # (``PERILLAS_MUESTRA_RETIRADAS`` de reports.py no lo es).
    patron = re.compile(r"_MUESTRA_[\d*<{]|MUESTRA_100k|excel_limit|EXCEL_ROW_LIMIT")
    hallazgos = {
        p.relative_to(SRC).as_posix(): m.group(0)
        for p in sorted(SRC.rglob("*.py"))
        if (m := patron.search(p.read_text(encoding="utf-8")))
    }
    assert not hallazgos, hallazgos
    perfiles = (SRC / "config" / "profiles.py").read_text(encoding="utf-8")
    interno = (SRC / "pipeline" / "_internal.py").read_text(encoding="utf-8")
    assert '"excel_max_rows"' not in perfiles  # ya no es clave de ningún perfil
    assert '"excel_max_rows"' not in interno
