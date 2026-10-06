"""Rutas en los reportes L6: ``pathlib`` donde F1 añadió código (trinquete por archivo y regla).

El paso «Reglas estrictas en código tocado» cuenta por archivo y regla, y F1
había añadido tres usos nuevos de ``os.path``/``os.unlink`` en ``reports.py``
y ``suite.py`` sobre módulos que ya traían deuda PTH heredada. Estas pruebas
fijan el comportamiento de esos tres sitios, ahora con ``pathlib``:

* ``ReportGenerator._leer_archivo_plano`` rechaza un formato desconocido
  nombrando el archivo (no la ruta completa);
* ``EnhancedReportingSuite._describir_insumo`` no juzga lo que no existe y
  describe un CSV por sus columnas sin cargarlo;
* ``generate_problematic_cases_report`` no deja un ``.xlsx`` a medias si la
  escritura falla, y el error sube al llamador.

Datos: empresas inventadas, ninguna razón social real.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from record_linkage.reporting.reports import ReportGenerator
from record_linkage.reporting.suite import EnhancedReportingSuite
from record_linkage.utils.logger import CustomLogger


def _suite_minima() -> EnhancedReportingSuite:
    """Una suite sin pasar por ``__init__`` (que carga y muestrea): solo lo que usan las pruebas."""
    suite = EnhancedReportingSuite.__new__(EnhancedReportingSuite)
    suite.logger = CustomLogger("prueba_rutas")
    suite.golden_records_sample = pd.DataFrame(
        {
            "ID_GRUPO": [1, 2],
            "NIT_FINAL": ["900111222", "800333444"],
            "RAZON_SOCIAL_FINAL": ["ACME INVENTADA SAS", "BETA INVENTADA LTDA"],
            "CONFIDENCE_SCORE": [0.3, 0.95],
        }
    )
    suite.correlative_sample = pd.DataFrame({"ID_GRUPO": [1, 1, 2]})
    return suite


def test_leer_archivo_plano_rechaza_formato_desconocido_nombrando_el_archivo(
    tmp_path: Path,
) -> None:
    ruta = tmp_path / "carpeta_privada" / "golden.txt"
    with pytest.raises(ValueError, match=r"'golden_records'.*golden\.txt") as exc:
        ReportGenerator._leer_archivo_plano(str(ruta), "golden_records", None)
    assert "carpeta_privada" not in str(exc.value), "se nombra el archivo, no la ruta completa"
    assert ".parquet" in str(exc.value), "dice qué formatos sí acepta"


def test_describir_insumo_no_juzga_lo_que_no_existe_y_describe_un_csv_sin_cargarlo(
    tmp_path: Path,
) -> None:
    suite = _suite_minima()
    assert suite._describir_insumo(str(tmp_path / "no_existe.csv"), "golden_records") == (
        None,
        None,
    )
    assert suite._describir_insumo(123, "golden_records") == (None, None)  # type: ignore[arg-type]

    csv = tmp_path / "golden.csv"
    csv.write_text(
        "ID_GRUPO,RAZON_SOCIAL_FINAL\n1,ACME INVENTADA SAS\n2,BETA INVENTADA LTDA\n",
        encoding="utf-8",
    )
    filas, columnas = suite._describir_insumo(str(csv), "golden_records")
    assert filas is None, "un CSV no se cuenta sin leerlo entero"
    assert columnas == ["ID_GRUPO", "RAZON_SOCIAL_FINAL"]

    marco = pd.DataFrame({"a": [1, 2, 3]})
    assert suite._describir_insumo(marco, "golden_records") == (3, ["a"])


def test_excel_de_casos_problematicos_no_deja_archivo_a_medias_si_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F1.4: si el xlsx no se puede escribir, no queda un archivo corrupto y el error sube."""
    suite = _suite_minima()
    esperado = tmp_path / "casos_problematicos_detallado.xlsx"

    class _EscritorQueFalla:
        def __init__(self, ruta: str, **_kwargs: object) -> None:
            Path(ruta).write_bytes(b"a medias")

        def __enter__(self) -> _EscritorQueFalla:
            raise OSError("disco inventado lleno")

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(pd, "ExcelWriter", _EscritorQueFalla)
    with pytest.raises(OSError, match="disco inventado"):
        suite.generate_problematic_cases_report(str(tmp_path))
    assert not esperado.exists(), "el xlsx a medias debe borrarse"


def test_excel_de_casos_problematicos_se_escribe_cuando_hay_casos(tmp_path: Path) -> None:
    """El camino feliz del mismo método, sobre la suite mínima (un caso de baja confianza)."""
    pytest.importorskip("openpyxl")
    suite = _suite_minima()
    ruta = suite.generate_problematic_cases_report(str(tmp_path))
    assert ruta is not None
    assert Path(ruta) == tmp_path / "casos_problematicos_detallado.xlsx"
    hojas = pd.read_excel(ruta, sheet_name=None)
    assert "Baja_Confianza" in hojas
    assert list(hojas["Baja_Confianza"]["RAZON_SOCIAL_FINAL"]) == ["ACME INVENTADA SAS"]
