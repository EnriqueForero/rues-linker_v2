"""Contrato de L6 (F1.4): artefactos obligatorios, opcionales omitidos y
ningún archivo con un error dentro.

Qué exige
---------
1. Una estrategia OPCIONAL que lanza no tumba la corrida: ``linkage()``
   termina y ``manifest.json → L6_reporting.meta.omitidos`` registra el
   artefacto, la estrategia y el motivo.
2. Una estrategia OBLIGATORIA (``DataExportStrategy``) que lanza hace FALLAR
   la corrida con ``ArtefactoObligatorioError`` y mensaje accionable.
3. ``verificar_artefactos`` detecta por NOMBRE EXACTO que falta
   ``golden_records.parquet`` aunque exista ``golden_records_MUESTRA.xlsx``
   (hasta F1.4 el orquestador verificaba por prefijo y eso pasaba).
4. Ningún archivo escrito contiene «Error generando»: un reporte que falla se
   omite y queda en ``omitidos``; no se escribe un xlsx con una celda «Error»
   ni un PNG con el texto del error.

Las corridas usan ``tests/data_sintetica/dataset_sintetico_p2_extra_features.csv``
(29 filas, empresas inventadas) con ``skip_reporting=False``. Cada corrida
cuesta ≈ 20 s, así que las pruebas 1 y 4 comparten una corrida (fixture de
módulo) y la 2 usa otra.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from record_linkage.api import linkage
from record_linkage.pipeline.errores import ArtefactoObligatorioError, EstrategiaFallo
from record_linkage.reporting import contrato_l6, strategies
from record_linkage.reporting.contrato_l6 import (
    ARTEFACTOS_OBLIGATORIOS,
    ARTEFACTOS_OPCIONALES,
    ArtefactoOmitido,
    ReporteL6,
    verificar_artefactos,
)

RUTA_DATASET = (
    Path(__file__).resolve().parent / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"
)
TEXTO_PROHIBIDO = "Error generando"
MOTIVO_DASHBOARD = "matplotlib inventó un fallo en el dashboard"
MOTIVO_REPORTE = "el reporte de métricas de calidad reventó a propósito"


def _correr_linkage(work_dir: Path) -> dict[str, Any]:
    df = pd.read_csv(RUTA_DATASET, dtype=str, keep_default_na=False)
    return linkage(
        {"P2": df},
        work_dir=str(work_dir),
        skip_reporting=False,
        trusted_sources=set(),
        col_ciudad="CIUDAD",
    )


def _leer_manifiesto(work_dir: Path) -> dict[str, Any]:
    return json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))


def _texto_de(ruta: Path) -> str:
    """Contenido textual de un artefacto, para buscar palabras prohibidas.

    Excel: todas las hojas, celdas y encabezados. CSV.gz/TXT/JSON: el texto.
    PNG/parquet: binarios, no se inspeccionan (un PNG con texto de error no
    contiene la cadena como bytes; esa ruta se cubre exigiendo que el archivo
    NO exista cuando el dashboard falla).
    """
    sufijos = "".join(ruta.suffixes)
    if sufijos.endswith(".xlsx"):
        hojas = pd.read_excel(ruta, sheet_name=None, header=None, dtype=str)
        return "\n".join(hoja.fillna("").to_string() for hoja in hojas.values())
    if sufijos.endswith(".csv.gz"):
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            return f.read()
    if ruta.suffix in {".txt", ".json", ".csv"}:
        return ruta.read_text(encoding="utf-8")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Corrida compartida: dashboard (estrategia opcional) y un reporte Excel fallan
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def corrida_con_opcionales_rotos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from record_linkage.reporting.reports import ReportGenerator

    work_dir = tmp_path_factory.mktemp("l6_opcionales")

    def _dashboard_roto(self: Any, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise RuntimeError(MOTIVO_DASHBOARD)

    def _reporte_roto(self: Any) -> pd.DataFrame:
        raise ValueError(MOTIVO_REPORTE)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(strategies.DashboardStrategy, "_execute_impl", _dashboard_roto)
        mp.setattr(ReportGenerator, "_generate_quality_metrics", _reporte_roto)
        resultado = _correr_linkage(work_dir)

    return {"work_dir": work_dir, "resultado": resultado}


def test_estrategia_opcional_que_lanza_no_tumba_la_corrida_y_queda_en_omitidos(
    corrida_con_opcionales_rotos: dict[str, Any],
) -> None:
    work_dir: Path = corrida_con_opcionales_rotos["work_dir"]
    manifiesto = _leer_manifiesto(work_dir)

    fase = manifiesto["L6_reporting"]
    assert fase["status"] == "DONE"
    omitidos = fase["meta"]["omitidos"]
    assert isinstance(omitidos, list) and omitidos, "L6 debe registrar los opcionales omitidos"
    for entrada in omitidos:
        assert set(entrada) == {"artefacto", "estrategia", "motivo"}

    por_estrategia = {(e["estrategia"], e["artefacto"]): e["motivo"] for e in omitidos}
    assert ("DashboardStrategy", "dashboard_ejecutivo.png") in por_estrategia
    assert MOTIVO_DASHBOARD in por_estrategia[("DashboardStrategy", "dashboard_ejecutivo.png")]
    assert ("ExcelReportsStrategy", "reporte_metricas_calidad.xlsx") in por_estrategia
    assert (
        MOTIVO_REPORTE in por_estrategia[("ExcelReportsStrategy", "reporte_metricas_calidad.xlsx")]
    )

    salida = work_dir / "L6_reporting"
    assert not (salida / "dashboard_ejecutivo.png").exists()
    assert not (salida / "reporte_metricas_calidad.xlsx").exists()
    # Los obligatorios siguen ahí, por nombre exacto.
    for nombre in ("tabla_correlativa.parquet", "golden_records.parquet", "golden_records.csv.gz"):
        assert (salida / nombre).is_file(), nombre
    # Lo que la corrida devuelve coincide con lo registrado.
    archivos = {Path(p).name for p in corrida_con_opcionales_rotos["resultado"]["report_files"]}
    assert "dashboard_ejecutivo.png" not in archivos
    assert "tabla_correlativa.parquet" in archivos


def test_ningun_archivo_escrito_contiene_error_generando(
    corrida_con_opcionales_rotos: dict[str, Any],
) -> None:
    salida: Path = corrida_con_opcionales_rotos["work_dir"] / "L6_reporting"
    archivos = sorted(p for p in salida.rglob("*") if p.is_file())
    assert archivos, "L6 no escribió nada"
    culpables = [str(p.relative_to(salida)) for p in archivos if TEXTO_PROHIBIDO in _texto_de(p)]
    assert culpables == [], f"Archivos con un error dentro: {culpables}"
    # Ninguna hoja Excel con la columna «Error» de la versión anterior.
    for ruta in archivos:
        if ruta.suffix == ".xlsx":
            hojas = pd.read_excel(ruta, sheet_name=None, dtype=str)
            for nombre, hoja in hojas.items():
                assert "Error" not in hoja.columns, f"{ruta.name}[{nombre}] trae columna Error"


# ─────────────────────────────────────────────────────────────────────────────
# Estrategia obligatoria que lanza → la corrida falla
# ─────────────────────────────────────────────────────────────────────────────


def test_estrategia_obligatoria_que_lanza_falla_la_corrida(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _exportacion_rota(self: Any, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise OSError("disco lleno (inventado)")

    monkeypatch.setattr(strategies.DataExportStrategy, "_execute_impl", _exportacion_rota)

    with pytest.raises(ArtefactoObligatorioError) as info:
        _correr_linkage(tmp_path)

    mensaje = str(info.value)
    for marcador in ("Qué pasó", "Por qué importa", "Qué hacer"):
        assert marcador in mensaje, f"falta «{marcador}» en:\n{mensaje}"
    assert "DataExportStrategy" in mensaje
    assert "disco lleno (inventado)" in mensaje
    assert isinstance(info.value.__cause__, EstrategiaFallo)
    assert info.value.__cause__.__cause__.__class__ is OSError

    manifiesto = _leer_manifiesto(tmp_path)
    assert manifiesto.get("L6_reporting", {}).get("status") != "DONE"
    assert manifiesto["L5_golden"]["status"] == "DONE"


# ─────────────────────────────────────────────────────────────────────────────
# verificar_artefactos: nombre exacto, no prefijo
# ─────────────────────────────────────────────────────────────────────────────


def _tocar(carpeta: Path, nombre: str) -> Path:
    ruta = carpeta / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"x")
    return ruta


def test_verificar_artefactos_detecta_falta_por_nombre_exacto(tmp_path: Path) -> None:
    generados = [
        _tocar(tmp_path, "golden_records_MUESTRA_100k.xlsx"),
        _tocar(tmp_path, "golden_records.csv.gz"),
        _tocar(tmp_path, "tabla_correlativa.parquet"),
        _tocar(tmp_path, "tabla_correlativa.csv.gz"),
        _tocar(tmp_path, "config_auditoria_20260101_000000.json"),
    ]

    reporte = verificar_artefactos(tmp_path, generados)

    assert isinstance(reporte, ReporteL6)
    assert reporte.obligatorios_faltantes == ("golden_records.parquet",)
    assert not reporte.ok
    assert "golden_records_MUESTRA_100k.xlsx" not in reporte.opcionales_omitidos
    assert "dashboard_ejecutivo.png" in reporte.opcionales_omitidos


def test_verificar_artefactos_exige_que_el_obligatorio_exista_en_disco(tmp_path: Path) -> None:
    """Un nombre en la lista de generados no basta: el archivo debe existir y
    no estar vacío. Un artefacto que una estrategia dijo escribir y no está es
    un obligatorio faltante, no un éxito."""
    generados = [
        _tocar(tmp_path, "golden_records.parquet"),
        _tocar(tmp_path, "golden_records.csv.gz"),
        _tocar(tmp_path, "tabla_correlativa.parquet"),
        _tocar(tmp_path, "tabla_correlativa.csv.gz"),
        _tocar(tmp_path, "config_auditoria_20260101_000000.json"),
    ]
    (tmp_path / "tabla_correlativa.parquet").unlink()
    (tmp_path / "golden_records.csv.gz").write_bytes(b"")

    reporte = verificar_artefactos(tmp_path, generados)

    # En el orden de la declaración, no en el de los generados.
    assert reporte.obligatorios_faltantes == (
        "tabla_correlativa.parquet",
        "golden_records.csv.gz",
    )


def test_verificar_artefactos_completo_es_ok(tmp_path: Path) -> None:
    generados = [
        _tocar(tmp_path, "golden_records.parquet"),
        _tocar(tmp_path, "golden_records.csv.gz"),
        _tocar(tmp_path, "tabla_correlativa.parquet"),
        _tocar(tmp_path, "tabla_correlativa.csv.gz"),
        _tocar(tmp_path, "config_auditoria_20260101_000000.json"),
        _tocar(tmp_path, "dashboard_ejecutivo.png"),
    ]
    reporte = verificar_artefactos(tmp_path, generados)
    assert reporte.ok
    assert reporte.obligatorios_faltantes == ()
    assert "dashboard_ejecutivo.png" not in reporte.opcionales_omitidos


def test_contrato_declara_obligatorios_y_opcionales_sin_solaparse() -> None:
    patrones_obl = {a.patron for a in ARTEFACTOS_OBLIGATORIOS}
    patrones_opc = {a.patron for a in ARTEFACTOS_OPCIONALES}
    assert {
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
        "config_auditoria_*.json",
    } == patrones_obl
    assert not patrones_obl & patrones_opc
    # Toda estrategia incorporada declara al menos un artefacto.
    declaradas = {a.estrategia for a in ARTEFACTOS_OBLIGATORIOS + ARTEFACTOS_OPCIONALES}
    assert declaradas == set(contrato_l6.ESTRATEGIAS_INCORPORADAS)
    obligatorias = set(contrato_l6.ESTRATEGIAS_OBLIGATORIAS)
    assert obligatorias == {"DataExportStrategy", "ConfigAuditStrategy"}


def test_una_subclase_hereda_el_contrato_de_su_estrategia_base() -> None:
    class ExportacionPersonalizada(strategies.DataExportStrategy):
        pass

    class Analitica(strategies.DashboardStrategy):
        pass

    assert ExportacionPersonalizada().obligatoria
    assert not Analitica().obligatoria
    assert contrato_l6.clase_declarada(ExportacionPersonalizada) == "DataExportStrategy"
    assert contrato_l6.artefactos_de(ExportacionPersonalizada()) == contrato_l6.artefactos_de(
        "DataExportStrategy"
    )
    assert contrato_l6.artefactos_de(_EstrategiaQueLanza()) == ()
    assert not contrato_l6.es_estrategia_obligatoria(_EstrategiaQueLanza())


# ─────────────────────────────────────────────────────────────────────────────
# Unidades: la estrategia deja de tragar; ningún generador escribe un error
# ─────────────────────────────────────────────────────────────────────────────


class _EstrategiaQueLanza(strategies.BaseReportingStrategy):
    @property
    def name(self) -> str:
        return "Estrategia de prueba"

    @property
    def required_class(self) -> str:
        return "None"

    def _execute_impl(self, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise KeyError("columna inventada")


def test_base_strategy_relanza_estrategia_fallo_tipada(tmp_path: Path) -> None:
    ctx = strategies.ReportingContext(
        golden_df=pd.DataFrame({"ID_GRUPO": [1]}),
        correlative_df=pd.DataFrame({"ID_GRUPO": [1], "SRC": ["A"]}),
        config={},
        output_dir=tmp_path,
        metrics={},
        start_time=0.0,
    )
    with pytest.raises(EstrategiaFallo) as info:
        _EstrategiaQueLanza().execute(ctx, logging.getLogger("prueba"))
    assert info.value.nombre == "Estrategia de prueba"
    assert isinstance(info.value.__cause__, KeyError)
    assert "columna inventada" in str(info.value)


def test_report_generator_omite_el_reporte_roto_en_vez_de_escribir_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from record_linkage.reporting.reports import ReportGenerator

    correl = pd.DataFrame(
        {"ID_GRUPO": [1, 1, 2], "SRC": ["A", "B", "A"], "CONFIDENCE_SCORE": [0.9, 0.9, 0.5]}
    )
    golden = pd.DataFrame({"ID_GRUPO": [1, 2], "CONFIDENCE_SCORE": [0.9, 0.5]})
    generador = ReportGenerator(
        correlative_data=correl, golden_records_data=golden, metrics={}, config={}
    )

    def _roto(self: Any) -> pd.DataFrame:
        raise ValueError(MOTIVO_REPORTE)

    monkeypatch.setattr(ReportGenerator, "_generate_quality_metrics", _roto)
    reportes = generador.generate_all_reports()

    assert "metricas_calidad" not in reportes
    for nombre, df in reportes.items():
        assert "Error" not in df.columns, nombre
        assert TEXTO_PROHIBIDO not in df.to_string(), nombre
    assert [o.artefacto for o in generador.omitidos] == ["reporte_metricas_calidad.xlsx"]
    assert MOTIVO_REPORTE in generador.omitidos[0].motivo
    assert isinstance(generador.omitidos[0], ArtefactoOmitido)


def test_dashboard_no_escribe_png_de_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from record_linkage.reporting.dashboard import ExecutiveDashboard

    assert not hasattr(ExecutiveDashboard, "_generate_error_dashboard")

    correl = pd.DataFrame({"ID_GRUPO": [1, 1], "SRC": ["A", "B"], "CONFIDENCE_SCORE": [0.9, 0.9]})
    golden = pd.DataFrame({"ID_GRUPO": [1], "CONFIDENCE_SCORE": [0.9]})
    tablero = ExecutiveDashboard(
        correlative_data=correl, golden_records_data=golden, metrics={"total_records": 2}
    )

    def _roto(self: Any, fig: Any, gs_area: Any) -> None:
        raise RuntimeError(MOTIVO_DASHBOARD)

    monkeypatch.setattr(ExecutiveDashboard, "_create_header", _roto)
    destino = tmp_path / "dashboard_ejecutivo.png"
    with pytest.raises(RuntimeError, match="inventó"):
        tablero.generate_dashboard(str(destino))
    assert not destino.exists()
