"""Tiempos por fase en dashboard, reportes y visualizador (tarea F1.6).

Qué prueba
----------
Hasta F1.6, ``dashboard.py``, ``reports.py``, ``visualizer.py`` y ``suite.py``
buscaban claves (``load_validate``, ``preprocessing_time``, ``scoring_time``…)
que el orquestador nunca entregó: ``_build_metrics`` entrega
``metrics["phase_times"]`` con las claves de ``Phase`` (``L1_prep`` …
``L5_golden``). Al no encontrar nada, el dashboard INVENTABA porcentajes
fijos sobre el tiempo total y el visualizador devolvía ``None`` (nunca se
generó ``performance_timeline.png``).

Desde F1.6 hay UNA sola lectura de esos tiempos —
``reporting._fases.tiempos_por_fase`` — y una sola tabla de etiquetas
humanas, ``ETIQUETAS_FASE``. Estas pruebas fijan que:

* los tiempos que llegan a los tres consumidores son EXACTAMENTE los de
  ``phase_times`` (ni escalados, ni redistribuidos);
* sin tiempos no se inventa nada: el gráfico dice «sin tiempos por fase», el
  reporte Excel se omite y el visualizador no escribe el PNG;
* de punta a punta, ``linkage(..., skip_reporting=False)`` genera
  ``performance_timeline.png`` y ``reporte_metricas_performance.xlsx`` trae
  los mismos segundos que ``manifest.json``.

Costo: la prueba de punta a punta corre ``linkage()`` una vez sobre el
dataset sintético de 28 filas con L6 activo (≈ 15–20 s, casi todo figuras).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from record_linkage.pipeline.errores import ErrorPipeline, TiemposPorFaseError
from record_linkage.reporting._fases import (
    ETIQUETAS_FASE,
    MENSAJE_SIN_TIEMPOS,
    tiempos_por_fase,
)
from record_linkage.reporting.strategies import Phase

RAIZ = Path(__file__).resolve().parent
RUTA_DATASET = RAIZ / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"

TIEMPOS = {
    "L1_prep": 1.2,
    "L2_lsh_candidates": 40.8,
    "L3_scoring": 12.5,
    "L4_clustering": 0.7,
    "L5_golden": 3.1,
}


def _metricas(phase_times: dict[str, float] | None, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "total_records": 1000,
        "unique_groups": 800,
        "execution_time": 120.0,
        "candidates_found": 5000,
        "pairs_scored": 4000,
    }
    if phase_times is not None:
        base["phase_times"] = phase_times
    base.update(extra)
    return base


def _marcos_vacios() -> tuple[pd.DataFrame, pd.DataFrame]:
    correlativa = pd.DataFrame(columns=["ID_GRUPO", "SRC", "NIT", "RAZON_SOCIAL"])
    golden = pd.DataFrame(columns=["ID_GRUPO", "NIT", "RAZON_SOCIAL", "CONFIDENCE_SCORE"])
    return correlativa, golden


def _exigir_matplotlib() -> None:
    faltan = [m for m in ("matplotlib", "seaborn") if importlib.util.find_spec(m) is None]
    if faltan:
        pytest.fail(f"Faltan {faltan}: instale el extra [viz] (pip install -e '.[dev]').")


# ─────────────────────────────────────────────────────────────────────────────
# 1. La única lectura: tiempos_por_fase y ETIQUETAS_FASE
# ─────────────────────────────────────────────────────────────────────────────


def test_etiquetas_cubren_exactamente_las_fases_del_pipeline() -> None:
    assert set(ETIQUETAS_FASE) == {fase.value for fase in Phase}
    assert list(ETIQUETAS_FASE) == [fase.value for fase in Phase], "orden L1…L6"
    for clave, etiqueta in ETIQUETAS_FASE.items():
        assert etiqueta.startswith(clave.split("_")[0] + " · "), (clave, etiqueta)


def test_tiempos_por_fase_devuelve_exactamente_phase_times() -> None:
    assert tiempos_por_fase(_metricas(TIEMPOS)) == TIEMPOS
    assert list(tiempos_por_fase(_metricas(TIEMPOS))) == list(TIEMPOS)


def test_tiempos_por_fase_acepta_L6_si_viene_y_ordena_por_fase() -> None:
    desordenado = {"L6_reporting": 9.0, "L3_scoring": 2.0, "L1_prep": 1.0}
    salida = tiempos_por_fase(_metricas(desordenado))
    assert salida == {"L1_prep": 1.0, "L3_scoring": 2.0, "L6_reporting": 9.0}
    assert list(salida) == ["L1_prep", "L3_scoring", "L6_reporting"]


@pytest.mark.parametrize("metrics", [_metricas(None), _metricas({}), {}, {"phase_times": None}])
def test_tiempos_por_fase_sin_tiempos_no_inventa_nada(metrics: dict[str, Any]) -> None:
    """Con execution_time=120 y sin phase_times, antes se repartía 15/10/25…%."""
    assert tiempos_por_fase(metrics) == {}


def test_tiempos_por_fase_rechaza_fase_desconocida() -> None:
    with pytest.raises(TiemposPorFaseError, match="ETIQUETAS_FASE") as info:
        tiempos_por_fase(_metricas({"L1_prep": 1.0, "load_validate": 2.0}))
    assert isinstance(info.value, ErrorPipeline)
    assert "Qué hacer" in str(info.value)


def test_tiempos_por_fase_rechaza_valor_no_numerico() -> None:
    with pytest.raises(TiemposPorFaseError, match="L2_lsh_candidates"):
        tiempos_por_fase(_metricas({"L2_lsh_candidates": "40.8"}))


def test_tiempos_por_fase_rechaza_lo_que_no_es_un_mapeo() -> None:
    with pytest.raises(TiemposPorFaseError, match="mapeo"):
        tiempos_por_fase(_metricas([("L1_prep", 1.0)]))  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dashboard
# ─────────────────────────────────────────────────────────────────────────────


def test_dashboard_serie_tiempos_es_exactamente_phase_times() -> None:
    from record_linkage.reporting.dashboard import _serie_tiempos

    serie = _serie_tiempos(_metricas(TIEMPOS))
    assert list(serie.index) == [ETIQUETAS_FASE[k] for k in TIEMPOS]
    assert serie.tolist() == list(TIEMPOS.values())


def test_dashboard_serie_tiempos_vacia_sin_phase_times() -> None:
    from record_linkage.reporting.dashboard import _serie_tiempos

    assert _serie_tiempos(_metricas(None, execution_time=500.0)).empty
    assert _serie_tiempos(_metricas({}, total_time=500.0)).empty


def test_dashboard_dibuja_los_tiempos_reales_o_dice_que_no_hay() -> None:
    _exigir_matplotlib()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from record_linkage.reporting.dashboard import ExecutiveDashboard

    correlativa, golden = _marcos_vacios()

    con_tiempos = ExecutiveDashboard(correlativa, golden, metrics=_metricas(TIEMPOS))
    fig, ax = plt.subplots()
    con_tiempos._plot_performance_metrics(ax)
    anchos = sorted(p.get_width() for p in ax.patches)
    assert anchos == sorted(TIEMPOS.values())
    plt.close(fig)

    sin_tiempos = ExecutiveDashboard(correlativa, golden, metrics=_metricas(None))
    fig, ax = plt.subplots()
    sin_tiempos._plot_performance_metrics(ax)
    assert len(ax.patches) == 0, "sin phase_times no se dibuja ninguna barra"
    textos = [t.get_text() for t in ax.texts]
    assert any(MENSAJE_SIN_TIEMPOS in t for t in textos), textos
    plt.close(fig)


def test_dashboard_tiempo_total_de_respaldo_suma_las_fases_reales() -> None:
    _exigir_matplotlib()
    from record_linkage.reporting.dashboard import ExecutiveDashboard

    correlativa, golden = _marcos_vacios()
    dash = ExecutiveDashboard(correlativa, golden, metrics=_metricas(TIEMPOS, execution_time=0))
    assert dash._calculate_execution_time() == pytest.approx(sum(TIEMPOS.values()))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Reportes Excel
# ─────────────────────────────────────────────────────────────────────────────


def test_reporte_metricas_performance_trae_exactamente_los_tiempos() -> None:
    from record_linkage.reporting.reports import ReportGenerator

    correlativa, golden = _marcos_vacios()
    generador = ReportGenerator(correlativa, golden, metrics=_metricas(TIEMPOS), config={})
    df = generador.reporte_metricas_performance()

    fases = df[df["Clave"].isin(TIEMPOS)]
    assert fases["Clave"].tolist() == list(TIEMPOS)
    assert fases["Fase"].tolist() == [ETIQUETAS_FASE[k] for k in TIEMPOS]
    assert fases["Segundos"].tolist() == list(TIEMPOS.values())
    total = df[df["Clave"] == "TOTAL"]
    assert len(total) == 1
    assert total["Segundos"].iloc[0] == pytest.approx(sum(TIEMPOS.values()))
    assert "Error" not in df.columns


def test_reporte_metricas_performance_se_omite_sin_tiempos() -> None:
    from record_linkage.reporting.reports import ReportGenerator

    correlativa, golden = _marcos_vacios()
    generador = ReportGenerator(
        correlativa, golden, metrics=_metricas(None, execution_time=500.0), config={}
    )
    assert generador.reporte_metricas_performance().empty
    assert "metricas_performance" not in generador.generate_all_reports()


def test_generate_all_reports_relanza_tiempos_malformados() -> None:
    """Un phase_times con claves viejas es un defecto del productor: el
    `except Exception` de generate_all_reports no lo vuelve «reporte omitido»
    (ni, como antes de F1.4, un Excel con «Error» dentro): sube."""
    from record_linkage.reporting.reports import ReportGenerator

    correlativa, golden = _marcos_vacios()
    generador = ReportGenerator(
        correlativa, golden, metrics=_metricas({"load_validate": 2.0}), config={}
    )
    with pytest.raises(TiemposPorFaseError, match="load_validate"):
        generador.generate_all_reports()
    # Las omisiones legítimas (marcos vacíos → «datos insuficientes») sí quedan;
    # el fallo de tiempos no se registra como una más.
    assert all("metricas_performance" not in archivo for archivo, _ in generador.omitidos)
    assert all("TiemposPorFaseError" not in motivo for _, motivo in generador.omitidos)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Visualizador y suite
# ─────────────────────────────────────────────────────────────────────────────


def test_plot_performance_timeline_devuelve_figura_con_tiempos(tmp_path: Path) -> None:
    _exigir_matplotlib()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from record_linkage.reporting.visualizer import DataVisualizer

    correlativa, golden = _marcos_vacios()
    viz = DataVisualizer(correlativa, golden, metrics=_metricas(TIEMPOS))
    fig = viz.plot_performance_timeline()
    assert fig is not None
    gantt = fig.axes[0]
    anchos = sorted(p.get_width() for p in gantt.patches)
    # barh(left=pos) reconstruye el ancho como derecha - izquierda: igualdad
    # hasta el redondeo flotante de matplotlib, no del pipeline.
    assert anchos == pytest.approx(sorted(TIEMPOS.values()))
    plt.close(fig)

    archivos = viz.save_all_visualizations(str(tmp_path))
    assert "performance_timeline.png" in archivos
    assert (tmp_path / "performance_timeline.png").is_file()


def test_plot_performance_timeline_sin_tiempos_se_omite(tmp_path: Path, caplog) -> None:
    _exigir_matplotlib()
    import matplotlib

    matplotlib.use("Agg")

    from record_linkage.reporting.visualizer import DataVisualizer

    correlativa, golden = _marcos_vacios()
    viz = DataVisualizer(correlativa, golden, metrics=_metricas(None, execution_time=500.0))
    assert viz.plot_performance_timeline() is None
    archivos = viz.save_all_visualizations(str(tmp_path))
    assert "performance_timeline.png" not in archivos
    assert not (tmp_path / "performance_timeline.png").exists()


def test_save_all_visualizations_relanza_tiempos_malformados(tmp_path: Path) -> None:
    _exigir_matplotlib()
    import matplotlib

    matplotlib.use("Agg")

    from record_linkage.reporting.visualizer import DataVisualizer

    correlativa, golden = _marcos_vacios()
    viz = DataVisualizer(correlativa, golden, metrics=_metricas({"load_validate": 2.0}))
    with pytest.raises(TiemposPorFaseError, match="load_validate"):
        viz.save_all_visualizations(str(tmp_path))
    assert not (tmp_path / "performance_timeline.png").exists()


def test_suite_dibuja_tiempos_reales_o_dice_que_no_hay() -> None:
    _exigir_matplotlib()
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from record_linkage.reporting.suite import EnhancedReportingSuite

    correlativa, golden = _marcos_vacios()
    suite = EnhancedReportingSuite(correlativa, golden, metrics=_metricas(TIEMPOS))
    fig, ax = plt.subplots()
    suite._plot_performance_metrics(ax)
    assert sorted(p.get_width() for p in ax.patches) == sorted(TIEMPOS.values())
    plt.close(fig)

    suite = EnhancedReportingSuite(correlativa, golden, metrics=_metricas(None))
    fig, ax = plt.subplots()
    suite._plot_performance_metrics(ax)
    assert len(ax.patches) == 0
    assert any(MENSAJE_SIN_TIEMPOS in t.get_text() for t in ax.texts)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Punta a punta: linkage() con L6 activo
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def corrida_con_l6(tmp_path_factory: pytest.TempPathFactory) -> Path:
    _exigir_matplotlib()
    from record_linkage.api import linkage

    df = pd.read_csv(RUTA_DATASET, dtype=str, keep_default_na=False)
    work_dir = tmp_path_factory.mktemp("tiempos_l6")
    linkage(
        {"P2": df},
        work_dir=str(work_dir),
        skip_reporting=False,
        trusted_sources=set(),
        col_ciudad="CIUDAD",
    )
    return work_dir


def test_punta_a_punta_genera_performance_timeline(corrida_con_l6: Path) -> None:
    png = corrida_con_l6 / "L6_reporting" / "visualizaciones" / "performance_timeline.png"
    assert png.is_file() and png.stat().st_size > 0


def test_punta_a_punta_excel_trae_los_tiempos_del_manifiesto(corrida_con_l6: Path) -> None:
    manifest = json.loads((corrida_con_l6 / "manifest.json").read_text(encoding="utf-8"))
    xlsx = corrida_con_l6 / "L6_reporting" / "reporte_metricas_performance.xlsx"
    assert xlsx.is_file()
    df = pd.read_excel(xlsx)

    fases = df[df["Clave"].isin(ETIQUETAS_FASE)].set_index("Clave")
    # L6 aún corre cuando se escribe el reporte: el manifiesto lo tiene, el Excel no.
    esperadas = [f.value for f in Phase if f is not Phase.L6_REPORTING]
    assert fases.index.tolist() == esperadas
    for clave in esperadas:
        duracion = manifest[clave]["meta"]["duration"]
        assert fases.loc[clave, "Segundos"] == pytest.approx(duracion, rel=1e-6), clave
        assert fases.loc[clave, "Fase"] == ETIQUETAS_FASE[clave]
    assert "Error" not in df.columns
