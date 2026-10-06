"""``salida.tecnicas``: volver a pegar a la correlativa del contrato las columnas
técnicas que F1.9 retiró (quedan en ``<dir_trabajo>/L5_golden/correlative.parquet``).

Lo que se prueba:

* sin colapso, se alinea por ``ORIGINAL_INDEX`` y se declara así;
* con colapso de duplicados exactos la correlativa entregada está expandida y
  renumerada: alinear por ``ORIGINAL_INDEX`` pegaría valores de otro registro.
  Se alinea por contenido (``SRC``, ``NIT``, ``RAZON_SOCIAL``), se declara, y
  el resultado coincide fila a fila con una corrida sin colapso (la verdad);
* fail-fast con mensaje accionable si no hay ``dir_trabajo``, si no existe el
  parquet, si le falta una columna o si el contenido no determina las técnicas.

Datos: empresas inventadas; el parquet de L5 se fabrica en ``tmp_path`` salvo
en la prueba de extremo a extremo, que corre ``linkage()`` sobre 10 filas.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pandas as pd
import pytest

import record_linkage as rl
from record_linkage.pipeline.errores import ColumnasTecnicasError
from record_linkage.salida import tecnicas

COLUMNAS = ("NIT_BASE", "NIT_VALID")


def _parquet_l5(dir_trabajo: Path, filas: list[dict]) -> Path:
    """Escribe un ``L5_golden/correlative.parquet`` compacto con filas inventadas."""
    ruta = dir_trabajo / tecnicas.RUTA_CORRELATIVA_L5
    ruta.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(filas).to_parquet(ruta, index=False)
    return ruta


def _fila(idx: int, src: str, nit: str | None, nombre: str, base: str, valido: str) -> dict:
    return {
        "SRC": src,
        "ORIGINAL_INDEX": idx,
        "NIT": nit,
        "RAZON_SOCIAL": nombre,
        "NIT_BASE": base,
        "NIT_VALID": valido,
        "ID_GRUPO": 0,
    }


@pytest.fixture
def compacto(tmp_path: Path) -> Path:
    """Parquet de L5 con 4 representantes (como deja ``linkage`` tras colapsar)."""
    _parquet_l5(
        tmp_path,
        [
            _fila(0, "A", "900111222", "ACME SAS", "900111222", "1"),
            _fila(1, "A", "800333444", "BETA LTDA", "800333444", "1"),
            _fila(2, "A", None, "GAMMA", "", "0"),
            _fila(3, "B", "700555666-1", "DELTA SA", "700555666", "1"),
        ],
    )
    return tmp_path


def _correlativa(filas: list[tuple[int, str, str | None, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_REGISTRO": [f"{src}-F{i}" for i, src, _, _ in filas],
            "SRC": [src for _, src, _, _ in filas],
            "ORIGINAL_INDEX": [i for i, _, _, _ in filas],
            "ID_GRUPO": [0] * len(filas),
            "NIT": [nit for _, _, nit, _ in filas],
            "RAZON_SOCIAL": [nombre for _, _, _, nombre in filas],
        }
    )


# ── Alineación por ORIGINAL_INDEX (sin colapso) ────────────────────────────


def test_sin_colapso_alinea_por_original_index(compacto: Path) -> None:
    correl = _correlativa(
        [
            (0, "A", "900111222", "ACME SAS"),
            (1, "A", "800333444", "BETA LTDA"),
            (2, "A", None, "GAMMA"),
            (3, "B", "700555666-1", "DELTA SA"),
        ]
    )
    con, reporte = tecnicas.adjuntar_tecnicas(correl, compacto, COLUMNAS)

    assert list(con.columns) == [*correl.columns, *COLUMNAS]
    assert con["NIT_BASE"].tolist() == ["900111222", "800333444", "", "700555666"]
    assert con["NIT_VALID"].tolist() == ["1", "1", "0", "1"]
    assert reporte.alineacion == "ORIGINAL_INDEX"
    assert reporte.filas_parquet == 4 and reporte.filas_correlativa == 4
    assert reporte.origen == compacto / tecnicas.RUTA_CORRELATIVA_L5
    # No muta la correlativa que recibió.
    assert "NIT_BASE" not in correl.columns


def test_un_subconjunto_en_orden_arbitrario_se_alinea_por_original_index(compacto: Path) -> None:
    correl = _correlativa([(3, "B", "700555666-1", "DELTA SA"), (1, "A", "800333444", "BETA LTDA")])
    con, reporte = tecnicas.adjuntar_tecnicas(correl, compacto, ("NIT_BASE",))
    assert con["NIT_BASE"].tolist() == ["700555666", "800333444"]
    assert reporte.alineacion == "ORIGINAL_INDEX"


# ── Alineación por contenido (correlativa expandida tras colapsar) ──────────


def test_con_colapso_original_index_ya_no_es_la_posicion_y_se_alinea_por_contenido(
    compacto: Path,
) -> None:
    """La entregada tiene 6 filas renumeradas 0..5; la fila 2 entregada (BETA
    repetida) y la 3 (GAMMA) NO son las posiciones 2 y 3 del parquet."""
    correl = _correlativa(
        [
            (0, "A", "900111222", "ACME SAS"),
            (1, "A", "800333444", "BETA LTDA"),
            (2, "A", "800333444", "BETA LTDA"),
            (3, "A", None, "GAMMA"),
            (4, "B", "700555666-1", "DELTA SA"),
            (5, "B", "700555666-1", "DELTA SA"),
        ]
    )
    con, reporte = tecnicas.adjuntar_tecnicas(correl, compacto, COLUMNAS)

    assert reporte.alineacion == "contenido"
    assert con["NIT_BASE"].tolist() == [
        "900111222",
        "800333444",
        "800333444",
        "",
        "700555666",
        "700555666",
    ]
    assert con["NIT_VALID"].tolist() == ["1", "1", "1", "0", "1", "1"]
    assert con.index.equals(correl.index)


def test_falla_si_el_contenido_no_determina_las_tecnicas(tmp_path: Path) -> None:
    """Dos filas del parquet con el mismo SRC·NIT·RAZON_SOCIAL y NIT_BASE distinto:
    por contenido no se puede decidir y no se adivina."""
    _parquet_l5(
        tmp_path,
        [
            _fila(0, "A", "900111222", "ACME SAS", "900111222", "1"),
            _fila(1, "A", "900111222", "ACME SAS", "999999999", "1"),
        ],
    )
    correl = _correlativa(
        [(0, "A", "900111222", "ACME SAS"), (1, "A", "900111222", "ACME SAS"), (2, "A", "1", "X")]
    )
    with pytest.raises(ColumnasTecnicasError, match="no determina"):
        tecnicas.adjuntar_tecnicas(correl, tmp_path, ("NIT_BASE",))


def test_falla_si_una_fila_entregada_no_esta_en_el_parquet(compacto: Path) -> None:
    correl = _correlativa(
        [
            (0, "A", "900111222", "ACME SAS"),
            (1, "A", "900111222", "ACME SAS"),
            (2, "C", "1", "NADIE"),
        ]
    )
    with pytest.raises(ColumnasTecnicasError, match="sin pareja"):
        tecnicas.adjuntar_tecnicas(correl, compacto, ("NIT_BASE",))


# ── Fail-fast accionable ────────────────────────────────────────────────────


def test_sin_dir_trabajo_falla_accionable() -> None:
    correl = _correlativa([(0, "A", "1", "X")])
    with pytest.raises(ColumnasTecnicasError) as exc:
        tecnicas.adjuntar_tecnicas(correl, None, COLUMNAS)
    assert "Qué pasó" in str(exc.value) and "Qué hacer" in str(exc.value)


def test_sin_parquet_falla_y_nombra_la_ruta_esperada(tmp_path: Path) -> None:
    correl = _correlativa([(0, "A", "1", "X")])
    with pytest.raises(ColumnasTecnicasError) as exc:
        tecnicas.adjuntar_tecnicas(correl, tmp_path, COLUMNAS)
    assert str(tmp_path / tecnicas.RUTA_CORRELATIVA_L5) in str(exc.value)
    assert "dedupe()" in str(exc.value)


def test_columna_ausente_en_el_parquet_falla_y_lista_las_que_hay(compacto: Path) -> None:
    correl = _correlativa([(0, "A", "900111222", "ACME SAS")])
    with pytest.raises(ColumnasTecnicasError, match="NOMBRE_BLOQUEO") as exc:
        tecnicas.adjuntar_tecnicas(correl, compacto, ("NIT_BASE", "NOMBRE_BLOQUEO"))
    assert "NIT_VALID" in str(exc.value)  # lista lo que sí trae


def test_no_pega_dos_veces(compacto: Path) -> None:
    correl = _correlativa([(0, "A", "900111222", "ACME SAS")])
    correl["NIT_BASE"] = "ya"
    with pytest.raises(ColumnasTecnicasError, match="ya trae"):
        tecnicas.adjuntar_tecnicas(correl, compacto, ("NIT_BASE",))


# ── Extremo a extremo con linkage(): con y sin colapso dan las mismas técnicas ──


def _fuentes() -> dict[str, pd.DataFrame]:
    a = pd.DataFrame(
        {
            "NIT": ["900111222-1", "900111222", "800333444", "800333444", None, "700555666"],
            "RAZON_SOCIAL": [
                "ACME SAS",
                "ACME S.A.S.",
                "BETA LTDA",
                "BETA LTDA",
                "GAMMA",
                "DELTA SA",
            ],
        }
    )
    b = pd.DataFrame(
        {
            "NIT": ["900111222", "800333444-5", "800333444-5", "700555666"],
            "RAZON_SOCIAL": ["ACME SAS", "BETA LTDA", "BETA LTDA", "DELTA S A"],
        }
    )
    return {"A": a, "B": b}


def _linkage(dir_trabajo: Path, colapsar: bool) -> rl.ResultadoLinkage:
    with contextlib.redirect_stdout(io.StringIO()):
        return rl.linkage(
            _fuentes(),
            work_dir=str(dir_trabajo),
            skip_reporting=True,
            col_ciudad=None,
            collapse_exact_duplicates=colapsar,
        )


def test_linkage_con_colapso_recupera_las_mismas_tecnicas_que_sin_colapso(tmp_path: Path) -> None:
    con_colapso = _linkage(tmp_path / "con", colapsar=True)
    sin_colapso = _linkage(tmp_path / "sin", colapsar=False)
    assert (
        con_colapso.metricas["preprocessing"]["exact_duplicate_collapse"]["A"]["collapsed_rows"]
        == 1
    )

    pegada_con, rep_con = tecnicas.adjuntar_tecnicas(
        con_colapso.correlativa, con_colapso.dir_trabajo, ("NIT_BASE", "NIT_VALID", "NIT_OK")
    )
    pegada_sin, rep_sin = tecnicas.adjuntar_tecnicas(
        sin_colapso.correlativa, sin_colapso.dir_trabajo, ("NIT_BASE", "NIT_VALID", "NIT_OK")
    )
    assert rep_con.alineacion == "contenido" and rep_sin.alineacion == "ORIGINAL_INDEX"
    assert rep_con.filas_parquet == 8 and rep_con.filas_correlativa == 10

    # Misma entrada → ORIGINAL_INDEX entregado = posición original en ambas;
    # las técnicas por registro deben coincidir.
    a = pegada_con.set_index("ORIGINAL_INDEX")[["NIT_BASE", "NIT_VALID", "NIT_OK"]].sort_index()
    b = pegada_sin.set_index("ORIGINAL_INDEX")[["NIT_BASE", "NIT_VALID", "NIT_OK"]].sort_index()
    pd.testing.assert_frame_equal(a, b)
    assert a.loc[3, "NIT_BASE"] == "800333444" and a.loc[4, "NIT_BASE"] == ""
