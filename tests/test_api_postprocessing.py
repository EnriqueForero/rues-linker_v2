"""Regresiones de colapso, expansión, matcher y reporting postprocesado."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.api import _collapse_exact_sources, _expand_exact_correlative
from record_linkage.matching import apply_matcher_to_linkage_result
from record_linkage.pipeline.errores import ArtefactoObligatorioError
from record_linkage.pipeline.orchestrator import Orchestrator
from record_linkage.reporting.strategies import DataExportStrategy, Phase


def test_collapse_exacto_preserva_orden_nulos_e_indices_externos():
    frame = pd.DataFrame(
        {
            "NIT": pd.Series(["01", pd.NA, "01", pd.NA], dtype="string[pyarrow]"),
            "RAZON_SOCIAL": ["A", "SIN NIT", "A", "SIN NIT"],
        },
        index=[90, 10, 70, 20],
    )

    compact, plan = _collapse_exact_sources({"FUENTE": frame})

    assert compact["FUENTE"].reset_index(drop=True).equals(frame.iloc[:2].reset_index(drop=True))
    np.testing.assert_array_equal(plan["codes"]["FUENTE"], [0, 1, 0, 1])
    assert plan["stats"]["FUENTE"] == {
        "input_rows": 4,
        "processed_rows": 2,
        "collapsed_rows": 2,
    }


def test_collapse_exacto_no_fusiona_una_colision_de_hash(monkeypatch):
    """Una colisión deliberada debe activar el camino exacto, no perder filas."""
    frame = pd.DataFrame({"NIT": ["1", "2", "1"], "RAZON_SOCIAL": ["A", "B", "A"]})

    def constant_hash(obj, **_kwargs):
        return pd.Series(np.zeros(len(obj), dtype=np.uint64), index=obj.index)

    monkeypatch.setattr(pd.util, "hash_pandas_object", constant_hash)
    compact, plan = _collapse_exact_sources({"F": frame})

    assert compact["F"].to_dict("records") == [
        {"NIT": "1", "RAZON_SOCIAL": "A"},
        {"NIT": "2", "RAZON_SOCIAL": "B"},
    ]
    np.testing.assert_array_equal(plan["codes"]["F"], [0, 1, 0])


def test_expansion_restaura_orden_global_y_asignacion_de_grupos():
    # La correlativa compacta llega deliberadamente desordenada.
    compact_corr = pd.DataFrame(
        {
            "SRC": ["A", "B", "A"],
            "ORIGINAL_INDEX": [1, 2, 0],
            "ID_GRUPO": [20, 30, 10],
            "NIT_FINAL": ["2", "3", "1"],
        }
    )
    plan = {
        "codes": {"A": np.array([0, 1, 0]), "B": np.array([0, 0])},
        "stats": {
            "A": {"input_rows": 3, "processed_rows": 2, "collapsed_rows": 1},
            "B": {"input_rows": 2, "processed_rows": 1, "collapsed_rows": 1},
        },
    }

    expanded = _expand_exact_correlative(compact_corr, ["A", "B"], plan)

    assert expanded["ORIGINAL_INDEX"].tolist() == [0, 1, 2, 3, 4]
    assert expanded["SRC"].tolist() == ["A", "A", "A", "B", "B"]
    assert expanded["ID_GRUPO"].tolist() == [10, 20, 10, 30, 30]


def test_linkage_colapsa_para_procesar_y_restituye_cada_fila_de_entrada(tmp_path):
    source = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "900123456", "800000001"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME SAS", "ACME SAS", "BETA SAS"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "BOGOTA", "CALI"],
        },
        index=[100, 200, 300, 400],
    )
    original = source.copy(deep=True)

    result = linkage(
        {"RUES": source},
        work_dir=str(tmp_path / "run"),
        skip_reporting=True,
        collapse_exact_duplicates=True,
    )

    corr = result["correlative"]
    assert len(corr) == len(source)
    assert corr["ORIGINAL_INDEX"].tolist() == [0, 1, 2, 3]
    assert corr.loc[:2, "ID_GRUPO"].nunique() == 1
    assert result["preprocessing"]["exact_duplicate_collapse"]["RUES"] == {
        "input_rows": 4,
        "processed_rows": 2,
        "collapsed_rows": 2,
    }
    acme_gid = corr.loc[0, "ID_GRUPO"]
    assert result["golden"].set_index("ID_GRUPO").loc[acme_gid, "INPUT_ROW_COUNT"] == 3
    pd.testing.assert_frame_equal(source, original)


def test_matcher_helper_recalcula_golden_canonico_y_preserva_metadatos():
    corr = pd.DataFrame(
        {
            "ID_REGISTRO": ["C1", "R1"],
            "ID_GRUPO": [7, 7],
            "NIT": ["900123456", "900123456"],
            # La primera fila es deliberadamente el valor de peor calidad.
            "RAZON_SOCIAL": ["NO DEFINIDO", "ACME COLOMBIA SAS"],
            "SRC": ["CRM", "RUES"],
            "ORIGINAL_INDEX": [0, 1],
            "CIUDAD": ["BOGOTA", "BOGOTA"],
            "TELEFONO": ["3001111111", "3001111111"],
            "EMAIL": ["info@acme.co", "info@acme.co"],
            "DIRECCION": ["CRA 7", "CRA 7"],
        }
    )
    original = {"golden": pd.DataFrame(), "correlative": corr, "trace_id": "run-123"}

    refined = apply_matcher_to_linkage_result(
        original,
        corr.copy(),
        source_priority=["RUES", "CRM"],
    )

    assert refined["trace_id"] == "run-123"
    assert refined["golden"].loc[0, "RAZON_SOCIAL_FINAL"] == "ACME COLOMBIA SAS"
    assert refined["golden"].loc[0, "PRIMARY_SOURCE"] == "RUES"
    assert len(refined["correlative"]) == 2


def _escribir_obligatorios(output_dir):
    """F1.4: L6 exige los artefactos obligatorios por nombre exacto; una
    estrategia de captura debe dejarlos para que la corrida cuente como válida."""
    nombres = [
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
        "config_auditoria_prueba.json",
    ]
    rutas = []
    for nombre in nombres:
        ruta = output_dir / nombre
        ruta.write_bytes(b"x")
        rutas.append(ruta)
    return rutas


class _CaptureExport(DataExportStrategy):
    def __init__(self, seen: dict[str, int]) -> None:
        self.seen = seen

    def execute(self, ctx, logger):
        self.seen["export_golden"] = len(ctx.golden_df)
        self.seen["export_correlative"] = len(ctx.correlative_df)
        self.seen["metric_total"] = ctx.metrics["total_records"]
        return _escribir_obligatorios(ctx.output_dir)


class _CaptureAnalytics:
    name = "captura analítica"

    def __init__(self, seen: dict[str, int]) -> None:
        self.seen = seen

    def execute(self, ctx, logger):
        self.seen["analytics_golden"] = len(ctx.golden_df)
        self.seen["analytics_correlative"] = len(ctx.correlative_df)
        return []


class _SilentLog:
    def info(self, *_args, **_kwargs):
        pass

    warning = info
    error = info


def test_build_metrics_admite_instancia_parcial_y_copia_picos_rss() -> None:
    """Compatibilidad con fixtures antiguos sin relajar el contrato de tipos."""

    orchestrator = object.__new__(Orchestrator)
    orchestrator._start_time = None
    orchestrator._phase_times = {}
    data = pd.DataFrame({"ID_GRUPO": [1]})

    metrics = orchestrator._build_metrics(data, data)

    assert metrics["peak_rss_mib_by_phase"] == {}

    orchestrator._phase_peak_rss_mib = {"L1_INGESTION": 12.5}
    metrics = orchestrator._build_metrics(data, data)
    metrics["peak_rss_mib_by_phase"]["L1_INGESTION"] = 99.0
    assert orchestrator._phase_peak_rss_mib == {"L1_INGESTION": 12.5}


def test_build_metrics_no_oculta_un_estado_rss_invalido() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator._start_time = None
    orchestrator._phase_times = {}
    orchestrator._phase_peak_rss_mib = None
    data = pd.DataFrame({"ID_GRUPO": [1]})

    with pytest.raises(TypeError, match="_phase_peak_rss_mib"):
        orchestrator._build_metrics(data, data)


def test_l6_no_trunca_exportacion_postprocesada_bajo_presion_de_ram(tmp_path, monkeypatch):
    """La muestra de analítica no debe convertirse en el artefacto exportado."""
    n_rows = 50_003
    golden = pd.DataFrame({"ID_GRUPO": np.arange(n_rows, dtype=np.int64)})
    correlative = pd.DataFrame({"ID_GRUPO": np.arange(n_rows, dtype=np.int64), "SRC": "FUENTE"})
    seen: dict[str, int] = {}

    orchestrator = object.__new__(Orchestrator)
    orchestrator.config = {"reporting_use_checkpoints": False}
    orchestrator.dirs = {Phase.L6_REPORTING: tmp_path / "reports"}
    orchestrator._start_time = 1.0
    orchestrator._phase_times = {}
    orchestrator._meta_extra = {}
    orchestrator._reporting_strategies = [_CaptureExport(seen), _CaptureAnalytics(seen)]
    orchestrator.log = _SilentLog()
    monkeypatch.setattr(
        "record_linkage.pipeline.orchestrator.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=90.0),
    )

    orchestrator._run_L6({"golden": golden, "correlative": correlative})

    assert seen == {
        "export_golden": n_rows,
        "export_correlative": n_rows,
        "metric_total": n_rows,
        "analytics_golden": 50_000,
        "analytics_correlative": 50_000,
    }
    assert len(golden) == n_rows
    assert len(correlative) == n_rows


def test_l6_intenta_exportar_aun_con_ram_critica_y_omite_solo_analitica(tmp_path, monkeypatch):
    seen: dict[str, int] = {}
    orchestrator = object.__new__(Orchestrator)
    orchestrator.config = {"reporting_use_checkpoints": False}
    orchestrator.dirs = {Phase.L6_REPORTING: tmp_path / "reports"}
    orchestrator._start_time = 1.0
    orchestrator._phase_times = {}
    orchestrator._meta_extra = {}
    orchestrator._reporting_strategies = [_CaptureExport(seen), _CaptureAnalytics(seen)]
    orchestrator.log = _SilentLog()
    monkeypatch.setattr(
        "record_linkage.pipeline.orchestrator.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=96.0),
    )
    data = pd.DataFrame({"ID_GRUPO": [1], "SRC": ["F"]})

    orchestrator._run_L6({"golden": data[["ID_GRUPO"]], "correlative": data})

    assert seen["export_golden"] == 1
    assert seen["export_correlative"] == 1
    assert "analytics_golden" not in seen
    # F1.4: la analítica saltada por RAM queda registrada, no desaparece.
    assert orchestrator.l6_omitidos == [
        {
            "artefacto": "captura analítica",
            "estrategia": "_CaptureAnalytics",
            "motivo": "RAM crítica (96.0 %): se omitió para proteger la corrida",
        }
    ]


def test_l6_no_declara_exito_si_data_export_no_produce_artefactos(tmp_path, monkeypatch):
    strategy = DataExportStrategy()
    monkeypatch.setattr(strategy, "execute", lambda _ctx, _logger: [])
    orchestrator = object.__new__(Orchestrator)
    orchestrator.config = {"reporting_use_checkpoints": False}
    orchestrator.dirs = {Phase.L6_REPORTING: tmp_path / "reports"}
    orchestrator._start_time = 1.0
    orchestrator._phase_times = {}
    orchestrator._meta_extra = {}
    orchestrator._reporting_strategies = [strategy]
    orchestrator.log = _SilentLog()
    monkeypatch.setattr(
        "record_linkage.pipeline.orchestrator.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=20.0),
    )
    data = pd.DataFrame({"ID_GRUPO": [1], "SRC": ["F"]})

    # F1.4: la verificación es por nombre exacto y la excepción es tipada.
    with pytest.raises(ArtefactoObligatorioError, match=r"golden_records\.parquet") as info:
        orchestrator._run_L6({"golden": data[["ID_GRUPO"]], "correlative": data})
    assert info.value.faltantes == (
        "tabla_correlativa.parquet",
        "tabla_correlativa.csv.gz",
        "golden_records.parquet",
        "golden_records.csv.gz",
        "config_auditoria_*.json",
    )
