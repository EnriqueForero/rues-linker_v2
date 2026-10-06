"""F1.3 · El muestreo de los reportes L6 no vacía la muestra ni pierde columnas.

Defecto medido (pandas 3.0): ``groupby("SRC", group_keys=False).apply(lambda x:
x.sample(...))`` quita la columna de agrupación del marco y descarta las filas
con ``SRC`` NaN. El golden trae ``SRC`` NaN en el 99,9 % (F1.1), así que con
golden > 30.000 filas la muestra quedaba VACÍA y ``tarjeta_calidad_datos.png``,
``heatmap_interseccion_mejorado.png`` y ``casos_problematicos_detallado.xlsx``
se omitían con un WARNING. Estas pruebas fijan la regla única
(``reporting._muestreo.muestra_estratificada``) y la compuerta de
``EnhancedReportingSuite._validate_data``.

Datos: empresas inventadas, ninguna razón social real.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.pipeline.errores import ErrorPipeline, MuestreoReportesError
from record_linkage.reporting._muestreo import muestra_estratificada
from record_linkage.reporting.reports import ReportGenerator
from record_linkage.reporting.suite import EnhancedReportingSuite
from record_linkage.reporting.visualizer import DataVisualizer

N_GOLDEN = 60_000
FUENTES = ["RUES", "ADUANAS", "CRM"]


def _golden_sintetico(n: int = N_GOLDEN, fraccion_src_nan: float = 0.99) -> pd.DataFrame:
    """Golden con las columnas mínimas que lee la suite y ``SRC`` NaN casi siempre."""
    rng = np.random.default_rng(7)
    indice = np.arange(n)
    src = pd.Series(rng.choice(FUENTES, size=n), dtype=object)
    src[rng.random(n) < fraccion_src_nan] = np.nan
    return pd.DataFrame(
        {
            "ID_GRUPO": "G" + pd.Series(indice).astype(str).str.zfill(6),
            "NIT_FINAL": 800_000_000 + indice,
            "RAZON_SOCIAL_FINAL": "EMPRESA FICTICIA " + pd.Series(indice).astype(str),
            "CONFIDENCE_SCORE": rng.uniform(0.3, 1.0, size=n).round(3),
            "SOURCES_COUNT": rng.integers(1, 4, size=n),
            "SRC": src,
        }
    )


def _correlativa_coherente(golden: pd.DataFrame) -> pd.DataFrame:
    """Una fila por fuente declarada en ``SOURCES_COUNT``; ``SRC`` siempre presente."""
    rng = np.random.default_rng(11)
    repeticiones = golden["SOURCES_COUNT"].to_numpy()
    base = golden.loc[golden.index.repeat(repeticiones), ["ID_GRUPO", "NIT_FINAL"]]
    n = len(base)
    return pd.DataFrame(
        {
            "ID_GRUPO": base["ID_GRUPO"].to_numpy(),
            "SRC": rng.choice(FUENTES, size=n),
            "NIT": base["NIT_FINAL"].to_numpy(),
            "RAZON_SOCIAL": "EMPRESA FICTICIA " + pd.Series(np.arange(n)).astype(str).to_numpy(),
            "ORIGINAL_INDEX": np.arange(n),
        }
    )


def _conteo_por_grupo(df: pd.DataFrame, columna: str) -> pd.Series:
    return df.groupby(columna, dropna=False).size()


def _estratos(indice: pd.Index) -> set[object]:
    """Etiquetas de los estratos con NaN como un valor comparable.

    Con pandas 2 (matriz 3.10) cada ``nan`` del índice es un objeto distinto y
    ``{nan} == {nan}`` es falso; con pandas 3 coincidía por casualidad.
    """
    return {"<NaN>" if pd.isna(v) else v for v in indice.tolist()}


# ---------------------------------------------------------------------------
# La regla única: muestra_estratificada
# ---------------------------------------------------------------------------


def test_muestra_estratificada_conserva_columnas_y_todos_los_grupos() -> None:
    df = pd.DataFrame(
        {
            "SRC": [np.nan] * 90 + ["A"] * 6 + ["B"] * 4,
            "x": np.arange(100),
            "y": "texto",
        }
    )
    muestra = muestra_estratificada(df, "SRC", 20, semilla=42)

    assert not muestra.empty
    assert list(muestra.columns) == list(df.columns)
    assert len(muestra) <= 20
    por_grupo = _conteo_por_grupo(muestra, "SRC")
    assert _estratos(por_grupo.index) == _estratos(_conteo_por_grupo(df, "SRC").index)
    assert (por_grupo >= 1).all()
    # Proporción: el estrato NaN (90 %) domina la muestra.
    assert por_grupo.loc[np.nan] >= 15
    # Son filas reales del insumo, sin duplicar.
    assert muestra.index.is_unique
    assert muestra.index.isin(df.index).all()


def test_muestra_estratificada_respeta_el_tope_con_muchos_estratos_pequenos() -> None:
    # 30 estratos de 1 fila (el piso les da 1 a cada uno) + 1 estrato grande.
    df = pd.DataFrame({"SRC": [f"F{i}" for i in range(30)] + ["GRANDE"] * 970})
    df["x"] = np.arange(len(df))
    muestra = muestra_estratificada(df, "SRC", 40, semilla=42)

    assert len(muestra) <= 40
    assert muestra["SRC"].nunique() == 31  # piso de 1 por estrato
    assert (muestra["SRC"] == "GRANDE").sum() == 10  # el tope lo paga el estrato grande


def test_muestra_estratificada_falla_si_hay_mas_estratos_que_n() -> None:
    df = pd.DataFrame({"SRC": [f"F{i}" for i in range(50)], "x": range(50)})
    with pytest.raises(MuestreoReportesError, match="Qué hacer"):
        muestra_estratificada(df, "SRC", 10, semilla=42)


def test_muestra_estratificada_n_invalido() -> None:
    df = pd.DataFrame({"SRC": ["A", "B"], "x": [1, 2]})
    with pytest.raises(MuestreoReportesError, match="Qué hacer"):
        muestra_estratificada(df, "SRC", 0, semilla=42)


def test_muestra_estratificada_devuelve_el_insumo_si_cabe() -> None:
    df = pd.DataFrame({"SRC": ["A", np.nan], "x": [1, 2]})
    muestra = muestra_estratificada(df, "SRC", 10, semilla=42)
    pd.testing.assert_frame_equal(muestra, df)


def test_muestra_estratificada_sin_columna_es_muestra_simple() -> None:
    df = pd.DataFrame({"x": np.arange(100)})
    muestra = muestra_estratificada(df, "SRC", 10, semilla=42)
    assert len(muestra) == 10
    assert list(muestra.columns) == ["x"]


def test_muestra_estratificada_es_determinista_por_semilla() -> None:
    df = pd.DataFrame({"SRC": np.repeat(["A", "B", np.nan], 100), "x": np.arange(300)})
    a = muestra_estratificada(df, "SRC", 30, semilla=42)
    b = muestra_estratificada(df, "SRC", 30, semilla=42)
    c = muestra_estratificada(df, "SRC", 30, semilla=43)
    pd.testing.assert_frame_equal(a, b)
    assert not a["x"].equals(c["x"])


# ---------------------------------------------------------------------------
# EnhancedReportingSuite: muestreo + compuerta
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def golden_60k() -> pd.DataFrame:
    return _golden_sintetico()


@pytest.fixture(scope="module")
def correlativa_60k(golden_60k: pd.DataFrame) -> pd.DataFrame:
    return _correlativa_coherente(golden_60k)


@pytest.fixture(scope="module")
def suite_60k(golden_60k: pd.DataFrame, correlativa_60k: pd.DataFrame) -> EnhancedReportingSuite:
    metrics = {
        "total_records": len(correlativa_60k),
        "unique_groups": len(golden_60k),
        "linkage_rate": 0.2,
        "reduction_rate": 0.3,
        "multi_source_groups": int((golden_60k["SOURCES_COUNT"] > 1).sum()),
        "candidates_found": 1000,
        "pairs_scored": 800,
    }
    return EnhancedReportingSuite(
        correlative_data=correlativa_60k,
        golden_records_data=golden_60k,
        metrics=metrics,
        config={},
    )


def test_load_smart_sample_golden_con_src_nan_no_queda_vacia(
    suite_60k: EnhancedReportingSuite, golden_60k: pd.DataFrame
) -> None:
    sample_size = int(suite_60k.sample_size * 0.6)
    assert len(golden_60k) > sample_size, "la prueba exige que el golden se muestree"

    muestra = suite_60k._load_smart_sample(golden_60k, "golden_records", sample_size)

    assert not muestra.empty
    assert list(muestra.columns) == list(golden_60k.columns)
    assert len(muestra) <= sample_size
    grupos_origen = _estratos(_conteo_por_grupo(golden_60k, "SRC").index)
    grupos_muestra = _conteo_por_grupo(muestra, "SRC")
    assert _estratos(grupos_muestra.index) == grupos_origen
    assert (grupos_muestra >= 1).all()
    assert grupos_muestra.loc[np.nan] > 0.9 * len(muestra)
    # Lo que el constructor dejó en la instancia es esa misma muestra.
    pd.testing.assert_frame_equal(suite_60k.golden_records_sample, muestra)


def test_load_smart_sample_correlativa_conserva_columnas(
    suite_60k: EnhancedReportingSuite, correlativa_60k: pd.DataFrame
) -> None:
    muestra = suite_60k.correlative_sample
    assert not muestra.empty
    assert list(muestra.columns) == list(correlativa_60k.columns)
    assert len(muestra) <= suite_60k.sample_size
    assert set(muestra["SRC"].unique()) == set(FUENTES)


def test_validate_data_falla_si_insumo_no_vacio_da_muestra_vacia(
    golden_60k: pd.DataFrame, correlativa_60k: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _vacia(self, data_ref, table_name, sample_size):
        return pd.DataFrame()

    monkeypatch.setattr(EnhancedReportingSuite, "_load_smart_sample", _vacia)
    with pytest.raises(MuestreoReportesError) as info:
        EnhancedReportingSuite(correlativa_60k, golden_60k, metrics={}, config={})
    texto = str(info.value)
    assert "Qué pasó" in texto and "Por qué importa" in texto and "Qué hacer" in texto
    assert "correlative_table" in texto
    assert isinstance(info.value, ErrorPipeline)


def test_validate_data_falla_si_la_muestra_pierde_columnas(
    golden_60k: pd.DataFrame, correlativa_60k: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _sin_src(self, data_ref, table_name, sample_size):
        return data_ref.head(10).drop(columns=["SRC"])

    monkeypatch.setattr(EnhancedReportingSuite, "_load_smart_sample", _sin_src)
    with pytest.raises(MuestreoReportesError, match="SRC"):
        EnhancedReportingSuite(correlativa_60k, golden_60k, metrics={}, config={})


def test_validate_data_acepta_insumo_vacio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un insumo vacío produce una muestra vacía: no es una degradación."""
    vacio = pd.DataFrame(columns=["ID_GRUPO", "SRC"])
    suite = EnhancedReportingSuite(vacio, vacio, metrics={}, config={})
    assert suite.correlative_sample.empty and suite.golden_records_sample.empty


def test_validate_data_falla_si_la_carga_lanzo_aunque_no_se_sepan_las_filas(
    tmp_path: Path,
) -> None:
    """Un ``.db`` existente sin la tabla: el conteo del insumo es desconocido,
    pero la carga lanzó ``SQLiteTableNotFoundError``; eso es degradación y la
    compuerta la cita en vez de seguir con una muestra vacía."""
    import sqlite3

    ruta = tmp_path / "resultados.db"
    with sqlite3.connect(ruta) as conn:
        pd.DataFrame({"x": [1]}).to_sql("otra_tabla", conn, index=False)

    with pytest.raises(MuestreoReportesError) as info:
        EnhancedReportingSuite(str(ruta), str(ruta), metrics={}, config={})
    texto = str(info.value)
    assert "correlative_table" in texto
    assert "SQLiteTableNotFoundError" in texto


def test_suite_relanza_el_fallo_del_muestreo_en_vez_de_tragarlo(
    golden_60k: pd.DataFrame, correlativa_60k: pd.DataFrame
) -> None:
    """``max_memory_mb=0`` da ``n=0``: ``muestra_estratificada`` falla con
    MuestreoReportesError y la suite la deja subir tal cual (antes la atrapaba
    el ``except Exception`` del cargador y la compuerta la reconstruía como
    «muestra vacía»)."""
    with pytest.raises(MuestreoReportesError, match="n=0"):
        EnhancedReportingSuite(correlativa_60k, golden_60k, metrics={}, config={}, max_memory_mb=0)


def test_visualizer_relanza_el_fallo_del_muestreo_en_vez_de_tragarlo() -> None:
    """Más estratos (3 fuentes + NaN) que ``viz_sample_size=2``: el visualizador
    no devuelve un DataFrame vacío con un error en el log, falla."""
    correlativa = _correlativa_con_src_nan(200)
    with pytest.raises(MuestreoReportesError, match="estratos"):
        DataVisualizer(correlativa, correlativa, metrics={}, config={"viz_sample_size": 2})


def test_pipeline_heredado_relanza_el_error_de_muestreo_de_la_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``RecordLinkagePipeline._phase5_reports_and_export`` (api.dedupe) envolvía
    la suite en ``except Exception`` y un MuestreoReportesError terminaba como
    ``logger.error`` con la corrida «bien». Ahora sube al llamador."""
    from record_linkage.pipeline import linkage_pipeline as modulo
    from record_linkage.reporting import suite as modulo_suite

    class _ExportadorNulo:
        def __init__(self, _config) -> None:
            pass

        def export_with_auto_detection(self, **_kw) -> str:
            return ""

    class _SuiteQueFalla:
        def __init__(self, *_a, **_k) -> None:
            raise MuestreoReportesError("muestreo inventado que falla")

    monkeypatch.setattr(modulo, "SmartExporter", _ExportadorNulo)
    monkeypatch.setattr(modulo, "_class_is_importable", lambda n: n == "EnhancedReportingSuite")
    monkeypatch.setattr(modulo_suite, "EnhancedReportingSuite", _SuiteQueFalla)

    with pytest.warns(DeprecationWarning, match="RecordLinkagePipeline"):  # F2.9
        pipeline = modulo.RecordLinkagePipeline()
    pipeline.config["output_directory"] = str(tmp_path / "salida")
    pipeline.keep_intermediate_results = True
    datos = pd.DataFrame({"ID_GRUPO": [1], "SRC": ["A"]})
    pipeline.results = {"correlative_table": datos, "golden_records": datos, "metrics": {}}

    with pytest.raises(MuestreoReportesError, match="muestreo inventado"):
        pipeline._phase5_reports_and_export(reports=[], generate_visualizations=True)


def test_suite_escribe_los_tres_artefactos(
    suite_60k: EnhancedReportingSuite, tmp_path: Path
) -> None:
    generados = suite_60k.generate_all_enhanced_reports(str(tmp_path))

    esperados = {
        "quality_card": "tarjeta_calidad_datos.png",
        "intersection_heatmap": "heatmap_interseccion_mejorado.png",
        "problematic_cases": "casos_problematicos_detallado.xlsx",
    }
    faltan = {clave: nombre for clave, nombre in esperados.items() if clave not in generados}
    assert not faltan, f"artefactos omitidos: {faltan}; generados: {sorted(generados)}"
    for clave, nombre in esperados.items():
        ruta = tmp_path / nombre
        assert Path(generados[clave]) == ruta
        assert ruta.is_file() and ruta.stat().st_size > 0


# ---------------------------------------------------------------------------
# ReportGenerator y DataVisualizer: la misma regla desde los otros sitios
# ---------------------------------------------------------------------------


class _LoggerMudo:
    def debug(self, *_a, **_k) -> None:
        pass

    def info(self, *_a, **_k) -> None:
        pass

    def warning(self, *_a, **_k) -> None:
        pass

    def error(self, *_a, **_k) -> None:
        pass


def _correlativa_con_src_nan(n: int = 1_000) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    src = pd.Series(rng.choice(FUENTES, size=n), dtype=object)
    src[rng.random(n) < 0.8] = np.nan
    return pd.DataFrame({"ID_GRUPO": np.arange(n) // 3, "SRC": src, "NIT": np.arange(n)})


# ReportGenerator ya no muestrea: desde F1.5 calcula los reportes sobre la tabla
# completa y `report_sample_size` es una perilla retirada (avisa, no recorta).
# El muestreo estratificado de L6 vive solo en suite.py y visualizer.py, probados abajo.


def test_visualizer_stratified_sample_conserva_nan_y_columnas() -> None:
    df = _correlativa_con_src_nan()
    visualizador = DataVisualizer.__new__(DataVisualizer)
    visualizador.sample_size = 100
    visualizador.logger = _LoggerMudo()

    muestra = visualizador._stratified_sample(df, "SRC")
    assert 0 < len(muestra) <= 100
    assert list(muestra.columns) == list(df.columns)
    assert muestra["SRC"].isna().any(), "el estrato NaN se descartaba"
    assert set(muestra["SRC"].dropna().unique()) == set(FUENTES)
