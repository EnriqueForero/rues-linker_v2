"""F1.5 · Los reportes L6 cuentan sobre la tabla completa, no sobre una muestra.

Hasta esta tarea ``ReportGenerator`` cargaba una muestra de ``report_sample_size``
(100.000) filas y calculaba ``analisis_fuentes``, ``estadisticas_grupos`` y las
cifras de calidad del resumen sobre ella: a 139k registros el reporte decía
«total 99.997». Estas pruebas exigen que los totales por fuente, el total de
registros, el número de grupos y el N del resumen ejecutivo sean EXACTAMENTE
los de la correlativa completa, venga como DataFrame, parquet o SQLite; y que
lo único que se muestrea (los casos de revisión ilustrativos) lo declare con
una columna ``ALCANCE`` y en ``metrics["muestras"]``.

Las empresas son inventadas (``EMPRESA INVENTADA n``); ningún dato real.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.reporting.reports import ReportGenerator

FUENTES = {"RUES_SINT": 70_000, "ADUANA_SINT": 40_000, "CRM_SINT": 10_000}
N_FILAS = sum(FUENTES.values())  # 120.000 > 100.000 (la muestra vieja)
# 94.000 singletons + 6.000 pares + 3.000 tríos + 1.000 quintetos
# = 120.000 filas y 104.000 grupos (> 100.000: el golden tampoco cabe en la muestra).
TAMANOS_GRUPO = {1: 94_000, 2: 6_000, 3: 3_000, 5: 1_000}
N_GRUPOS = sum(TAMANOS_GRUPO.values())


def _construir_correlativa() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    # Ids de grupo únicos por tamaño: desplazamiento acumulado.
    desplazamientos = np.cumsum([0, *TAMANOS_GRUPO.values()])[:-1]
    ids = np.concatenate(
        [
            np.repeat(np.arange(n) + d, t)
            for (t, n), d in zip(TAMANOS_GRUPO.items(), desplazamientos, strict=True)
        ]
    )
    assert len(ids) == N_FILAS
    ids = ids[rng.permutation(N_FILAS)]
    src = np.repeat(list(FUENTES), list(FUENTES.values()))
    return pd.DataFrame(
        {
            "ID_GRUPO": ids.astype("int64"),
            "SRC": src,
            "NIT": (900_000_000 + ids).astype(str),
            "RAZON_SOCIAL": ("EMPRESA INVENTADA " + pd.Series(ids).astype(str) + " SAS").to_numpy(),
            "ORIGINAL_INDEX": np.arange(N_FILAS, dtype="int64"),
        }
    )


def _construir_golden(correlativa: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    por_grupo = correlativa.groupby("ID_GRUPO").agg(
        RECORD_COUNT=("SRC", "size"),
        SOURCES_COUNT=("SRC", "nunique"),
        PRIMARY_SOURCE=("SRC", "first"),
        NIT_FINAL=("NIT", "first"),
        RAZON_SOCIAL_FINAL=("RAZON_SOCIAL", "first"),
    )
    golden = por_grupo.reset_index()
    golden["CONFIDENCE_SCORE"] = rng.uniform(0.3, 1.0, size=len(golden)).round(3)
    golden["NAME_VARIATIONS"] = 1
    golden["NIT_VARIATIONS"] = 1
    return golden


@pytest.fixture(scope="module")
def correlativa() -> pd.DataFrame:
    return _construir_correlativa()


@pytest.fixture(scope="module")
def golden(correlativa: pd.DataFrame) -> pd.DataFrame:
    return _construir_golden(correlativa)


def _reportes(correlativa, golden, metrics=None, config=None) -> dict[str, pd.DataFrame]:
    generador = ReportGenerator(
        correlative_data=correlativa,
        golden_records_data=golden,
        metrics={} if metrics is None else metrics,
        config=config or {},
    )
    return generador.generate_all_reports()


def _valor_resumen(resumen: pd.DataFrame, metrica: str) -> str:
    fila = resumen[resumen["Métrica"] == metrica]
    assert len(fila) == 1, f"métrica {metrica!r} ausente o repetida en el resumen"
    return str(fila["Valor"].iloc[0])


# ─────────────────────────────────────────────────────────────────────
#  Totales exactos sobre la tabla completa (DataFrame en memoria)
# ─────────────────────────────────────────────────────────────────────


def test_datos_de_prueba_superan_la_muestra_vieja(correlativa, golden):
    assert len(correlativa) == N_FILAS > 100_000
    assert correlativa["SRC"].value_counts().to_dict() == FUENTES
    assert len(golden) == N_GRUPOS > 100_000


def test_analisis_fuentes_cuenta_toda_la_correlativa(correlativa, golden):
    reportes = _reportes(correlativa, golden)
    fuentes = reportes["analisis_fuentes"].set_index("Fuente")

    assert fuentes["Total_Registros"].to_dict() == FUENTES
    assert int(fuentes["Total_Registros"].sum()) == N_FILAS
    esperado_grupos = correlativa.groupby("SRC")["ID_GRUPO"].nunique().to_dict()
    assert fuentes["Grupos_Unicos"].to_dict() == esperado_grupos
    assert "ALCANCE" not in fuentes.columns


def test_estadisticas_grupos_cuenta_todos_los_grupos(correlativa, golden):
    reportes = _reportes(correlativa, golden)
    estadisticas = reportes["estadisticas_grupos"]
    totales = estadisticas[estadisticas["Tamaño"] == "--- TOTALES ---"].iloc[0]

    assert int(totales["Cantidad_Grupos"]) == N_GRUPOS
    assert int(totales["Registros_Estimados"]) == N_FILAS

    por_tamano = estadisticas[estadisticas["Tamaño"] != "--- TOTALES ---"].set_index("Tamaño")
    assert int(por_tamano.loc["1 (Singleton)", "Cantidad_Grupos"]) == TAMANOS_GRUPO[1]
    assert int(por_tamano.loc["2 (Par)", "Cantidad_Grupos"]) == TAMANOS_GRUPO[2]
    assert int(por_tamano.loc["3", "Cantidad_Grupos"]) == TAMANOS_GRUPO[3]
    assert int(por_tamano.loc["4-5", "Cantidad_Grupos"]) == TAMANOS_GRUPO[5]
    assert int(por_tamano["Registros_Estimados"].sum()) == N_FILAS
    assert "ALCANCE" not in estadisticas.columns


def test_resumen_ejecutivo_dice_el_n_real(correlativa, golden):
    # Sin métricas externas: el N tiene que salir de la tabla, no de un dict.
    reportes = _reportes(correlativa, golden, metrics={})
    resumen = reportes["resumen_ejecutivo"]

    assert _valor_resumen(resumen, "Total Registros Procesados") == f"{N_FILAS:,}"
    assert _valor_resumen(resumen, "Entidades Únicas Identificadas") == f"{N_GRUPOS:,}"
    assert _valor_resumen(resumen, "Fuentes de Datos") == "3"
    multifuente = int((correlativa.groupby("ID_GRUPO")["SRC"].nunique() > 1).sum())
    assert _valor_resumen(resumen, "Entidades Multi-fuente") == f"{multifuente:,}"

    # Calidad sobre TODO el golden (104.000 > 100.000 filas).
    puntajes = golden["CONFIDENCE_SCORE"]
    assert (
        _valor_resumen(resumen, "Registros Alta Confianza (>0.9)") == f"{(puntajes > 0.9).sum():,}"
    )
    assert _valor_resumen(resumen, "Casos para Revisión (<0.75)") == f"{(puntajes < 0.75).sum():,}"


def test_resumen_prefiere_la_tabla_a_una_metrica_discrepante(correlativa, golden):
    # Una métrica heredada con un N de muestra no puede imponerse a la tabla.
    reportes = _reportes(correlativa, golden, metrics={"total_records": 99_997})
    resumen = reportes["resumen_ejecutivo"]
    assert _valor_resumen(resumen, "Total Registros Procesados") == f"{N_FILAS:,}"


def test_metricas_calidad_cubren_todo_el_golden(correlativa, golden):
    reportes = _reportes(correlativa, golden)
    calidad = reportes["metricas_calidad"]
    estadisticos = {"--- ESTADÍSTICAS ---", "Media", "Mediana", "Desviación Estándar"}
    niveles = calidad[~calidad["Nivel de Confianza"].isin(estadisticos)]
    assert int(pd.to_numeric(niveles["Cantidad"]).sum()) == N_GRUPOS


# ─────────────────────────────────────────────────────────────────────
#  Mismos totales cuando la correlativa llega por archivo
# ─────────────────────────────────────────────────────────────────────


def _exigir_totales(reportes: dict[str, pd.DataFrame]) -> None:
    fuentes = reportes["analisis_fuentes"].set_index("Fuente")
    assert fuentes["Total_Registros"].to_dict() == FUENTES
    estadisticas = reportes["estadisticas_grupos"]
    totales = estadisticas[estadisticas["Tamaño"] == "--- TOTALES ---"].iloc[0]
    assert int(totales["Cantidad_Grupos"]) == N_GRUPOS
    assert int(totales["Registros_Estimados"]) == N_FILAS
    resumen = reportes["resumen_ejecutivo"]
    assert _valor_resumen(resumen, "Total Registros Procesados") == f"{N_FILAS:,}"
    assert _valor_resumen(resumen, "Entidades Únicas Identificadas") == f"{N_GRUPOS:,}"


def test_totales_exactos_desde_parquet(correlativa, golden, tmp_path: Path):
    ruta_corr = tmp_path / "correlativa.parquet"
    ruta_gold = tmp_path / "golden.parquet"
    correlativa.to_parquet(ruta_corr, index=False)
    golden.to_parquet(ruta_gold, index=False)

    _exigir_totales(_reportes(str(ruta_corr), str(ruta_gold)))


def test_totales_exactos_desde_csv(correlativa, golden, tmp_path: Path):
    ruta_corr = tmp_path / "correlativa.csv"
    ruta_gold = tmp_path / "golden.csv"
    correlativa.to_csv(ruta_corr, index=False)
    golden.to_csv(ruta_gold, index=False)

    _exigir_totales(_reportes(str(ruta_corr), str(ruta_gold)))


def test_totales_exactos_desde_sqlite(correlativa, golden, tmp_path: Path):
    ruta_db = tmp_path / "resultados.db"
    with sqlite3.connect(ruta_db) as conn:
        correlativa.to_sql("correlative_table", conn, index=False)
        golden.to_sql("golden_records", conn, index=False)

    _exigir_totales(_reportes(str(ruta_db), str(ruta_db)))


# ─────────────────────────────────────────────────────────────────────
#  Lo único que se muestrea lo dice: casos de revisión ilustrativos
# ─────────────────────────────────────────────────────────────────────


def test_casos_revision_declaran_su_alcance_y_lo_registran(correlativa, golden):
    metrics: dict = {}
    reportes = _reportes(correlativa, golden, metrics=metrics)
    casos = reportes["casos_revision"]

    # Con confianza uniforme en [0.3, 1.0] hay miles de casos con prioridad > 20.
    # Misma fórmula del reporte: confianza 40 % · nombres 25 % · NIT 20 % · tamaño 15 %.
    prioridad = (
        (1 - golden["CONFIDENCE_SCORE"]) * 40
        + (golden["NAME_VARIATIONS"] / 10).clip(0, 1) * 25
        + (golden["NIT_VARIATIONS"] / 5).clip(0, 1) * 20
        + (golden["RECORD_COUNT"] / 50).clip(0, 1) * 15
    )
    n_total = int((prioridad > 20).sum())
    assert n_total > 1_000
    assert len(casos) == 1_000
    assert casos["ALCANCE"].unique().tolist() == [f"MUESTRA (1000 de {n_total})"]
    assert metrics["muestras"]["casos_revision"] == {"n": 1_000, "N": n_total}
    # Los demás reportes no se muestrean y no aparecen en el registro.
    assert set(metrics["muestras"]) == {"casos_revision"}
    assert casos["RAZON_PRINCIPAL"].str.contains("Baja confianza").all()


def test_casos_revision_completos_no_se_rotulan_como_muestra(correlativa, golden):
    pocos = golden.head(50).copy()
    pocos["CONFIDENCE_SCORE"] = 0.4  # 50 casos, todos con prioridad > 20
    metrics: dict = {}
    reportes = _reportes(correlativa, pocos, metrics=metrics)
    casos = reportes["casos_revision"]

    assert len(casos) == 50
    assert casos["ALCANCE"].unique().tolist() == ["COMPLETO (50 de 50)"]
    # Nada se recortó: no se escribe un registro vacío en las métricas.
    assert "muestras" not in metrics


def test_casos_revision_sobre_golden_recortado_nunca_dicen_completo(correlativa, golden):
    # El llamador (orquestador bajo RAM crítica) recortó el golden a 50 filas y lo
    # declaró. Que los 50 casos quepan en el tope NO los vuelve «completos»:
    # son una muestra de una vista.
    pocos = golden.head(50).copy()
    pocos["CONFIDENCE_SCORE"] = 0.4
    metrics: dict = {"muestras": {"golden": {"n": 50, "N": N_GRUPOS, "motivo": "memoria"}}}
    reportes = _reportes(correlativa, pocos, metrics=metrics)
    casos = reportes["casos_revision"]

    assert len(casos) == 50
    alcance = casos["ALCANCE"].unique().tolist()
    assert len(alcance) == 1
    assert not alcance[0].startswith("COMPLETO")
    assert alcance[0] == f"MUESTRA (50 de 50, sobre una vista de 50 de {N_GRUPOS} del golden)"
    assert metrics["muestras"]["casos_revision"] == {
        "n": 50,
        "N": 50,
        "golden_vista": {"n": 50, "N": N_GRUPOS},
    }


def test_sin_casos_de_revision_sobre_una_vista_del_golden_no_se_afirma_sobre_todo(
    correlativa, golden
):
    """Golden declarado como vista y ningún caso con prioridad > 20: el mensaje
    «no hay casos» se afirma sobre la vista, con ALCANCE y registro, no sobre
    «todos los grupos»."""
    pocos = golden.head(50).copy()
    pocos["CONFIDENCE_SCORE"] = 0.99
    pocos["RECORD_COUNT"] = 1
    metrics: dict = {"muestras": {"golden": {"n": 50, "N": N_GRUPOS, "motivo": "memoria"}}}
    reportes = _reportes(correlativa, pocos, metrics=metrics)
    casos = reportes["casos_revision"]

    assert len(casos) == 1
    assert "Todos los grupos" not in casos["Detalle"].iloc[0]
    assert casos["ALCANCE"].iloc[0] == (
        f"MUESTRA (0 de 0, sobre una vista de 50 de {N_GRUPOS} del golden)"
    )
    assert metrics["muestras"]["casos_revision"] == {
        "n": 0,
        "N": 0,
        "golden_vista": {"n": 50, "N": N_GRUPOS},
    }
    # Sin vista declarada, el mismo golden sí habla de todos los grupos y sin ALCANCE.
    sin_vista: dict = {}
    casos = _reportes(correlativa, pocos, metrics=sin_vista)["casos_revision"]
    assert "ALCANCE" not in casos.columns
    assert "Todos los grupos" in casos["Detalle"].iloc[0]
    assert "muestras" not in sin_vista


def test_muestras_heredado_sin_recorte_desaparece_en_vez_de_quedar_vacio(correlativa, golden):
    """Un dict de métricas de una corrida previa trae solo ``casos_revision``;
    en esta corrida los casos caben: la clave no puede quedar como ``{}``
    («ausente = todo completo» es el contrato de la cabecera)."""
    pocos = golden.head(50).copy()
    pocos["CONFIDENCE_SCORE"] = 0.4
    metrics: dict = {"muestras": {"casos_revision": {"n": 1000, "N": 5000}}}
    reportes = _reportes(correlativa, pocos, metrics=metrics)

    assert reportes["casos_revision"]["ALCANCE"].unique().tolist() == ["COMPLETO (50 de 50)"]
    assert "muestras" not in metrics


def test_total_registros_cuenta_las_filas_con_id_grupo_nulo(golden):
    """``groupby("ID_GRUPO").size()`` descarta los NaN de la clave: el resumen
    decía 3 y analisis_fuentes 4 sobre la misma correlativa. El total es el de
    la correlativa COMPLETA, igual que la suma por fuente."""
    correlativa = pd.DataFrame(
        {
            "ID_GRUPO": pd.array([1, 1, 2, None], dtype="Int64"),
            "SRC": ["A", "B", "A", "B"],
        }
    )
    reportes = _reportes(correlativa, golden.head(2))
    resumen = reportes["resumen_ejecutivo"]
    fuentes = reportes["analisis_fuentes"]

    assert _valor_resumen(resumen, "Total Registros Procesados") == "4"
    assert int(fuentes["Total_Registros"].sum()) == 4
    assert _valor_resumen(resumen, "Entidades Únicas Identificadas") == "2"


@pytest.mark.parametrize("tope", [-5, 0, 2.5, "1000"])
def test_tope_de_casos_revision_invalido_falla_rapido(correlativa, golden, tope):
    with pytest.raises(ValueError, match="report_max_casos_revision") as exc:
        ReportGenerator(
            correlative_data=correlativa,
            golden_records_data=golden,
            metrics={},
            config={"report_max_casos_revision": tope},
        )
    mensaje = str(exc.value)
    assert "Por qué importa" in mensaje
    assert "Qué hacer" in mensaje


def test_report_sample_size_ya_no_aplica_y_se_avisa(correlativa, golden):
    # CustomLogger no propaga al root (caplog no lo ve): se engancha un handler propio.
    import logging

    class _Captura(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.WARNING)
            self.mensajes: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.mensajes.append(record.getMessage())

    captura = _Captura()
    logger = logging.getLogger("ReportGenerator")
    logger.addHandler(captura)
    try:
        reportes = _reportes(correlativa, golden, config={"report_sample_size": 1_000})
    finally:
        logger.removeHandler(captura)

    _exigir_totales(reportes)  # la perilla heredada no recorta nada
    assert any("report_sample_size" in m for m in captura.mensajes)


# ─────────────────────────────────────────────────────────────────────
#  Si quien llama recorta (orquestador bajo presión de RAM), lo declara
#  y los reportes lo rotulan en vez de presentar la vista como total
# ─────────────────────────────────────────────────────────────────────


def test_vista_recortada_declarada_rotula_los_agregados_y_conserva_el_n_real(correlativa, golden):
    n_vista = 50_000
    metrics: dict = {
        "total_records": N_FILAS,
        "unique_groups": N_GRUPOS,
        "muestras": {
            "correlativa": {"n": n_vista, "N": N_FILAS, "motivo": "memoria crítica"},
            "golden": {"n": n_vista, "N": N_GRUPOS, "motivo": "memoria crítica"},
        },
    }
    # Las mismas métricas que el orquestador calcula (_build_metrics) sobre las
    # tablas completas ANTES de recortar las vistas.
    puntajes = golden["CONFIDENCE_SCORE"]
    multifuente = int((golden["SOURCES_COUNT"] > 1).sum())
    metrics.update(
        {
            "multi_source_groups": multifuente,
            "avg_confidence": float(puntajes.mean()),
            "high_confidence_count": int((puntajes > 0.9).sum()),
            "low_confidence_count": int((puntajes < 0.75).sum()),
            "sources_count": 3,
        }
    )
    reportes = _reportes(correlativa.head(n_vista), golden.head(n_vista), metrics=metrics)

    rotulo_corr = f"MUESTRA ({n_vista} de {N_FILAS})"
    rotulo_gold = f"MUESTRA ({n_vista} de {N_GRUPOS})"
    assert reportes["analisis_fuentes"]["ALCANCE"].unique().tolist() == [rotulo_corr]
    assert reportes["estadisticas_grupos"]["ALCANCE"].unique().tolist() == [rotulo_corr]
    assert reportes["metricas_calidad"]["ALCANCE"].unique().tolist() == [rotulo_gold]

    resumen = reportes["resumen_ejecutivo"]
    assert _valor_resumen(resumen, "Total Registros Procesados") == f"{N_FILAS:,}"
    assert _valor_resumen(resumen, "Entidades Únicas Identificadas") == f"{N_GRUPOS:,}"
    assert _valor_resumen(resumen, "Fuentes de Datos") == "3"
    assert _valor_resumen(resumen, "Entidades Multi-fuente") == f"{multifuente:,}"
    # La calidad sale de las métricas (calculadas sobre TODO el golden), no de la vista.
    assert _valor_resumen(resumen, "Confidence Promedio") == f"{puntajes.mean():.3f}"
    assert (
        _valor_resumen(resumen, "Registros Alta Confianza (>0.9)") == f"{(puntajes > 0.9).sum():,}"
    )
    assert _valor_resumen(resumen, "Casos para Revisión (<0.75)") == f"{(puntajes < 0.75).sum():,}"
    # Hubo recorte declarado: el resumen lleva ALCANCE y ninguna fila sale de la vista.
    assert resumen["ALCANCE"].unique().tolist() == ["COMPLETO"]
    # Lo declarado arriba sobrevive; la entrada propia se añade sin pisarlo.
    assert set(metrics["muestras"]) == {"correlativa", "golden", "casos_revision"}
    assert metrics["muestras"]["casos_revision"]["golden_vista"] == {"n": n_vista, "N": N_GRUPOS}


def test_vista_recortada_sin_metricas_usa_el_n_declarado_y_rotula_la_vista(correlativa, golden):
    # Constructor público sin `total_records` ni cifras de calidad: el N real
    # está en lo declarado y se usa; lo que solo puede salir de la vista se rotula.
    n_vista = 50_000
    metrics: dict = {
        "muestras": {
            "correlativa": {"n": n_vista, "N": N_FILAS},
            "golden": {"n": n_vista, "N": N_GRUPOS},
        }
    }
    vista_gold = golden.head(n_vista)
    reportes = _reportes(correlativa.head(n_vista), vista_gold, metrics=metrics)
    resumen = reportes["resumen_ejecutivo"].set_index("Métrica")

    assert resumen.loc["Total Registros Procesados", "Valor"] == f"{N_FILAS:,}"
    assert resumen.loc["Total Registros Procesados", "ALCANCE"] == "COMPLETO"
    assert resumen.loc["Entidades Únicas Identificadas", "Valor"] == f"{N_GRUPOS:,}"
    assert resumen.loc["Entidades Únicas Identificadas", "ALCANCE"] == "COMPLETO"

    rotulo_gold = f"MUESTRA ({n_vista} de {N_GRUPOS})"
    rotulo_corr = f"MUESTRA ({n_vista} de {N_FILAS})"
    puntajes_vista = vista_gold["CONFIDENCE_SCORE"]
    alta = resumen.loc["Registros Alta Confianza (>0.9)"]
    assert alta["Valor"] == f"{(puntajes_vista > 0.9).sum():,}"
    assert alta["ALCANCE"] == rotulo_gold
    assert resumen.loc["Casos para Revisión (<0.75)", "ALCANCE"] == rotulo_gold
    assert resumen.loc["Confidence Promedio", "ALCANCE"] == rotulo_gold
    assert resumen.loc["Entidades Multi-fuente", "ALCANCE"] == rotulo_corr
    assert resumen.loc["Fuentes de Datos", "ALCANCE"] == rotulo_corr


def test_resumen_sin_recorte_no_lleva_columna_alcance(correlativa, golden):
    resumen = _reportes(correlativa, golden)["resumen_ejecutivo"]
    assert "ALCANCE" not in resumen.columns


def _correr_l6_sintetico(tmp_path, monkeypatch, *, mem_percent: float, n_filas: int) -> dict:
    """Corre ``Orchestrator._run_L6`` con estrategias espía y RAM simulada."""
    from types import SimpleNamespace

    from artefactos_l6 import escribir_obligatorios_l6

    from record_linkage.pipeline.orchestrator import Orchestrator
    from record_linkage.reporting.strategies import DataExportStrategy, Phase

    datos = pd.DataFrame({"ID_GRUPO": np.arange(n_filas, dtype="int64"), "SRC": "FUENTE_SINT"})
    visto: dict[str, object] = {}

    class _Exporta(DataExportStrategy):
        def execute(self, ctx, logger):
            visto["export_n"] = len(ctx.correlative_df)
            # F1.4: el contrato de L6 exige los obligatorios en disco por
            # nombre exacto; la espía los deja escritos como haría la real.
            return escribir_obligatorios_l6(ctx.output_dir)

    class _Analitica:
        name = "analitica"

        def execute(self, ctx, logger):
            visto["analitica_n"] = len(ctx.correlative_df)
            visto["muestras"] = ctx.metrics.get("muestras")
            visto["total_records"] = ctx.metrics.get("total_records")
            visto["metrics"] = ctx.metrics
            return []

    class _Silencio:
        def info(self, *_a, **_k):
            pass

        warning = error = info

    orquestador = object.__new__(Orchestrator)
    orquestador.config = {"reporting_use_checkpoints": False}
    orquestador.sources = {}
    # work_dir y los directorios de todas las fases (vacíos): _build_metrics
    # cuenta en L2/L3 bajo work_dir y lo lee sin guardas, como en __init__.
    orquestador.work_dir = tmp_path
    orquestador.dirs = {p: tmp_path / p.value for p in Phase}
    orquestador._start_time = 1.0
    orquestador._phase_times = {}
    orquestador._meta_extra = {}
    orquestador._reporting_strategies = [_Exporta(), _Analitica()]
    orquestador.log = _Silencio()
    monkeypatch.setattr(
        "record_linkage.pipeline.orchestrator.psutil.virtual_memory",
        lambda: SimpleNamespace(percent=mem_percent),
    )

    orquestador._run_L6({"golden": datos[["ID_GRUPO"]], "correlative": datos})
    return visto


def test_orquestador_declara_el_recorte_de_l6_bajo_presion_de_ram(tmp_path, monkeypatch):
    n_filas = 50_003
    visto = _correr_l6_sintetico(tmp_path, monkeypatch, mem_percent=90.0, n_filas=n_filas)

    assert visto["export_n"] == n_filas
    assert visto["analitica_n"] == 50_000
    assert visto["total_records"] == n_filas
    muestras = visto["muestras"]
    assert isinstance(muestras, dict)
    assert muestras["correlativa"]["n"] == 50_000
    assert muestras["correlativa"]["N"] == n_filas
    assert muestras["golden"] == {**muestras["golden"], "n": 50_000, "N": n_filas}
    assert "memoria crítica" in muestras["correlativa"]["motivo"]
    # La bandera heredada se deriva del registro: una sola fuente de verdad.
    assert visto["metrics"]["reporting_sampled"] is True
    assert visto["metrics"]["reporting_sample_size"] == 50_000


def test_orquestador_sin_presion_de_ram_no_registra_muestras(tmp_path, monkeypatch):
    visto = _correr_l6_sintetico(tmp_path, monkeypatch, mem_percent=40.0, n_filas=50_003)

    assert visto["analitica_n"] == 50_003
    assert "muestras" not in visto["metrics"]
    assert "reporting_sampled" not in visto["metrics"]
