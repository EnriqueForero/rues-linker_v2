"""tests/integration/test_deduplicate_unified.py

Tests end-to-end del API público `deduplicate_unified`. Validan el contrato
funcional con DataFrames sintéticos pequeños (≤50 filas) y ejecutan en
≤30s en CI.

Cobertura:
    - El pipeline retorna correlativa no vacía con defaults razonables.
    - Detección correcta de duplicados conocidos (ground truth controlado).
    - Manejo explícito de DataFrame vacío.
    - Validación de columnas requeridas (errores claros).
"""

from __future__ import annotations

import tempfile

import pandas as pd
import pytest

from record_linkage.deduplication.unified import deduplicate_unified


def test_returns_non_empty_correlative_with_default_args(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """Defaults razonables deben producir una tabla correlativa no vacía
    y con la misma cardinalidad que el input (un registro origen → una fila
    correlativa con su ID_GRUPO asignado).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, conexiones = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="BALANCEADO",
            profile="deduplication_standard",
            output_dir=tmpdir,
            validate_against_legacy=False,
        )

    assert isinstance(correlativa, pd.DataFrame)
    assert isinstance(conexiones, pd.DataFrame)
    assert not correlativa.empty
    assert len(correlativa) == len(df_single_source_with_duplicates)
    assert "ID_GRUPO" in correlativa.columns


def test_detects_known_duplicate_pairs(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """Verifica detección del ground truth sembrado:
    8 registros con 2 pares de duplicados → 6 grupos finales,
    2 grupos con >1 registro.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, _conexiones = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            output_dir=tmpdir,
        )

    n_grupos = correlativa["ID_GRUPO"].nunique()
    # Permitimos algo de holgura: el pipeline puede ser más estricto o más
    # laxo, pero la tasa de reducción debe estar entre 20% y 30%
    # (8 → 6 = 25%).
    assert 5 <= n_grupos <= 7, f"Esperado 5-7 grupos, obtuvo {n_grupos}"

    # NITs idénticos (900123456 y 800999111) DEBEN colapsarse:
    # buscamos al menos un grupo con 2 registros.
    tamanos_grupo = correlativa.groupby("ID_GRUPO").size()
    grupos_con_duplicados = (tamanos_grupo > 1).sum()
    assert grupos_con_duplicados >= 1, (
        "Esperado ≥1 grupo con duplicados. NITs idénticos no se colapsaron."
    )


def test_raises_on_empty_dataframe(df_empty: pd.DataFrame):
    """DataFrame vacío debe levantar ValueError explícito, no fallar
    silenciosamente downstream."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match=r"(?i)vac"):
            deduplicate_unified(
                df_input=df_empty,
                col_nit="NIT",
                col_name="RAZON_SOCIAL",
                output_dir=tmpdir,
            )


def test_raises_on_missing_columns():
    """Columnas requeridas ausentes deben levantar ValueError con mensaje
    explícito que mencione las columnas faltantes."""
    df_bad = pd.DataFrame({"OTRO": ["A"], "COLUMNA": ["B"]})
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match=r"(?i)NIT|RAZON_SOCIAL|columna"):
            deduplicate_unified(
                df_input=df_bad,
                col_nit="NIT",
                col_name="RAZON_SOCIAL",
                output_dir=tmpdir,
            )


def test_result_is_pipeline_result_compatible_dict(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """El cambio F1.4 (PipelineResult dict-like) NO debe romper a callers
    que esperan poder hacer `.get()` sobre el resultado interno.
    Esto se valida indirectamente: si `deduplicate_unified` retorna
    correlativa no vacía, significa que `result.get("correlative_table")`
    funciona dentro del módulo.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, _ = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            output_dir=tmpdir,
        )
    assert not correlativa.empty


# ─────────────────────────────────────────────────────────────────────────────
# F2.10 · contrato corregido y salidas por el escritor único
# ─────────────────────────────────────────────────────────────────────────────


def test_segundo_elemento_son_las_conexiones_no_triviales(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """El contrato real (desde F1.13, declarado en F2.10): la tupla es
    ``(correlativa, conexiones)``. ``conexiones`` es la tabla del contrato:
    una fila por registro de la correlativa que comparte grupo con otro, con
    las columnas fijas de ``contrato.CONEXIONES`` primero y después las del
    registro, ordenada por ``ID_GRUPO`` y ``ORIGINAL_INDEX``.
    """
    from record_linkage import contrato

    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, conexiones = deduplicate_unified(
            df_input=df_single_source_with_duplicates,
            output_dir=tmpdir,
        )

    tamanos = correlativa.groupby("ID_GRUPO")["ID_GRUPO"].transform("size")
    esperadas = correlativa[tamanos > 1]
    assert len(conexiones) == len(esperadas) >= 2
    assert set(conexiones["ORIGINAL_INDEX"]) == set(esperadas["ORIGINAL_INDEX"])
    assert (conexiones["RECORD_COUNT"] > 1).all()
    assert list(conexiones.columns[: len(contrato.COLUMNAS_CONEXIONES)]) == list(
        contrato.COLUMNAS_CONEXIONES
    )
    # Las columnas del registro (fuente y técnicas) siguen ahí, después de las fijas.
    assert {"NIT", "RAZON_SOCIAL", "NIT_OK", "NOMBRE_LIMPIO"} <= set(conexiones.columns)
    assert set(conexiones.columns) == set(correlativa.columns) | {"RECORD_COUNT"}
    orden = conexiones[["ID_GRUPO", "ORIGINAL_INDEX"]].to_numpy().tolist()
    assert orden == sorted(orden)


def test_salidas_pasan_por_el_escritor_unico(
    df_single_source_with_duplicates: pd.DataFrame,
    tmp_path,
):
    """``output_dir`` queda con ``correlativa.parquet`` y ``conexiones.parquet``
    (los nombres del estándar), escritos con las primitivas del escritor
    único: deterministas y, en ``conexiones.parquet``, con los tipos y el
    metadato del contrato. Los nombres de v1 (``correlativa_unificada``,
    ``conexiones_no_triviales``) no se escriben más.
    """
    import pyarrow.parquet as pq

    from record_linkage import contrato
    from record_linkage.deduplication.unified import rutas_salida

    salida = tmp_path / "salida"
    correlativa, conexiones = deduplicate_unified(
        df_input=df_single_source_with_duplicates, output_dir=str(salida)
    )
    rutas = rutas_salida(salida)
    assert rutas["correlativa"] == salida / "correlativa.parquet"
    assert rutas["conexiones"] == salida / "conexiones.parquet"
    assert rutas["correlativa"].is_file() and rutas["conexiones"].is_file()
    assert not (salida / "correlativa_unificada.parquet").exists()
    assert not (salida / "conexiones_no_triviales.parquet").exists()

    # conexiones.parquet tiene la forma de la tabla del contrato.
    esquema = pq.read_schema(rutas["conexiones"])
    assert esquema.metadata[b"contrato"].decode() == contrato.VERSION_CONTRATO
    for col in contrato.CONEXIONES:
        assert esquema.field(col.nombre).type == col.tipo, col.nombre
    leida = pd.read_parquet(rutas["conexiones"])
    assert len(leida) == len(conexiones)
    assert list(leida.columns) == list(conexiones.columns)

    # correlativa.parquet es la tabla de trabajo del motor (antes de completar):
    # NO dice que cumple el contrato, y devuelve exactamente lo que se retornó.
    esquema_correl = pq.read_schema(rutas["correlativa"])
    assert not esquema_correl.metadata or b"contrato" not in esquema_correl.metadata
    leida = pd.read_parquet(rutas["correlativa"])
    pd.testing.assert_frame_equal(leida, correlativa.reset_index(drop=True))
    assert {"NIT_OK", "NIT_BASE", "NOMBRE_LIMPIO"} <= set(leida.columns)


def test_validate_against_legacy_true_falla_porque_no_valida_nada(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """El parámetro quedó inerte cuando se retiró el motor heredado. Aceptar
    ``True`` en silencio prometería una validación que no ocurre."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="validate_against_legacy"):
            deduplicate_unified(
                df_input=df_single_source_with_duplicates,
                output_dir=tmpdir,
                validate_against_legacy=True,
            )


def test_los_errores_de_unified_usan_el_formato_del_modulo_de_errores(
    df_single_source_with_duplicates: pd.DataFrame,
):
    """Los dos mensajes de tres secciones de ``unified.py`` se arman con
    ``pipeline.errores.mensaje_accionable`` (una regla se escribe una vez):
    una sección por línea, no un texto a mano en una sola línea."""
    from record_linkage.deduplication.unified import _generate_non_trivial_connections

    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError) as exc_legacy:
            deduplicate_unified(
                df_input=df_single_source_with_duplicates,
                output_dir=tmpdir,
                validate_against_legacy=True,
            )
    with pytest.raises(ValueError) as exc_conexiones:
        _generate_non_trivial_connections(pd.DataFrame({"ID_GRUPO": [1, 1]}))

    for exc in (exc_legacy, exc_conexiones):
        texto = str(exc.value)
        assert texto.startswith("Qué pasó: "), texto
        assert "\nPor qué importa: " in texto and "\nQué hacer: " in texto, texto
    assert "ORIGINAL_INDEX" in str(exc_conexiones.value)  # dice cuáles faltan


def test_fallo_de_parquet_se_relanza_nombrando_este_camino(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    """``escribir_parquet`` falla con ``EscrituraSalidaError`` cuyo remedio habla
    de ``linkage()`` y de ``validar()``; en este camino la tabla es la de
    trabajo de ``dedupe()``/``deduplicate_unified`` y el remedio es sobre
    ``df_input``. Hasta F2.10 ``SmartExporter`` degradaba a CSV.gz en silencio.
    El motor normaliza a texto las columnas mixtas (medido), así que el fallo
    se provoca en la primitiva."""
    import pyarrow as pa

    from record_linkage.deduplication import unified
    from record_linkage.pipeline.errores import EscrituraSalidaError

    def _falla(df: pd.DataFrame, ruta, columnas=()) -> None:
        try:
            raise pa.ArrowInvalid("Conversion failed for column EXTRA with type object")
        except pa.ArrowInvalid as causa:
            raise EscrituraSalidaError("pyarrow no pudo convertir la tabla a parquet") from causa

    monkeypatch.setattr(unified, "escribir_parquet", _falla)
    df = pd.DataFrame(
        {
            "NIT": ["900100200", "900100200"],
            "RAZON_SOCIAL": ["FERRETERIA INVENTADA SAS", "FERRETERIA INVENTADA S.A.S."],
        }
    )
    with pytest.raises(EscrituraSalidaError, match="deduplicate_unified") as exc:
        deduplicate_unified(df, output_dir=str(tmp_path))
    texto = str(exc.value)
    assert "correlativa.parquet" in texto
    assert "column EXTRA" in texto  # cita la causa de pyarrow, que nombra la columna
    assert "dedupe()" in texto and "\nQué hacer: " in texto
    assert "linkage()" not in texto
    assert isinstance(exc.value.__cause__, EscrituraSalidaError)
