"""F2.12 — una sola regla de ``CONFIANZA`` (ALTA · MEDIA · BAJA) en los cinco caminos.

Qué congela
-----------
* La regla vive UNA vez: ``golden.metricas.confianza_de_grupo``. Su vocabulario
  es ``contrato.NIVELES_CONFIANZA`` (ni una copia local ni literales sueltos) y
  ``GoldenRecordGeneratorV7`` ya no lleva la copia fila a fila que tenía.
* El diccionario de cada tabla con ``CONFIANZA`` (correlativa, golden, enlaces)
  cita la función por su nombre y nombra el vocabulario.
* Los cinco caminos la aplican y solo producen valores del vocabulario:
  ``linkage()``, ``link()``, ``dedupe()``, ``ejecutar_cruce`` (pandas y DuckDB)
  y ``deduplicar_importadores`` (``GOLDEN`` y ``CORRELATIVA``, que hasta F2.12
  no traían ``CONFIANZA``).
* En cada camino, la ``CONFIANZA`` del golden se reproduce bit a bit con
  ``confianza_de_grupo`` sobre las métricas del propio golden, y la de la
  correlativa es la del grupo.
* ``dedupe()`` no trae golden en memoria (la correlativa lleva ``CONFIANZA``
  nula; eso lo congela ``test_contrato_salida``), así que aquí se lee el golden
  que deja en disco por régimen (``con_nit``/``sin_nit``): una aserción sobre
  la columna nula de la correlativa no podría fallar nunca.

Las empresas son inventadas. Ningún dato licenciado entra aquí.
"""

from __future__ import annotations

import contextlib
import inspect
import io
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from apoyo_cruce import config_cruce

import record_linkage as rl
from record_linkage import ColumnType, SourceSpec, contrato
from record_linkage.flujo import ConfigImportadores, deduplicar_importadores, ejecutar_cruce
from record_linkage.golden import generator as generador_golden
from record_linkage.golden.metricas import confianza_de_grupo
from record_linkage.ingestion import DuckDBIngestionSettings

NIVELES = set(contrato.NIVELES_CONFIANZA)
NOMBRE_REGLA = "golden.metricas.confianza_de_grupo"


def _silencio() -> contextlib.AbstractContextManager[Any]:
    return contextlib.redirect_stdout(io.StringIO())


# ─────────────────────────────────────────────────────────────────────────────
# Insumos inventados
# ─────────────────────────────────────────────────────────────────────────────


def _fuentes() -> dict[str, pd.DataFrame]:
    """Dos fuentes pequeñas: 4 entidades con NIT en ambas, 2 sin NIT, 3 solas."""
    padron = pd.DataFrame(
        [
            {
                "NIT": "800100200",
                "RAZON_SOCIAL": "TORNILLOS DEL CARIBE SAS",
                "CIUDAD": "BARRANQUILLA",
            },
            {"NIT": "800100201", "RAZON_SOCIAL": "PANELA LA MOLIENDA LTDA", "CIUDAD": "CALI"},
            {"NIT": "800100202", "RAZON_SOCIAL": "VIDRIOS ANDINOS SA", "CIUDAD": "BOGOTA"},
            {"NIT": "800100203", "RAZON_SOCIAL": "CUEROS DEL NORTE SAS", "CIUDAD": "CUCUTA"},
            {"NIT": "", "RAZON_SOCIAL": "HELADERIA COPO NEVADO LTDA", "CIUDAD": "PASTO"},
            {"NIT": "", "RAZON_SOCIAL": "LIBRERIA PAGINAS ABIERTAS LTDA", "CIUDAD": "NEIVA"},
            {"NIT": "", "RAZON_SOCIAL": "ACUARIO CORAL ESMERALDA", "CIUDAD": "SANTA MARTA"},
        ]
    )
    clientes = pd.DataFrame(
        [
            {
                "NIT": "800100200",
                "RAZON_SOCIAL": "TORNILLOS DEL CARIBE S.A.S.",
                "CIUDAD": "BARRANQUILLA",
            },
            {"NIT": "800100201", "RAZON_SOCIAL": "PANELA LA MOLIENDA LIMITADA", "CIUDAD": "CALI"},
            {"NIT": "800100202", "RAZON_SOCIAL": "VIDRIOS ANDINOS S.A.", "CIUDAD": "BOGOTA"},
            {"NIT": "800100203", "RAZON_SOCIAL": "CUEROS DEL NORTE S A S", "CIUDAD": "CUCUTA"},
            {"NIT": "", "RAZON_SOCIAL": "HELADERIA COPO NEVADO LIMITADA", "CIUDAD": "PASTO"},
            {"NIT": "", "RAZON_SOCIAL": "LIBRERIA PAGINAS ABIERTAS LIMITADA", "CIUDAD": "NEIVA"},
            {"NIT": "", "RAZON_SOCIAL": "TORNERIA PISTON GIRATORIO", "CIUDAD": "IBAGUE"},
            {"NIT": "", "RAZON_SOCIAL": "JOYERIA PERLA BRILLANTE", "CIUDAD": "ARMENIA"},
        ]
    )
    return {"PADRON": padron.astype(str), "CLIENTES": clientes.astype(str)}


def _fuentes_csv(tmp_path: Path) -> list[SourceSpec]:
    fuentes = _fuentes()
    rutas = {}
    for nombre, df in fuentes.items():
        ruta = tmp_path / f"{nombre.lower()}.csv"
        df.to_csv(ruta, index=False)
        rutas[nombre] = ruta
    return [
        SourceSpec(
            name=nombre,
            path=ruta,
            column_mapping={"NIT": "NIT", "RAZON_SOCIAL": "RAZON_SOCIAL"},
            delimiter=",",
            column_types={"NIT": ColumnType.IDENTIFIER},
        )
        for nombre, ruta in rutas.items()
    ]


def _base_importadores() -> pd.DataFrame:
    """Un importador con 6 grafías (grupo > 5 filas → BAJA), otro con 2 (MEDIA)."""
    filas = [
        ("ACME TRADING LLC", "ESTADOS UNIDOS", 100.0),
        ("ACME TRADING L.L.C.", "Estados Unidos", 50.0),
        ("ACME TRADING", "ESTADOS UNIDOS", 25.0),
        ("ACME TRADING LLC.", "estados unidos de america", 40.0),
        ("ACME TRADING, LLC", "ESTADOS UNIDOS DE AMÉRICA", 30.0),
        ("ACME TRADING  LLC", "ESTADOS UNIDOS", 20.0),
        ("BETA LOGISTICS INC", "ESTADOS UNIDOS", 70.0),
        ("BETA LOGISTICS INC.", "ESTADOS UNIDOS", 10.0),
        ("GAMMA IMPORTS GMBH", "ALEMANIA", 30.0),
    ]
    return pd.DataFrame(filas, columns=["RAZON_SOCIAL", "PAIS", "FOB"])


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: un resultado por camino (una corrida por módulo)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def res_linkage(tmp_path_factory: pytest.TempPathFactory) -> Any:
    with _silencio():
        return rl.linkage(
            _fuentes(),
            work_dir=str(tmp_path_factory.mktemp("linkage")),
            skip_reporting=True,
            col_ciudad="CIUDAD",
        )


@pytest.fixture(scope="module")
def res_link(tmp_path_factory: pytest.TempPathFactory) -> Any:
    fuentes = _fuentes()
    with _silencio():
        return rl.link(
            fuentes["PADRON"],
            fuentes["CLIENTES"],
            nombre_a="PADRON",
            nombre_b="CLIENTES",
            work_dir=str(tmp_path_factory.mktemp("link")),
            skip_reporting=True,
        )


@pytest.fixture(scope="module")
def dir_dedupe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Carpeta de ``dedupe()``: ahí queda el golden por régimen que no viaja en memoria."""
    return tmp_path_factory.mktemp("dedupe")


@pytest.fixture(scope="module")
def res_dedupe(dir_dedupe: Path) -> Any:
    fuentes = _fuentes()
    df = pd.concat(fuentes.values(), ignore_index=True)
    with _silencio():
        return rl.dedupe(df, output_dir=str(dir_dedupe))


@pytest.fixture(scope="module")
def res_cruce_pandas(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("cruce_pandas")
    with _silencio():
        return ejecutar_cruce(config_cruce(_fuentes_csv(tmp), tmp))


@pytest.fixture(scope="module")
def res_cruce_duckdb(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("cruce_duckdb")
    with _silencio():
        return ejecutar_cruce(
            config_cruce(
                _fuentes_csv(tmp),
                tmp,
                motor_ingesta="duckdb",
                modo_resultado="disco",
                duckdb_settings=DuckDBIngestionSettings(
                    memory_limit="256MB", threads=2, temp_directory=tmp / "spill"
                ),
            )
        )


@pytest.fixture(scope="module")
def res_importadores() -> Any:
    cfg = ConfigImportadores(
        col_razon_social="RAZON_SOCIAL",
        col_pais="PAIS",
        cols_metricas=("FOB",),
        col_peso_economico="FOB",
        verboso=False,
    )
    return deduplicar_importadores(_base_importadores(), cfg)


def _tablas(
    nombre: str, request: pytest.FixtureRequest
) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """``(correlativa, golden)`` como DataFrames, cualquiera sea la forma del camino."""
    res = request.getfixturevalue(f"res_{nombre}")
    if nombre == "importadores":
        return res.correlativa, res.golden
    correl, golden = res.correlativa, res.golden
    if not isinstance(correl, pd.DataFrame):  # TablaParquet (cruce en disco)
        correl = correl.to_pandas()
    if golden is not None and not isinstance(golden, pd.DataFrame):
        golden = golden.to_pandas()
    return correl, golden


# ``dedupe`` no está aquí: su correlativa lleva CONFIANZA nula por diseño y su
# golden vive en disco; lo cubre test_dedupe_aplica_la_regla_en_el_golden_de_cada_regimen.
CAMINOS = ("linkage", "link", "cruce_pandas", "cruce_duckdb", "importadores")


# ─────────────────────────────────────────────────────────────────────────────
# La regla, una vez
# ─────────────────────────────────────────────────────────────────────────────


def test_la_regla_solo_produce_el_vocabulario_del_contrato() -> None:
    casos = pd.DataFrame(
        {
            "NIT_VARIATIONS": [0, 1, 1, 2, 3, 1],
            "SOURCES_COUNT": [1, 2, 1, 1, 2, 1],
            "RECORD_COUNT": [1, 2, 5, 5, 3, 6],
        }
    )
    assert list(confianza_de_grupo(casos)) == ["MEDIA", "ALTA", "MEDIA", "MEDIA", "BAJA", "BAJA"]
    assert set(confianza_de_grupo(casos)) <= NIVELES
    assert contrato.NIVELES_CONFIANZA == ("ALTA", "MEDIA", "BAJA")
    # Los niveles salen del contrato, no de literales propios de la función.
    fuente = inspect.getsource(confianza_de_grupo)
    assert "NIVELES_CONFIANZA" in fuente
    assert '"ALTA"' not in fuente and '"MEDIA"' not in fuente and '"BAJA"' not in fuente


def test_el_generador_del_golden_no_lleva_una_copia_de_la_regla() -> None:
    assert not hasattr(generador_golden.GoldenRecordGeneratorV7, "_calcular_confianza")
    fuente = inspect.getsource(generador_golden)
    assert 'return "ALTA"' not in fuente and 'return "MEDIA"' not in fuente


def test_el_diccionario_cita_la_regla_en_cada_tabla() -> None:
    texto = contrato._DEFINICION_CONFIANZA
    assert NOMBRE_REGLA in texto and "NIVELES_CONFIANZA" in texto
    for nivel in contrato.NIVELES_CONFIANZA:
        assert nivel in texto
    for columnas in (contrato.CORRELATIVA, contrato.GOLDEN, contrato.ENLACES):
        col = next(c for c in columnas if c.nombre == "CONFIANZA")
        assert NOMBRE_REGLA in col.significado
    correl = pd.DataFrame({c.nombre: pd.Series(dtype=str) for c in contrato.CORRELATIVA})
    golden = pd.DataFrame({c.nombre: pd.Series(dtype=str) for c in contrato.GOLDEN})
    dic = contrato.diccionario({"correlativa": correl, "golden": golden})
    filas = dic[dic["columna"] == "CONFIANZA"]
    assert set(filas["tabla"]) == {"correlativa", "golden"}
    assert filas["significado"].str.contains(NOMBRE_REGLA, regex=False).all()


# ─────────────────────────────────────────────────────────────────────────────
# Los cinco caminos
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("camino", CAMINOS)
def test_vocabulario_de_confianza_en_cada_camino(
    camino: str, request: pytest.FixtureRequest
) -> None:
    correl, golden = _tablas(camino, request)
    assert "CONFIANZA" in correl.columns, f"{camino}: la correlativa no trae CONFIANZA"
    valores = set(correl["CONFIANZA"].dropna().unique())
    assert valores <= NIVELES, f"{camino}: correlativa fuera del vocabulario: {valores - NIVELES}"
    if golden is not None:
        assert "CONFIANZA" in golden.columns, f"{camino}: el golden no trae CONFIANZA"
        assert not golden["CONFIANZA"].isna().any(), f"{camino}: golden con CONFIANZA nula"
        assert set(golden["CONFIANZA"]) <= NIVELES


@pytest.mark.parametrize("camino", ("linkage", "link", "cruce_pandas", "cruce_duckdb"))
def test_la_confianza_del_golden_es_la_regla_sobre_sus_metricas(
    camino: str, request: pytest.FixtureRequest
) -> None:
    correl, golden = _tablas(camino, request)
    assert golden is not None
    esperado = confianza_de_grupo(golden[["NIT_VARIATIONS", "SOURCES_COUNT", "RECORD_COUNT"]])
    assert list(golden["CONFIANZA"]) == list(esperado)
    por_grupo = golden.set_index("ID_GRUPO")["CONFIANZA"]
    assert (correl["CONFIANZA"] == correl["ID_GRUPO"].map(por_grupo)).all()
    # La regla discrimina de verdad en este insumo: NIT en dos fuentes → ALTA;
    # nombre sin NIT en dos fuentes y registros solos → MEDIA.
    assert {"ALTA", "MEDIA"} <= set(golden["CONFIANZA"])


def test_dedupe_aplica_la_regla_en_el_golden_de_cada_regimen(
    res_dedupe: Any, dir_dedupe: Path
) -> None:
    """``dedupe()`` deja un golden por régimen (``con_nit``, ``sin_nit``) en disco;
    cada uno trae CONFIANZA del vocabulario y es la regla sobre sus métricas."""
    assert res_dedupe.golden is None  # por eso se lee de disco
    goldens = sorted(
        dir_dedupe.glob("*/intermediate_checkpoints/checkpoint_05_golden_final.parquet")
    )
    assert goldens, f"dedupe no dejó golden en {dir_dedupe}"
    for ruta in goldens:
        g = pd.read_parquet(ruta)
        regimen = ruta.relative_to(dir_dedupe)
        assert "CONFIANZA" in g.columns, f"{regimen}: el golden no trae CONFIANZA"
        assert not g["CONFIANZA"].isna().any(), f"{regimen}: golden con CONFIANZA nula"
        assert set(g["CONFIANZA"]) <= NIVELES, f"{regimen}: fuera del vocabulario"
        esperado = confianza_de_grupo(g[["NIT_VARIATIONS", "SOURCES_COUNT", "RECORD_COUNT"]])
        assert list(g["CONFIANZA"]) == list(esperado), f"{regimen}: no es la regla única"


def test_importadores_aplica_la_regla_con_sus_metricas(res_importadores: Any) -> None:
    golden, correl = res_importadores.golden, res_importadores.correlativa
    assert res_importadores.todo_ok, res_importadores.invariantes.to_string(index=False)
    # Sin identificador y con una sola fuente, la regla deja MEDIA a los grupos
    # de hasta 5 filas y BAJA a los mayores: ACME (6 grafías) → BAJA; BETA → MEDIA.
    por_nombre = golden.set_index("RAZON_SOCIAL_FINAL")["CONFIANZA"]
    acme = golden[golden["RAZON_SOCIAL_FINAL"].str.startswith("ACME")]
    assert len(acme) == 1 and int(acme["N_FILAS_ORIGEN"].iloc[0]) == 6
    assert acme["CONFIANZA"].iloc[0] == "BAJA"
    assert por_nombre[por_nombre.index.str.startswith("BETA")].iloc[0] == "MEDIA"
    assert por_nombre[por_nombre.index.str.startswith("GAMMA")].iloc[0] == "MEDIA"
    esperado = confianza_de_grupo(
        pd.DataFrame(
            {
                "NIT_VARIATIONS": 0,
                "SOURCES_COUNT": 1,
                "RECORD_COUNT": golden["N_FILAS_ORIGEN"].to_numpy(),
            }
        )
    )
    assert list(golden["CONFIANZA"]) == list(esperado)
    por_grupo = golden.set_index("ID_IMPORTADOR")["CONFIANZA"]
    assert (correl["CONFIANZA"] == correl["ID_IMPORTADOR"].map(por_grupo)).all()
    assert "CONFIANZA" in res_importadores.revision.columns
    inv = res_importadores.invariantes
    fila = inv[inv["invariante"].str.contains("CONFIANZA")]
    assert len(fila) == 1 and bool(fila["cumple"].iloc[0])
