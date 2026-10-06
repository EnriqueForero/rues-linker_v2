"""F1.13 · Degradaciones silenciosas restantes en api.py y deduplication/auto.py.

Cuatro puntos medidos en b45e335 y corregidos aquí:

(a) L6 tras postprocesamiento (colapso exacto / matcher) corría fuera de
    ``_exec_phase`` y no quedaba en ``manifest.json``.
(b) El colapso exacto con celdas no hashables (listas/dicts) caía a «conservar
    todas las filas» sin aviso.
(c) ``link()`` devolvía ``-1`` como centinela si la correlativa no traía ``SRC``.
(d) ``deduplicate_auto`` desempaquetaba como ``stats`` el segundo elemento de
    ``deduplicate_unified``, que es el DataFrame de conexiones no triviales.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from record_linkage import linkage
from record_linkage.api import _collapse_exact_sources, link
from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.deduplication.auto import deduplicate_auto
from record_linkage.pipeline.errores import ColapsoExactoError, CruceSinFuenteError
from record_linkage.pipeline.orchestrator import Orchestrator


@contextlib.contextmanager
def _silencio():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# (a) L6 postprocesado entra al manifiesto
# ─────────────────────────────────────────────────────────────────────────────


def test_l6_postprocesado_queda_registrado_en_manifest(tmp_path: Path) -> None:
    """Con colapso exacto y reporting activo, L6 pasa por el mismo registro de
    fase que el camino normal: estado, tiempos y artefactos en manifest.json."""
    fuente = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "800000001"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME SAS", "BETA SAS"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "CALI"],
        }
    )
    work_dir = tmp_path / "corrida"
    with _silencio():
        resultado = linkage(
            {"RUES": fuente},
            work_dir=str(work_dir),
            skip_reporting=False,
            collapse_exact_duplicates=True,
        )

    assert resultado["report_files"], "L6 debe producir artefactos"
    manifiesto = json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "L6_reporting" in manifiesto, sorted(manifiesto)
    registro = manifiesto["L6_reporting"]
    assert registro["status"] == "DONE"
    assert registro["meta"]["duration"] > 0
    assert "peak_rss_mib" in registro["meta"]
    assert registro["meta"]["postprocesado"] == ["colapso_exacto"]
    rutas_manifiesto = {Path(a["path"]).name for a in registro["artifacts"]}
    rutas_resultado = {Path(p).name for p in resultado["report_files"]}
    assert rutas_manifiesto == rutas_resultado
    assert rutas_manifiesto
    # La expansión sigue intacta: una fila correlativa por fila de entrada.
    assert len(resultado["correlative"]) == len(fuente)


def _manifiesto(work_dir: Path) -> dict:
    return json.loads((work_dir / "manifest.json").read_text(encoding="utf-8"))


def test_l6_postprocesado_se_regenera_en_cada_corrida(tmp_path: Path) -> None:
    """La huella de L6 no incorpora el matcher ni el plan de colapso: dos
    corridas postprocesadas sobre el mismo work_dir tienen el mismo hash L6.
    Sin invalidar el registro previo, la segunda reutilizaría en silencio los
    reportes de la primera. L1…L5 sí se reutilizan."""
    fuente = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "800000001"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME SAS", "BETA SAS"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "CALI"],
        }
    )
    work_dir = tmp_path / "corrida"
    sellos: list[dict[str, str]] = []
    for _ in range(2):
        with _silencio():
            linkage(
                {"RUES": fuente},
                work_dir=str(work_dir),
                skip_reporting=False,
                collapse_exact_duplicates=True,
            )
        manifiesto = _manifiesto(work_dir)
        sellos.append(
            {
                "L5": manifiesto["L5_golden"]["timestamp"],
                "L6": manifiesto["L6_reporting"]["timestamp"],
                "hash_L6": manifiesto["L6_reporting"]["hash"],
            }
        )

    assert sellos[0]["hash_L6"] == sellos[1]["hash_L6"], "premisa: la huella L6 no cambia"
    assert sellos[0]["L5"] == sellos[1]["L5"], "L5 debe reutilizarse"
    assert sellos[0]["L6"] != sellos[1]["L6"], "L6 debe regenerarse, no reutilizarse"


def test_export_reports_queda_registrado_en_manifest(tmp_path: Path) -> None:
    """``export_reports()`` es el otro camino que generaba L6 fuera del
    registro de fase; ahora delega en ``ejecutar_reporting_postprocesado``."""
    fuente = pd.DataFrame(
        {
            "NIT": ["900111111", "900222222"],
            "RAZON_SOCIAL": ["ALFA SAS", "BETA SAS"],
        }
    )
    work_dir = tmp_path / "manual"
    config = crear_config_orchestrator(perfil="prueba_rapida", workspace=str(work_dir))
    orquestador = Orchestrator(config, {"RUES": fuente}, str(work_dir))
    with _silencio():
        orquestador.run(skip_reporting=True)
    assert "L6_reporting" not in _manifiesto(work_dir)

    with _silencio():
        archivos = orquestador.export_reports()

    assert archivos
    registro = _manifiesto(work_dir)["L6_reporting"]
    assert registro["status"] == "DONE"
    assert registro["meta"]["duration"] > 0
    assert registro["meta"]["postprocesado"] == ["export_reports"]
    assert {Path(a["path"]).name for a in registro["artifacts"]} == {p.name for p in archivos}


# ─────────────────────────────────────────────────────────────────────────────
# (b) Celdas no hashables: fallar con mensaje accionable
# ─────────────────────────────────────────────────────────────────────────────


def test_colapso_exacto_nombra_las_columnas_no_hashables() -> None:
    fuente = pd.DataFrame(
        {
            "NIT": ["1", "2", "1"],
            "RAZON_SOCIAL": ["A", "B", "A"],
            "CIIU": [["4711"], ["4719"], ["4711"]],
            "EXTRA": [{"k": 1}, {"k": 2}, {"k": 1}],
        }
    )
    with pytest.raises(ColapsoExactoError) as exc:
        _collapse_exact_sources({"CRM": fuente})

    assert exc.value.fuente == "CRM"
    assert exc.value.columnas == ["CIIU", "EXTRA"]
    texto = str(exc.value)
    assert "Qué pasó" in texto and "Por qué importa" in texto and "Qué hacer" in texto
    assert "CRM" in texto and "CIIU" in texto and "EXTRA" in texto
    assert "collapse_exact_duplicates=False" in texto
    assert "texto" in texto.lower()


def _snippet_remedio(texto: str) -> str:
    """Extrae del mensaje la línea ``df[...] = df[...].astype(str)``."""
    encontrado = re.search(r"df\[.*?\] = df\[.*?\]\.astype\(str\)", texto)
    assert encontrado is not None, texto
    return encontrado.group(0)


def test_colapso_exacto_remedio_se_puede_ejecutar_tal_cual() -> None:
    """El «qué hacer» tiene que ser pegable: con dos columnas, ``df['A', 'B']``
    indexa una tupla y pandas levanta KeyError."""
    fuente = pd.DataFrame(
        {
            "NIT": ["1", "2", "1"],
            "RAZON_SOCIAL": ["A", "B", "A"],
            "CIIU": [["4711"], ["4719"], ["4711"]],
            "EXTRA": [{"k": 1}, {"k": 2}, {"k": 1}],
        }
    )
    with pytest.raises(ColapsoExactoError) as exc:
        _collapse_exact_sources({"CRM": fuente})

    snippet = _snippet_remedio(str(exc.value))
    df = fuente.copy()
    exec(snippet, {}, {"df": df})  # el remedio debe correr tal cual
    assert df["CIIU"].map(type).eq(str).all() and df["EXTRA"].map(type).eq(str).all()
    colapsadas, plan = _collapse_exact_sources({"CRM": df})
    assert len(colapsadas["CRM"]) == 2
    assert plan["stats"]["CRM"]["collapsed_rows"] == 1


def test_colapso_exacto_sin_columnas_no_sugiere_df_vacio() -> None:
    """Si pandas falló por otra causa no hay columnas que convertir: el
    remedio cita la causa y no imprime ``df[] = df[]``."""
    exc = ColapsoExactoError("CRM", [], causa=ValueError("columnas duplicadas"))
    texto = str(exc)
    assert exc.columnas == []
    assert "df[]" not in texto
    assert "ValueError: columnas duplicadas" in texto
    assert "collapse_exact_duplicates=False" in texto
    assert "Qué hacer" in texto


def test_linkage_con_celda_lista_falla_en_vez_de_conservar_todo(tmp_path: Path) -> None:
    fuente = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456"],
            "RAZON_SOCIAL": ["ACME SAS", "ACME SAS"],
            "TAGS": [["a"], ["a"]],
        }
    )
    with pytest.raises(ColapsoExactoError, match="TAGS"):
        linkage(
            {"RUES": fuente},
            work_dir=str(tmp_path / "corrida"),
            skip_reporting=True,
            collapse_exact_duplicates=True,
        )
    # Sin colapso la misma fuente sigue siendo válida: el error es del colapso.
    with _silencio():
        resultado = linkage(
            {"RUES": fuente},
            work_dir=str(tmp_path / "sin_colapso"),
            skip_reporting=True,
            collapse_exact_duplicates=False,
        )
    assert len(resultado["correlative"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# (c) link(): sin SRC → excepción; con SRC → conteos reales
# ─────────────────────────────────────────────────────────────────────────────


def _tablas_ab() -> tuple[pd.DataFrame, pd.DataFrame]:
    df_a = pd.DataFrame(
        {
            "NIT": ["900111222", "900111222", "900333444"],
            "RAZON_SOCIAL": ["ACME COLOMBIA SAS", "ACME COLOMBIA S.A.S.", "GLOBEX LTDA"],
            "CIUDAD": ["BOGOTA", "BOGOTA", "MEDELLIN"],
        }
    )
    df_b = pd.DataFrame(
        {
            "NIT": ["900111222", "900999888"],
            "RAZON_SOCIAL": ["ACME COLOMBIA S A S", "TITAN GROUP SAS"],
            "CIUDAD": ["BOGOTA", "BARRANQUILLA"],
        }
    )
    return df_a, df_b


def test_link_sin_src_falla_con_excepcion_especifica(monkeypatch: pytest.MonkeyPatch) -> None:
    df_a, df_b = _tablas_ab()

    def _linkage_sin_src(*_args, **_kwargs):
        return {
            "correlative": pd.DataFrame({"ID_GRUPO": [1, 1, 2], "ORIGINAL_INDEX": [0, 1, 2]}),
            "golden": pd.DataFrame({"ID_GRUPO": [1, 2]}),
        }

    monkeypatch.setattr("record_linkage.api.linkage", _linkage_sin_src)
    with pytest.raises(CruceSinFuenteError) as exc:
        link(df_a, df_b, nombre_a="RUES", nombre_b="ADUANAS")
    texto = str(exc.value)
    assert "SRC" in texto
    assert "Qué pasó" in texto and "Por qué importa" in texto and "Qué hacer" in texto
    assert "-1" not in texto
    # Documentado en CruceSinFuenteError: vacío si la columna misma falta.
    assert exc.value.faltantes == []
    assert "SRC" not in exc.value.columnas


def test_link_con_src_sin_alguna_fuente_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """SRC existe pero una de las dos etiquetas no aparece: también es defecto."""
    df_a, df_b = _tablas_ab()

    def _linkage_una_fuente(*_args, **_kwargs):
        return {
            "correlative": pd.DataFrame(
                {"ID_GRUPO": [1, 1], "ORIGINAL_INDEX": [0, 1], "SRC": ["RUES", "RUES"]}
            ),
            "golden": pd.DataFrame({"ID_GRUPO": [1]}),
        }

    monkeypatch.setattr("record_linkage.api.linkage", _linkage_una_fuente)
    with pytest.raises(CruceSinFuenteError, match="ADUANAS") as exc:
        link(df_a, df_b, nombre_a="RUES", nombre_b="ADUANAS")
    assert exc.value.faltantes == ["ADUANAS"]


def test_link_camino_normal_devuelve_conteos_reales(tmp_path: Path) -> None:
    df_a, df_b = _tablas_ab()
    with _silencio():
        res = link(
            df_a,
            df_b,
            nombre_a="RUES",
            nombre_b="ADUANAS",
            work_dir=str(tmp_path / "cruce"),
            skip_reporting=True,
        )
    # Oráculo fijo derivado de _tablas_ab(): ACME (NIT 900111222) tiene 2 filas
    # en RUES y 1 en ADUANAS → 1 grupo cruzado y 2 pares A↔B. GLOBEX (solo A) y
    # TITAN (solo B) no cruzan.
    assert "SRC" in res.correlativa.columns
    assert res.metricas["n_grupos_cruzados"] == 1
    assert res.metricas["n_pares_a_b"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# (d) deduplicate_auto: stats es un dict de estadísticas, no un DataFrame
# ─────────────────────────────────────────────────────────────────────────────


def _df_con_nit(n: int) -> pd.DataFrame:
    filas = []
    for i in range(n):
        nit = f"{900000000 + i}"
        filas.append({"NIT": nit, "RAZON_SOCIAL": f"EMPRESA {i} SAS"})
        filas.append({"NIT": nit, "RAZON_SOCIAL": f"EMPRESA {i} S.A.S."})
    return pd.DataFrame(filas)


def _df_sin_nit(n: int) -> pd.DataFrame:
    filas = []
    for i in range(n):
        filas.append({"NIT": "", "RAZON_SOCIAL": f"IMPORTADORA {i} CO LTD"})
        filas.append({"NIT": "", "RAZON_SOCIAL": f"IMPORTADORA {i} CORP"})
    return pd.DataFrame(filas)


def _sin_pandas_dentro(obj: object) -> bool:
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return False
    if isinstance(obj, dict):
        return all(_sin_pandas_dentro(v) for v in obj.values())
    return True


def test_auto_homogeneo_stats_son_estadisticas_y_no_columnas(tmp_path: Path) -> None:
    df = _df_con_nit(4)
    with _silencio():
        corr, stats = deduplicate_auto(df, output_dir=str(tmp_path / "o"))

    assert stats["routed"] == "homogeneo_con_nit"
    assert _sin_pandas_dentro(stats), sorted(stats)
    assert "NIT" not in stats and "RAZON_SOCIAL" not in stats
    assert stats["n_grupos_con_nit"] == corr["ID_GRUPO"].nunique()
    assert stats["n_grupos_sin_nit"] == 0
    assert stats["stats_con_nit"]["n_registros"] == len(df)
    assert stats["stats_con_nit"]["n_grupos"] == corr["ID_GRUPO"].nunique()
    assert stats["stats_con_nit"]["profile"] == "deduplication_standard"
    assert stats["stats_con_nit"]["n_conexiones_no_triviales"] >= 0
    json.dumps(stats)  # serializable: va al manifiesto de dedupe()


def test_auto_mixto_stats_por_regimen_son_dicts(tmp_path: Path) -> None:
    df = pd.concat([_df_con_nit(4), _df_sin_nit(4)], ignore_index=True)
    with _silencio():
        corr, stats = deduplicate_auto(df, output_dir=str(tmp_path / "o"))

    assert stats["routed"] == "mixto"
    assert _sin_pandas_dentro(stats)
    assert isinstance(stats["stats_con_nit"], dict)
    assert isinstance(stats["stats_sin_nit"], dict)
    assert stats["stats_con_nit"]["n_registros"] == 8
    assert stats["stats_sin_nit"]["n_registros"] == 8
    assert stats["stats_con_nit"]["n_grupos"] == stats["n_grupos_con_nit"]
    assert stats["stats_sin_nit"]["n_grupos"] == stats["n_grupos_sin_nit"]
    assert stats["stats_sin_nit"]["profile"] == "deduplication_sin_nit_conservador"
    assert len(corr) == len(df)
    json.dumps(stats)
