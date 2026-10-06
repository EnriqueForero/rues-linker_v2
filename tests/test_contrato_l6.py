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
   ni un PNG con el texto del error, tampoco cuando el que falla es UN PANEL
   de una figura que sí se habría guardado (dashboard, tarjeta de calidad).
5. El camino postprocesado de ``linkage()`` (matcher o
   ``collapse_exact_duplicates``) pasa por
   ``Orchestrator.ejecutar_reporting_postprocesado`` → ``_exec_phase`` (F1.13):
   el manifiesto trae ``omitidos`` junto a ``status``, tiempos, artefactos y
   ``postprocesado``, como cualquier otra fase, sin mezclar dos corridas.
6. Una estrategia añadida con ``add_reporting_strategy`` que solo cumple el
   ``ReportingStrategy`` Protocol y lanza una excepción corriente recibe el
   mismo trato que una ``BaseReportingStrategy``: opcional → omitida con
   motivo; obligatoria → ``ArtefactoObligatorioError``.

Las corridas usan ``tests/data_sintetica/dataset_sintetico_p2_extra_features.csv``
(29 filas, empresas inventadas) con ``skip_reporting=False``. Cada corrida
cuesta ≈ 20 s, así que las pruebas 1 y 4 comparten una corrida (fixture de
módulo), la 2 usa otra y la 5 una tercera por el camino postprocesado.
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from artefactos_l6 import NOMBRES_OBLIGATORIOS_L6, escribir_obligatorios_l6

from record_linkage.api import linkage
from record_linkage.pipeline.errores import ArtefactoObligatorioError, EstrategiaFallo
from record_linkage.pipeline.orchestrator import Orchestrator
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
MOTIVO_RADAR = "el radar de calidad inventó un fallo en su panel"
MOTIVO_REPORTE = "el reporte de métricas de calidad reventó a propósito"


def _correr_linkage(work_dir: Path, **extra: Any) -> dict[str, Any]:
    df = pd.read_csv(RUTA_DATASET, dtype=str, keep_default_na=False)
    fuente = extra.pop("fuente", df)
    return linkage(
        {"P2": fuente},
        work_dir=str(work_dir),
        skip_reporting=False,
        trusted_sources=set(),
        col_ciudad="CIUDAD",
        **extra,
    )


class _MetricasQueRevientan(dict):
    """``metrics`` cuyo ``get("phase_times")`` lanza: esa clave solo la lee el
    panel de rendimiento del dashboard (F1.6: ``_serie_tiempos`` →
    ``reporting._fases.tiempos_por_fase``), DENTRO de lo que antes era su
    ``try``. Así el fallo nace donde antes se capturaba y se pintaba «Error
    generando métricas»."""

    def get(self, clave: Any, valor: Any = None) -> Any:
        if clave == "phase_times":
            raise RuntimeError(MOTIVO_DASHBOARD)
        return super().get(clave, valor)


def _romper_paneles(mp: pytest.MonkeyPatch) -> None:
    """Hace fallar, desde adentro, el panel de rendimiento del dashboard y el
    radar de la tarjeta de calidad (``Axes.fill`` solo lo llama el radar en
    ``reporting/``, dentro de su ``try``; matplotlib no lo usa al crear ejes)."""
    from matplotlib.axes import Axes

    from record_linkage.reporting.dashboard import ExecutiveDashboard

    init_original = ExecutiveDashboard.__init__

    def _init_con_metricas_rotas(self: Any, *args: Any, **kwargs: Any) -> None:
        init_original(self, *args, **kwargs)
        self.metrics = _MetricasQueRevientan(self.metrics)

    def _radar_roto(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(MOTIVO_RADAR)

    mp.setattr(ExecutiveDashboard, "__init__", _init_con_metricas_rotas)
    mp.setattr(Axes, "fill", _radar_roto)


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
# Corrida compartida: un panel del dashboard, un panel de la tarjeta de calidad
# y un reporte Excel fallan. Los paneles son el caso traicionero: hasta la
# corrección cada uno capturaba su excepción, pintaba «Error generando …» y la
# figura se guardaba igual (un PNG con un error dentro que ninguna búsqueda de
# texto detecta).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def corrida_con_opcionales_rotos(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from record_linkage.reporting.reports import ReportGenerator

    work_dir = tmp_path_factory.mktemp("l6_opcionales")

    def _reporte_roto(self: Any) -> pd.DataFrame:
        raise ValueError(MOTIVO_REPORTE)

    with pytest.MonkeyPatch.context() as mp:
        _romper_paneles(mp)
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
    # El panel roto de la tarjeta de calidad (suite.py) también se omite con motivo.
    assert ("EnhancedInsightsStrategy", "tarjeta_calidad_datos.png") in por_estrategia
    assert MOTIVO_RADAR in por_estrategia[("EnhancedInsightsStrategy", "tarjeta_calidad_datos.png")]

    salida = work_dir / "L6_reporting"
    assert not (salida / "dashboard_ejecutivo.png").exists()
    assert not (salida / "tarjeta_calidad_datos.png").exists()
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
    # Un PNG no contiene la cadena como bytes: la figura cuyo panel falló NO
    # debe existir (antes se guardaba con «Error generando métricas» pintado).
    nombres = {str(p.relative_to(salida)) for p in archivos}
    assert "dashboard_ejecutivo.png" not in nombres
    assert "tarjeta_calidad_datos.png" not in nombres
    # Las figuras cuyos paneles no fallaron sí están (el fallo no es global).
    assert "heatmap_interseccion_mejorado.png" in nombres
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
    # Solo los obligatorios de la estrategia, no sus Excel opcionales.
    assert info.value.faltantes == (
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
    )

    manifiesto = _leer_manifiesto(tmp_path)
    assert manifiesto.get("L6_reporting", {}).get("status") != "DONE"
    assert manifiesto["L5_golden"]["status"] == "DONE"


# ─────────────────────────────────────────────────────────────────────────────
# Camino postprocesado: ejecutar_reporting_postprocesado → _exec_phase → manifiesto
# ─────────────────────────────────────────────────────────────────────────────


def test_camino_postprocesado_deja_omitidos_en_el_manifiesto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``collapse_exact_duplicates=True`` hace que ``linkage()`` genere L6 por
    ``ejecutar_reporting_postprocesado`` (F1.13: pasa por ``_exec_phase`` y
    queda en el manifiesto como cualquier fase). Los ``omitidos`` de F1.4
    viajan en la misma entrada, junto a ``postprocesado``."""

    def _dashboard_roto(self: Any, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise RuntimeError(MOTIVO_DASHBOARD)

    monkeypatch.setattr(strategies.DashboardStrategy, "_execute_impl", _dashboard_roto)
    df = pd.read_csv(RUTA_DATASET, dtype=str, keep_default_na=False)
    con_duplicados = pd.concat([df, df.head(3)], ignore_index=True)

    resultado = _correr_linkage(tmp_path, fuente=con_duplicados, collapse_exact_duplicates=True)

    assert resultado["preprocessing"]["exact_duplicate_collapse"]["P2"]["collapsed_rows"] == 3
    fase = _leer_manifiesto(tmp_path)["L6_reporting"]
    assert fase["status"] == "DONE"
    assert "hash" in fase and "artifacts" in fase
    # Lo de la fase (duración, RSS), lo del llamador (postprocesado) y lo que
    # L6 dejó para el manifiesto (omitidos): una sola entrada, un solo camino.
    assert set(fase["meta"]) == {"duration", "peak_rss_mib", "postprocesado", "omitidos"}
    assert fase["meta"]["postprocesado"] == ["colapso_exacto"]
    omitidos = {(e["estrategia"], e["artefacto"]): e["motivo"] for e in fase["meta"]["omitidos"]}
    assert ("DashboardStrategy", "dashboard_ejecutivo.png") in omitidos
    assert MOTIVO_DASHBOARD in omitidos[("DashboardStrategy", "dashboard_ejecutivo.png")]
    salida = tmp_path / "L6_reporting"
    assert not (salida / "dashboard_ejecutivo.png").exists()
    archivos = {Path(p).name for p in resultado["report_files"]}
    assert "dashboard_ejecutivo.png" not in archivos
    assert "golden_records.parquet" in archivos


# ─────────────────────────────────────────────────────────────────────────────
# Estrategias añadidas con add_reporting_strategy que solo cumplen el Protocol
# ─────────────────────────────────────────────────────────────────────────────


class _ExportacionDePrueba(strategies.DataExportStrategy):
    """Escribe los obligatorios sin pasar por el pipeline (como las capturas
    de tests/test_api_postprocessing.py)."""

    def execute(self, ctx: Any, logger: logging.Logger) -> list[Path]:
        return escribir_obligatorios_l6(ctx.output_dir)


class _SoloProtocolQueLanza:
    """Cumple ``ReportingStrategy`` (name + execute) sin heredar de la base."""

    name = "analítica externa"

    def execute(self, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise ValueError("fallo opcional inventado")


class _ExportacionQueLanzaSinTipar(strategies.DataExportStrategy):
    def execute(self, ctx: Any, logger: logging.Logger) -> list[Path]:
        raise OSError("fallo obligatorio sin tipar")


def _orquestador_parcial(tmp_path: Path, estrategias: list[Any]) -> Orchestrator:
    orq = object.__new__(Orchestrator)
    orq.config = {"reporting_use_checkpoints": False}
    # Directorios de todas las fases (vacíos): _build_metrics cuenta en L2/L3.
    orq.dirs = {p: tmp_path / p.value for p in strategies.Phase}
    orq._start_time = 1.0
    orq._phase_times = {}
    orq._meta_extra = {}
    orq._reporting_strategies = estrategias
    orq.log = logging.getLogger("prueba_l6")
    return orq


def _datos_minimos() -> dict[str, pd.DataFrame]:
    data = pd.DataFrame({"ID_GRUPO": [1], "SRC": ["F"]})
    return {"golden": data[["ID_GRUPO"]], "correlative": data}


def test_estrategia_de_solo_protocol_que_lanza_se_omite_con_motivo(tmp_path: Path) -> None:
    orq = _orquestador_parcial(tmp_path, [_ExportacionDePrueba(), _SoloProtocolQueLanza()])

    archivos, _ = orq._run_L6(_datos_minimos())

    assert {p.name for p in archivos} == set(NOMBRES_OBLIGATORIOS_L6)
    assert orq.l6_omitidos == [
        {
            "artefacto": "analítica externa",
            "estrategia": "_SoloProtocolQueLanza",
            "motivo": "ValueError: fallo opcional inventado",
        }
    ]
    assert orq._meta_extra["L6_reporting"] == {"omitidos": orq.l6_omitidos}


def test_estrategia_obligatoria_que_lanza_sin_tipar_falla_la_corrida(tmp_path: Path) -> None:
    orq = _orquestador_parcial(tmp_path, [_ExportacionQueLanzaSinTipar()])

    with pytest.raises(ArtefactoObligatorioError, match="fallo obligatorio sin tipar") as info:
        orq._run_L6(_datos_minimos())
    assert isinstance(info.value.__cause__, EstrategiaFallo)
    assert isinstance(info.value.__cause__.__cause__, OSError)


def test_txt_de_auditoria_que_falla_se_omite_y_el_json_obligatorio_queda(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``config_auditoria_*.txt`` es OPCIONAL: si falla (un perfil con pesos no
    numéricos, p. ej.) la corrida NO falla, el JSON obligatorio queda y la
    omisión va al manifiesto. Antes el TypeError subía como ``EstrategiaFallo``
    de una estrategia obligatoria y la corrida moría diciendo que faltaba un
    JSON que sí estaba en disco."""
    motivo = "peso de fuente no numérico (inventado)"

    def _txt_roto(self: Any, path: Path, *args: Any, **kwargs: Any) -> None:
        path.write_text("a medias", encoding="utf-8")
        raise TypeError(motivo)

    monkeypatch.setattr(strategies.ConfigAuditStrategy, "_write_txt_audit", _txt_roto)
    orq = _orquestador_parcial(tmp_path, [_ExportacionDePrueba(), strategies.ConfigAuditStrategy()])

    archivos, _ = orq._run_L6(_datos_minimos())

    salida = orq.dirs[strategies.Phase.L6_REPORTING]
    # (la exportación de prueba también deja su config_auditoria_prueba.json)
    jsons = [
        p for p in salida.glob("config_auditoria_*.json") if p.name not in NOMBRES_OBLIGATORIOS_L6
    ]
    assert len(jsons) == 1 and jsons[0].stat().st_size > 0
    assert jsons[0] in archivos
    assert list(salida.glob("config_auditoria_*.txt")) == [], "el TXT a medias debe borrarse"
    assert len(orq.l6_omitidos) == 1
    omision = orq.l6_omitidos[0]
    assert omision["estrategia"] == "ConfigAuditStrategy"
    assert omision["artefacto"] == jsons[0].with_suffix(".txt").name
    assert omision["motivo"] == f"TypeError: {motivo}"
    assert orq._meta_extra["L6_reporting"] == {"omitidos": orq.l6_omitidos}


def test_omitidos_existe_antes_de_execute_y_omitir_no_revienta() -> None:
    """Una subclase que sobreescribe ``execute`` (patrón de las capturas en
    pruebas) puede llamar a ``omitir`` sin haber pasado por el ``execute``
    de la base."""
    estrategia = _ExportacionDePrueba()
    assert estrategia.omitidos == []
    estrategia.omitir("golden_records.xlsx", "motivo inventado")
    assert [o.como_dict() for o in estrategia.omitidos] == [
        {
            "artefacto": "golden_records.xlsx",
            "estrategia": "_ExportacionDePrueba",
            "motivo": "motivo inventado",
        }
    ]
    # Dos instancias no comparten la lista.
    assert _ExportacionDePrueba().omitidos == []


def test_dependencia_no_disponible_se_omite_por_artefacto_declarado() -> None:
    """La omisión por ``is_available() == False`` se registra con los patrones
    del contrato (como hace ``_omisiones_de`` en el orquestador bajo RAM
    crítica), no con el nombre descriptivo de la estrategia: quien lea el
    manifiesto busca por nombre de archivo."""

    class _DashboardSinDependencia(strategies.DashboardStrategy):
        @property
        def required_class(self) -> str:
            return "ClaseQueNoExisteEnNingunEntorno"

    estrategia = _DashboardSinDependencia()
    assert estrategia.execute(None, logging.getLogger("prueba_l6")) == []
    assert [o.como_dict() for o in estrategia.omitidos] == [
        {
            "artefacto": patron,
            "estrategia": "_DashboardSinDependencia",
            "motivo": "clase 'ClaseQueNoExisteEnNingunEntorno' no disponible en el entorno",
        }
        for patron in contrato_l6.artefactos_de(strategies.DashboardStrategy)
    ]
    assert estrategia.omitidos[0].artefacto == "dashboard_ejecutivo.png"

    # Una estrategia que el contrato no declara sigue registrando su nombre.
    class _AjenaSinDependencia(strategies.BaseReportingStrategy):
        name = "analítica ajena"
        required_class = "ClaseQueNoExisteEnNingunEntorno"

        def _execute_impl(self, ctx: Any, logger: logging.Logger) -> list[Path]:
            return []

    ajena = _AjenaSinDependencia()
    assert ajena.execute(None, logging.getLogger("prueba_l6")) == []
    assert [o.artefacto for o in ajena.omitidos] == ["analítica ajena"]


def test_la_omision_de_un_opcional_se_registra_una_sola_vez_en_el_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``execute`` ya avisa por cada omisión; el aviso local del TXT de
    auditoría lo repetía con el mismo motivo."""
    motivo = "peso de fuente no numérico (inventado)"

    def _txt_roto(self: Any, path: Path, *args: Any, **kwargs: Any) -> None:
        raise TypeError(motivo)

    monkeypatch.setattr(strategies.ConfigAuditStrategy, "_write_txt_audit", _txt_roto)
    orq = _orquestador_parcial(tmp_path, [_ExportacionDePrueba(), strategies.ConfigAuditStrategy()])
    with caplog.at_level(logging.WARNING, logger="prueba_l6"):
        orq._run_L6(_datos_minimos())

    avisos = [r.getMessage() for r in caplog.records if motivo in r.getMessage()]
    assert len(avisos) == 1, avisos


def test_obligatorios_de_filtra_los_opcionales_de_la_estrategia() -> None:
    assert contrato_l6.obligatorios_de("DataExportStrategy") == (
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
    )
    assert contrato_l6.obligatorios_de(_ExportacionDePrueba()) == contrato_l6.obligatorios_de(
        strategies.DataExportStrategy
    )
    assert contrato_l6.obligatorios_de("ConfigAuditStrategy") == ("config_auditoria_*.json",)
    assert contrato_l6.obligatorios_de(strategies.DashboardStrategy()) == ()
    assert contrato_l6.obligatorios_de(_SoloProtocolQueLanza()) == ()
    # artefactos_de sigue devolviendo todo (es lo que usa _omisiones_de).
    assert set(contrato_l6.obligatorios_de("DataExportStrategy")) < set(
        contrato_l6.artefactos_de("DataExportStrategy")
    )


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
    # (archivo, motivo), como visualizer/suite: quien etiqueta la estrategia es
    # la estrategia, no el generador.
    omitidos = dict(generador.omitidos)
    assert MOTIVO_REPORTE in omitidos["reporte_metricas_calidad.xlsx"]
    # F1.6: sin metrics["phase_times"] el reporte de performance no tiene filas
    # y tampoco se escribe; esa omisión también queda registrada, con su motivo,
    # en vez de un Excel vacío o con tiempos inventados.
    assert set(omitidos) == {
        "reporte_metricas_calidad.xlsx",
        "reporte_metricas_performance.xlsx",
    }
    assert omitidos["reporte_metricas_performance.xlsx"] == "el generador no devolvió filas"


def test_excel_strategy_etiqueta_las_omisiones_del_generador_con_su_propia_clase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from record_linkage.reporting.reports import ReportGenerator

    class ReportesPersonalizados(strategies.ExcelReportsStrategy):
        pass

    def _roto(self: Any) -> pd.DataFrame:
        raise ValueError(MOTIVO_REPORTE)

    monkeypatch.setattr(ReportGenerator, "_generate_quality_metrics", _roto)
    ctx = strategies.ReportingContext(
        golden_df=pd.DataFrame({"ID_GRUPO": [1, 2], "CONFIDENCE_SCORE": [0.9, 0.5]}),
        correlative_df=pd.DataFrame(
            {"ID_GRUPO": [1, 1, 2], "SRC": ["A", "B", "A"], "CONFIDENCE_SCORE": [0.9, 0.9, 0.5]}
        ),
        config={},
        output_dir=tmp_path,
        metrics={},
        start_time=0.0,
    )
    estrategia = ReportesPersonalizados()
    estrategia.execute(ctx, logging.getLogger("prueba"))

    omision = next(o for o in estrategia.omitidos if o.artefacto == "reporte_metricas_calidad.xlsx")
    assert isinstance(omision, ArtefactoOmitido)
    assert omision.estrategia == "ReportesPersonalizados"
    assert MOTIVO_REPORTE in omision.motivo


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


def test_un_panel_del_dashboard_que_falla_no_deja_png(tmp_path: Path) -> None:
    """El panel de rendimiento capturaba su excepción, pintaba «Error generando
    métricas» y la figura se guardaba igual: un PNG con un error dentro que
    ninguna búsqueda de texto detecta. El fallo nace dentro del panel
    (``self.metrics.get("phase_times")``), no en un método parcheado."""
    from record_linkage.reporting.dashboard import ExecutiveDashboard

    correl = pd.DataFrame({"ID_GRUPO": [1, 1], "SRC": ["A", "B"], "CONFIDENCE_SCORE": [0.9, 0.9]})
    golden = pd.DataFrame({"ID_GRUPO": [1], "CONFIDENCE_SCORE": [0.9]})
    tablero = ExecutiveDashboard(
        correlative_data=correl,
        golden_records_data=golden,
        # El único lector de «phase_times» en el dashboard es el panel de
        # rendimiento (F1.6); el resto de métricas se leen por otras claves.
        metrics={"total_records": 2, "execution_time": 1.0},
    )
    tablero.metrics = _MetricasQueRevientan(tablero.metrics)

    destino = tmp_path / "dashboard_ejecutivo.png"
    with pytest.raises(RuntimeError, match="inventó"):
        tablero.generate_dashboard(str(destino))
    assert not destino.exists()


def test_un_panel_de_la_tarjeta_de_calidad_que_falla_se_omite_con_motivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo mismo para suite.py: el radar pintaba «Error generando radar» y la
    tarjeta se guardaba. Ahora la tarjeta no existe y queda en ``omitidos``."""
    from record_linkage.reporting.suite import EnhancedReportingSuite

    _romper_paneles(monkeypatch)
    correl = pd.DataFrame(
        {"ID_GRUPO": [1, 1, 2], "SRC": ["A", "B", "A"], "CONFIDENCE_SCORE": [0.9, 0.9, 0.5]}
    )
    golden = pd.DataFrame({"ID_GRUPO": [1, 2], "CONFIDENCE_SCORE": [0.9, 0.5]})
    suite = EnhancedReportingSuite(
        correlative_data=correl, golden_records_data=golden, metrics={"total_records": 3}
    )

    with pytest.raises(RuntimeError, match="radar"):
        suite.generate_quality_card(str(tmp_path))
    assert not (tmp_path / "tarjeta_calidad_datos.png").exists()

    suite.generate_all_enhanced_reports(str(tmp_path))
    omitidos = dict(suite.omitidos)
    assert "tarjeta_calidad_datos.png" in omitidos
    assert MOTIVO_RADAR in omitidos["tarjeta_calidad_datos.png"]
    assert not (tmp_path / "tarjeta_calidad_datos.png").exists()
