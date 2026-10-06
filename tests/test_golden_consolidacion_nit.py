"""F1.1 · Golden huérfanas tras la consolidación por NIT.

Hecho medido (b45e335, banco de 30.486): ``consolidate_groups_by_nit_balanced``
reconstruía los grupos fusionados con ``groupby("ID_GRUPO").first()`` sobre la
CORRELATIVA y pegaba al golden toda columna que no existiera en él. El golden
salía con 40 columnas (13 propias + 27 de la correlativa), las filas
fusionadas con ``PRIMARY_SOURCE``, ``SOURCES_COUNT``, ``RECORD_COUNT``,
``CONFIDENCE_SCORE``, ``CONFIANZA`` y ``REQUIRES_REVIEW`` en NaN, los enteros
ascendidos a ``float64`` y ``SRC`` NaN en el 99,9 % de las filas.

Lo que se exige aquí:

a) el golden consolidado conserva EXACTAMENTE las columnas (y dtypes) del
   golden de entrada — nunca una de la correlativa;
b) ninguna métrica queda nula;
c) la fila fusionada tiene ``RECORD_COUNT`` = suma, ``SOURCES_LIST`` /
   ``SOURCES_COUNT`` / ``NAME_VARIATIONS`` / ``NIT_VARIATIONS`` recalculados
   sobre su subconjunto, ``PRIMARY_SOURCE`` según la prioridad recibida, y
   ``CONFIANZA`` / ``CONFIDENCE_SCORE`` / ``REQUIRES_REVIEW`` con la MISMA
   regla del camino normal (paridad de plomería con ``_process_batch_vectorized``,
   de regla contra una referencia mínima, y con el SQL de ``_add_quality_metrics``).

Empresas inventadas; ningún dato real.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest

from record_linkage.golden.containment import (
    _concat_filtrado_por_columnas,
    consolidate_groups_by_nit_balanced,
)
from record_linkage.golden.generator import GoldenRecordGeneratorV7
from record_linkage.golden.metricas import (
    COLUMNAS_GOLDEN,
    COLUMNAS_METRICAS,
    PRIORIDAD_FUENTE_DESCONOCIDA,
    metricas_de_calidad,
    metricas_de_grupo,
    verificar_golden,
)
from record_linkage.pipeline.errores import GoldenInvalidoError

PRIORIDAD = ["RUES", "CRM", "EXPORTACIONES"]
NIT_TORNILLOS = "9001002001"
CREADO = "2026-10-06 10:00:00"


def _correlativa() -> pd.DataFrame:
    """Seis registros, cuatro grupos; 10, 11 y 14 comparten NIT_FINAL."""
    return pd.DataFrame(
        {
            "ID_GRUPO": pd.array([10, 10, 11, 12, 14, 14], dtype="int64"),
            "SRC": pd.array(
                ["RUES", "EXPORTACIONES", "CRM", "RUES", "CRM", "EXPORTACIONES"],
                dtype="string[pyarrow]",
            ),
            "ORIGINAL_INDEX": pd.array([0, 1, 2, 3, 4, 5], dtype="int64"),
            "NIT": pd.array(
                ["900100200", "900100200-1", "9001002001", "830555444", "900100200", "900100200"],
                dtype="string[pyarrow]",
            ),
            "NIT_OK": pd.array(
                [
                    NIT_TORNILLOS,
                    NIT_TORNILLOS,
                    NIT_TORNILLOS,
                    "8305554447",
                    "900100200",
                    "900100200",
                ],
                dtype="string[pyarrow]",
            ),
            "RAZON_SOCIAL": pd.array(
                [
                    "TORNILLOS DEL VALLE S.A.S.",
                    "TORNILLOS DEL VALLE SAS",
                    "TORNILLOS DEL VALLE",
                    "PANADERIA LUNA AZUL LTDA",
                    "TORNILLOS DEL VALLE SAS",
                    "TORNILLOS DEL VALLE SA",
                ],
                dtype="string[pyarrow]",
            ),
            "CIUDAD": pd.array(
                ["CALI", "CALI", "YUMBO", "PASTO", "CALI", "CALI"], dtype="string[pyarrow]"
            ),
            "NIT_FINAL": pd.array(
                [
                    NIT_TORNILLOS,
                    NIT_TORNILLOS,
                    NIT_TORNILLOS,
                    "8305554447",
                    NIT_TORNILLOS,
                    NIT_TORNILLOS,
                ],
                dtype="string[pyarrow]",
            ),
            "RAZON_SOCIAL_FINAL": pd.array(
                [
                    "TORNILLOS DEL VALLE S.A.S.",
                    "TORNILLOS DEL VALLE S.A.S.",
                    "TORNILLOS DEL VALLE",
                    "PANADERIA LUNA AZUL LTDA",
                    "TORNILLOS DEL VALLE SAS",
                    "TORNILLOS DEL VALLE SAS",
                ],
                dtype="string[pyarrow]",
            ),
        }
    )


def _golden() -> pd.DataFrame:
    """Golden coherente con ``_correlativa()`` y con los dtypes que produce ``_read_sql_arrow``."""
    texto = "string[pyarrow]"
    return pd.DataFrame(
        {
            "ID_GRUPO": pd.array([10, 11, 12, 14], dtype="int64"),
            "NIT_FINAL": pd.array(
                [NIT_TORNILLOS, NIT_TORNILLOS, "8305554447", NIT_TORNILLOS], dtype=texto
            ),
            "RAZON_SOCIAL_FINAL": pd.array(
                [
                    "TORNILLOS DEL VALLE S.A.S.",
                    "TORNILLOS DEL VALLE",
                    "PANADERIA LUNA AZUL LTDA",
                    "TORNILLOS DEL VALLE SAS",
                ],
                dtype=texto,
            ),
            "PRIMARY_SOURCE": pd.array(["RUES", "CRM", "RUES", "CRM"], dtype=texto),
            "SOURCES_LIST": pd.array(
                ["EXPORTACIONES|RUES", "CRM", "RUES", "CRM|EXPORTACIONES"], dtype=texto
            ),
            "SOURCES_COUNT": pd.array([2, 1, 1, 2], dtype="int64"),
            "RECORD_COUNT": pd.array([2, 1, 1, 2], dtype="int64"),
            "NAME_VARIATIONS": pd.array([2, 1, 1, 2], dtype="int64"),
            "NIT_VARIATIONS": pd.array([1, 1, 1, 1], dtype="int64"),
            "CONFIDENCE_SCORE": pd.array([0.7667, 1.0, 1.0, 0.7667], dtype="float64"),
            "CONFIANZA": pd.array(["ALTA", "MEDIA", "MEDIA", "ALTA"], dtype=texto),
            "REQUIRES_REVIEW": pd.array([0, 0, 0, 0], dtype="int64"),
            "CREATED_AT": pd.array(
                [CREADO, "2026-10-06 10:00:01", "2026-10-06 10:00:02", "2026-10-06 10:00:03"],
                dtype=texto,
            ),
        }
    )


@pytest.fixture(scope="module")
def consolidado() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    golden, correl = _golden(), _correlativa()
    golden_out, correl_out = consolidate_groups_by_nit_balanced(
        golden, correl, verbose=False, prioridad_fuentes=PRIORIDAD
    )
    return golden, correl, golden_out, correl_out


def test_fusion_ocurre_y_la_particion_es_la_esperada(consolidado) -> None:
    _golden_in, _correl_in, golden_out, correl_out = consolidado
    # El pivote es el grupo de nombre más largo (10); 11 y 14 se le unen.
    assert correl_out["ID_GRUPO"].tolist() == [10, 10, 10, 12, 10, 10]
    assert sorted(golden_out["ID_GRUPO"].tolist()) == [10, 12]


def test_golden_conserva_exactamente_sus_columnas_y_dtypes(consolidado) -> None:
    golden_in, correl_in, golden_out, _ = consolidado
    assert list(golden_out.columns) == list(COLUMNAS_GOLDEN) == list(golden_in.columns)
    ajenas = [c for c in correl_in.columns if c in golden_out.columns and c not in COLUMNAS_GOLDEN]
    assert ajenas == []
    assert golden_out.dtypes.to_dict() == golden_in.dtypes.to_dict(), (
        "los enteros ascendían a float64 al concatenar con NaN"
    )


def test_ninguna_metrica_queda_nula(consolidado) -> None:
    _, correl_in, golden_out, _ = consolidado
    nulos = golden_out[list(COLUMNAS_METRICAS)].isna().sum()
    assert int(nulos.sum()) == 0, nulos[nulos > 0].to_dict()
    verificar_golden(golden_out, correl_in.columns)


def test_fila_fusionada_recalcula_metricas_sobre_su_subconjunto(consolidado) -> None:
    golden_in, _, golden_out, _ = consolidado
    fila = golden_out.set_index("ID_GRUPO").loc[10]
    assert fila["RECORD_COUNT"] == 5  # 2 + 1 + 2
    assert fila["SOURCES_LIST"] == "CRM|EXPORTACIONES|RUES"
    assert fila["SOURCES_COUNT"] == 3
    assert fila["NAME_VARIATIONS"] == 4  # S.A.S. / SAS / (sin sufijo) / SA
    assert fila["NIT_VARIATIONS"] == 2  # sobre NIT_OK: 9001002001 y 900100200
    assert fila["PRIMARY_SOURCE"] == "RUES"  # primera de PRIORIDAD presente en el grupo
    assert fila["CONFIANZA"] == "MEDIA"  # 2 NIT y 5 filas: ni ALTA ni BAJA
    assert fila["CONFIDENCE_SCORE"] == pytest.approx(0.45)  # 0.5/2 + 0.4/4 + 0.1·min(1, 3/3)
    assert fila["REQUIRES_REVIEW"] == 0
    # Lo que NO es métrica se hereda del grupo raíz (10), no de la correlativa.
    assert fila["CREATED_AT"] == CREADO
    # Los campos finales conservan la regla histórica: primer valor no nulo
    # del subconjunto en el orden de la correlativa (fila ORIGINAL_INDEX 0).
    assert fila["NIT_FINAL"] == NIT_TORNILLOS
    assert fila["RAZON_SOCIAL_FINAL"] == "TORNILLOS DEL VALLE S.A.S."
    # El grupo no afectado sale idéntico.
    intacto_in = golden_in.set_index("ID_GRUPO").loc[12]
    intacto_out = golden_out.set_index("ID_GRUPO").loc[12]
    pd.testing.assert_series_equal(intacto_in, intacto_out, check_names=False)


def test_correlativa_readjunta_los_finales_del_grupo_fusionado(consolidado) -> None:
    _, _, golden_out, correl_out = consolidado
    finales = golden_out.set_index("ID_GRUPO")[["NIT_FINAL", "RAZON_SOCIAL_FINAL"]]
    esperado = finales.reindex(correl_out["ID_GRUPO"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        correl_out[["NIT_FINAL", "RAZON_SOCIAL_FINAL"]].reset_index(drop=True), esperado
    )


def test_prioridad_de_fuentes_decide_primary_source() -> None:
    golden_out, _ = consolidate_groups_by_nit_balanced(
        _golden(), _correlativa(), verbose=False, prioridad_fuentes=["EXPORTACIONES", "CRM", "RUES"]
    )
    assert golden_out.set_index("ID_GRUPO").loc[10, "PRIMARY_SOURCE"] == "EXPORTACIONES"
    # Un mapeo explícito {fuente: rango} también sirve (menor = gana).
    golden_out, _ = consolidate_groups_by_nit_balanced(
        _golden(), _correlativa(), verbose=False, prioridad_fuentes={"CRM": 1, "RUES": 2}
    )
    assert golden_out.set_index("ID_GRUPO").loc[10, "PRIMARY_SOURCE"] == "CRM"


def test_prioridad_es_obligatoria() -> None:
    with pytest.raises(ValueError, match="prioridad_fuentes"):
        consolidate_groups_by_nit_balanced(_golden(), _correlativa(), verbose=False)


def test_fila_fusionada_usa_la_misma_plomeria_que_el_generador(consolidado) -> None:
    """Plomería, no regla: ambos caminos llaman a ``metricas_de_grupo``, así que
    esto solo detecta divergencias de subconjunto o de dtype entre la fila
    fusionada y ``_process_batch_vectorized``. La REGLA se compara contra una
    referencia independiente en
    ``test_metricas_de_grupo_coincide_con_una_referencia_minima``."""
    _, correl_in, golden_out, correl_out = consolidado
    subconjunto = correl_in.loc[correl_out["ID_GRUPO"] == 10].copy()
    subconjunto["ID_GRUPO"] = 10
    generador = GoldenRecordGeneratorV7(PRIORIDAD, {})
    lote, _ = generador._process_batch_vectorized(
        subconjunto.drop(columns=["NIT_FINAL", "RAZON_SOCIAL_FINAL"])
    )
    assert len(lote) == 1
    columnas = [
        "SOURCES_LIST",
        "SOURCES_COUNT",
        "RECORD_COUNT",
        "NAME_VARIATIONS",
        "NIT_VARIATIONS",
        "PRIMARY_SOURCE",
        "CONFIANZA",
    ]
    esperado = lote.iloc[0][columnas].to_dict()
    obtenido = golden_out.set_index("ID_GRUPO").loc[10, columnas].to_dict()
    assert obtenido == esperado


def _metricas_de_referencia(correl: pd.DataFrame, prioridad: list[str]) -> dict[int, dict]:
    """La regla de ``_process_batch_vectorized`` escrita de la forma más simple
    posible (bucle por grupo, ``min(key=rango)``), solo para esta prueba: no
    comparte una línea con ``metricas_de_grupo``."""
    rangos = {fuente: i for i, fuente in enumerate(prioridad)}
    columna_nit = "NIT_OK" if "NIT_OK" in correl.columns else "NIT"
    referencia: dict[int, dict] = {}
    for id_grupo, grupo in correl.groupby("ID_GRUPO", sort=True):
        fuentes = grupo["SRC"].tolist()
        n_fuentes = len(set(fuentes))
        n_filas = len(grupo)
        n_nits = len(set(grupo[columna_nit].dropna()))
        if n_nits == 1 and n_fuentes >= 2:
            confianza = "ALTA"
        elif n_nits <= 2 and n_filas <= 5:
            confianza = "MEDIA"
        else:
            confianza = "BAJA"
        referencia[int(id_grupo)] = {
            "SOURCES_LIST": "|".join(sorted(set(fuentes))),
            "SOURCES_COUNT": n_fuentes,
            "RECORD_COUNT": n_filas,
            "NAME_VARIATIONS": len(set(grupo["RAZON_SOCIAL"].dropna())),
            "NIT_VARIATIONS": n_nits,
            # El empate lo gana la primera fila del grupo en el orden de correl.
            "PRIMARY_SOURCE": min(
                fuentes, key=lambda f: rangos.get(f, PRIORIDAD_FUENTE_DESCONOCIDA)
            ),
            "CONFIANZA": confianza,
        }
    return referencia


def _correlativa_variada() -> pd.DataFrame:
    """Los cuatro grupos de ``_correlativa()`` más tres que cubren BAJA, una
    fuente no declarada en la prioridad y el empate entre dos desconocidas."""
    extra = pd.DataFrame(
        {
            # 20: 6 filas, 3 NIT, 3 fuentes (una sin prioridad) → BAJA; gana RUES.
            # 21: 2 filas, 1 NIT, ADUANA + CRM → ALTA; gana CRM sobre la desconocida.
            # 22: 2 desconocidas con el mismo rango → gana la primera fila (SUPERSOC).
            "ID_GRUPO": [20, 20, 20, 20, 20, 20, 21, 21, 22, 22],
            "SRC": [
                "ADUANA",
                "CRM",
                "RUES",
                "CRM",
                "ADUANA",
                "CRM",
                "ADUANA",
                "CRM",
                "SUPERSOC",
                "ADUANA",
            ],
            "RAZON_SOCIAL": [
                "FERRETERIA EL CLAVO SAS",
                "FERRETERIA EL CLAVO",
                "FERRETERIA EL CLAVO S.A.S.",
                "FERRETERIA EL CLAVO",
                "FERRETERIA EL CLAVO LTDA",
                "FERRETERIA EL CLAVO",
                "LACTEOS LA NUBE SAS",
                "LACTEOS LA NUBE S.A.S.",
                "VIDRIOS DEL SUR",
                "VIDRIOS DEL SUR SAS",
            ],
            "NIT_OK": [
                "8001112221",
                "8001112221",
                "8001112222",
                "8001112223",
                "8001112221",
                "8001112222",
                "9005556661",
                "9005556661",
                "9007778881",
                "9007778882",
            ],
        }
    )
    base = _correlativa()[["ID_GRUPO", "SRC", "RAZON_SOCIAL", "NIT_OK"]].astype(object)
    return pd.concat([base, extra], ignore_index=True)


def test_metricas_de_grupo_coincide_con_una_referencia_minima() -> None:
    """Paridad de REGLA: la versión vectorizada y la referencia por grupo dan lo mismo."""
    correl = _correlativa_variada()
    referencia = _metricas_de_referencia(correl, PRIORIDAD)
    obtenido = metricas_de_grupo(correl, PRIORIDAD)

    assert obtenido.index.tolist() == sorted(referencia)
    assert obtenido.to_dict("index") == referencia
    # La referencia cubre de verdad las tres confianzas y el empate de desconocidas.
    assert {r["CONFIANZA"] for r in referencia.values()} == {"ALTA", "MEDIA", "BAJA"}
    assert referencia[22]["PRIMARY_SOURCE"] == "SUPERSOC"


def test_golden_categorico_sin_la_categoria_nueva_falla_en_vez_de_dejar_nan() -> None:
    """``astype`` a un Categorical sin la categoría recalculada da NaN sin error;
    la consolidación lo dice al convertir, no ``verificar_golden`` después."""
    golden = _golden()
    golden["SOURCES_LIST"] = golden["SOURCES_LIST"].astype("category")
    with pytest.raises(GoldenInvalidoError, match="SOURCES_LIST") as info:
        consolidate_groups_by_nit_balanced(
            golden, _correlativa(), verbose=False, prioridad_fuentes=PRIORIDAD
        )
    assert "category" in str(info.value) and "Qué hacer" in str(info.value)


def test_metricas_de_grupo_exige_columnas_de_la_correlativa() -> None:
    sin_src = _correlativa().drop(columns=["SRC"])
    with pytest.raises(KeyError, match="SRC"):
        metricas_de_grupo(sin_src, PRIORIDAD)
    with pytest.raises(TypeError, match="cadena"):
        metricas_de_grupo(_correlativa(), "RUES")


def test_consolidacion_falla_antes_de_mutar_si_la_correlativa_no_trae_src() -> None:
    correl = _correlativa().drop(columns=["SRC"])
    original = correl.copy(deep=True)
    with pytest.raises(KeyError, match="SRC"):
        consolidate_groups_by_nit_balanced(
            _golden(), correl, verbose=False, copiar_correlativa=False, prioridad_fuentes=PRIORIDAD
        )
    pd.testing.assert_frame_equal(correl, original)


def test_columna_extra_del_golden_se_hereda_de_la_raiz() -> None:
    golden = _golden()
    golden["ETIQUETA"] = pd.array(["raiz", "b", "c", "d"], dtype="string[pyarrow]")
    golden_out, _ = consolidate_groups_by_nit_balanced(
        golden, _correlativa(), verbose=False, prioridad_fuentes=PRIORIDAD
    )
    assert list(golden_out.columns) == list(golden.columns)
    assert golden_out.set_index("ID_GRUPO").loc[10, "ETIQUETA"] == "raiz"


def test_concat_filtrado_solo_conserva_columnas_del_golden() -> None:
    golden = pd.DataFrame(
        {
            "ID_GRUPO": pd.array([0, 1, 2], dtype="int64"),
            "NIT_FINAL": pd.array(["a", "b", "c"], dtype="string[pyarrow]"),
        }
    )
    nuevo = pd.DataFrame(
        {
            "ID_GRUPO": pd.array([1], dtype="int64"),
            "NIT_FINAL": pd.array(["bb"], dtype="string[pyarrow]"),
            "SRC": pd.array(["RUES"], dtype="string[pyarrow]"),  # de la correlativa: se ignora
        }
    )
    resultado = _concat_filtrado_por_columnas(golden, nuevo, {1})
    assert list(resultado.columns) == ["ID_GRUPO", "NIT_FINAL"]
    assert resultado["ID_GRUPO"].tolist() == [0, 2, 1]
    assert resultado["NIT_FINAL"].tolist() == ["a", "c", "bb"]
    with pytest.raises(ValueError, match="NIT_FINAL"):
        _concat_filtrado_por_columnas(golden, nuevo.drop(columns=["NIT_FINAL"]), {1})


def test_verificar_golden_detecta_nan_y_columnas_de_la_correlativa() -> None:
    golden, correl = _golden(), _correlativa()
    verificar_golden(golden, correl.columns)

    con_nan = golden.copy()
    con_nan.loc[0, "SOURCES_COUNT"] = pd.NA
    with pytest.raises(GoldenInvalidoError, match="SOURCES_COUNT"):
        verificar_golden(con_nan, correl.columns)

    con_intrusa = golden.copy()
    con_intrusa["SRC"] = "RUES"
    with pytest.raises(GoldenInvalidoError, match="SRC"):
        verificar_golden(con_intrusa, correl.columns)

    with pytest.raises(GoldenInvalidoError, match="CREATED_AT"):
        verificar_golden(golden.drop(columns=["CREATED_AT"]), correl.columns)

    repetido = pd.concat([golden, golden.iloc[[0]]], ignore_index=True)
    with pytest.raises(GoldenInvalidoError, match="repetido"):
        verificar_golden(repetido, correl.columns)


def test_paridad_sql_pandas_metricas_de_calidad() -> None:
    """``_add_quality_metrics`` (SQL) y ``metricas_de_calidad`` (pandas) dan lo mismo bit a bit.

    La malla incluye los empates exactos de ``ROUND(…, 4)`` (NIT_VARIATIONS
    = 16 → 0.5/16 = 0.03125) donde numpy redondea al par y SQLite se aleja de
    cero, y NIT_FINAL nulo, vacío y corto para ``LENGTH``.
    """
    nit_var, name_var, fuentes = np.meshgrid(
        np.arange(1, 41), np.arange(1, 41), np.arange(1, 7), indexing="ij"
    )
    n = nit_var.size
    filas = np.array([1, 5, 21])[np.arange(n) % 3]
    nits = np.array([NIT_TORNILLOS, "12345", "", None], dtype=object)[np.arange(n) % 4]
    golden = pd.DataFrame(
        {
            "ID_GRUPO": np.arange(n, dtype=np.int64),
            "NIT_FINAL": nits,
            "SOURCES_COUNT": fuentes.ravel().astype(np.int64),
            "RECORD_COUNT": filas.astype(np.int64),
            "NAME_VARIATIONS": name_var.ravel().astype(np.int64),
            "NIT_VARIATIONS": nit_var.ravel().astype(np.int64),
        }
    )

    generador = GoldenRecordGeneratorV7(["A"], {})
    with sqlite3.connect(":memory:") as conexion:
        generador._prepare_output_database_v2(conexion)
        golden.to_sql("golden_records", conexion, if_exists="append", index=False)
        generador._add_quality_metrics(conexion)
        sql = pd.read_sql_query(
            'SELECT "ID_GRUPO", "CONFIDENCE_SCORE", "REQUIRES_REVIEW" '
            'FROM golden_records ORDER BY "ID_GRUPO"',
            conexion,
        )

    pandas_ = metricas_de_calidad(golden)
    assert np.array_equal(
        sql["CONFIDENCE_SCORE"].to_numpy(dtype=np.float64),
        pandas_["CONFIDENCE_SCORE"].to_numpy(dtype=np.float64),
    )
    assert np.array_equal(
        sql["REQUIRES_REVIEW"].to_numpy(dtype=np.int64),
        pandas_["REQUIRES_REVIEW"].to_numpy(dtype=np.int64),
    )
    # La malla de verdad ejercitó un empate exacto (de lo contrario la prueba
    # no demostraría nada sobre el redondeo).
    empate = golden[
        (golden["NIT_VARIATIONS"] == 16)
        & (golden["NAME_VARIATIONS"] == 1)
        & (golden["SOURCES_COUNT"] == 3)
    ]
    assert len(empate) == 1
    assert pandas_.loc[empate.index[0], "CONFIDENCE_SCORE"] == 0.5313
