"""tests/test_reproduce_bugs.py — Test de regresión de los 5 bugs encontrados
en la validación post-migración del notebook → paquete.

Cada test reproduce un bug específico. En la línea base v2.0.0 los cinco
DEBEN fallar. Tras aplicar los hotfixes F1.1–F1.5 los cinco DEBEN pasar.

Bugs cubiertos:
    #1 — utils/logger.py: global _LOGGING_CONFIGURED sin inicializar
    #2 — processing/nit.py: self.nit_regex inexistente (debería ser non_digit_regex)
    #3 — engine/lsh/legacy.py: tabla SQL index_data no creada en _init_database
    #4 — deduplication/unified.py: contrato roto contra RecordLinkagePipeline.run
    #5 — pipeline/_internal.py: _phase_cleanup sin @contextmanager
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Bug #1 — Logger no inicializa _LOGGING_CONFIGURED a nivel de módulo
# ─────────────────────────────────────────────────────────────────────────────
def test_bug_01_custom_logger_first_instantiation_does_not_raise():
    """Instanciar CustomLogger por primera vez en un proceso fresco no debe
    levantar NameError. La bandera de configuración global debe existir a
    nivel de módulo antes del primer uso.
    """
    # Reimport limpio para reproducir el primer-uso
    import importlib

    import record_linkage.utils.logger as logger_mod

    importlib.reload(logger_mod)
    # En la línea base esto levanta NameError: name '_LOGGING_CONFIGURED' is not defined
    instance = logger_mod.CustomLogger("test_bug_01")
    assert instance is not None
    instance.info("smoke")  # también debe poder loggear sin error


# ─────────────────────────────────────────────────────────────────────────────
# Bug #2 — AdvancedNitProcessor referencia self.nit_regex inexistente
# ─────────────────────────────────────────────────────────────────────────────
def test_bug_02_advanced_nit_processor_uses_inherited_regex():
    """enhanced_fix_nit() debe poder limpiar un NIT con caracteres no-dígito
    usando el regex heredado de la clase base. En la línea base levanta
    AttributeError: 'AdvancedNitProcessor' object has no attribute 'nit_regex'.

    Input elegido: '900-123-456' con guiones — fuerza la ejecución de
    `self.non_digit_regex.sub("", s)` que es la línea exacta del bug.
    NITs con puntos como '900.123.456' se truncan antes en split(".")[0]
    y no ejercitan el regex.
    """
    from record_linkage.processing.nit import AdvancedNitProcessor

    proc = AdvancedNitProcessor()
    # v2.10.0: enhanced_fix_nit ahora retorna 3 elementos (base, ok, dv_origen).
    nit_base, nit_ok, dv_origen = proc.enhanced_fix_nit("900-123-456")
    assert nit_base == "900123456"
    assert nit_ok.startswith("900123456")  # con DV opcional concatenado
    # Sanity: el DV de 900123456 según pesos DIAN es 8
    assert nit_ok == "9001234568"
    # El guion indica DV declarado por el usuario.
    assert dv_origen == "computed"  # 9 dígitos puros tras limpiar => computed


# ─────────────────────────────────────────────────────────────────────────────
# Bug #3 — Tabla index_data referenciada pero no creada en _init_database
# ─────────────────────────────────────────────────────────────────────────────
def test_bug_03_lsh_legacy_init_database_creates_all_required_tables():
    """_init_database debe crear TODAS las tablas que el resto del motor LSH
    legacy usa, incluida index_data. En la línea base la tabla index_data
    no se crea, lo que genera 'OperationalError: no such table: index_data'
    cuando _process_with_sqlite intenta insertar metadata de fuente.
    """
    from record_linkage.engine.lsh.legacy import OptimizedLSHEngine

    # Perfil mínimo válido para construcción
    engine = OptimizedLSHEngine(
        profile={"lsh_permutations": 128, "lsh_threshold": 0.5, "lsh_ngram": 3}
    )
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    conn = sqlite3.connect(db_path)
    try:
        engine._init_database(conn)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tablas = {row[0] for row in cur.fetchall()}
        # Estas tres tablas son contractuales: el código las usa después.
        assert "candidates" in tablas
        assert "stats" in tablas
        assert "index_data" in tablas, (
            "Bug #3: _init_database omite la tabla index_data, que _process_with_sqlite "
            "requiere para mapear idx -> fuente al filtrar por trusted sources."
        )
    finally:
        conn.close()
        Path(db_path).unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Bug #4 — deduplicate_unified no obtiene la tabla correlativa
# ─────────────────────────────────────────────────────────────────────────────
def test_bug_04_deduplicate_unified_returns_non_empty_correlative():
    """deduplicate_unified() con defaults razonables debe retornar una tabla
    correlativa no vacía. En la línea base falla con:
        RuntimeError: El pipeline no generó la tabla correlativa.
    porque RecordLinkagePipeline.run() corre con keep_intermediate_results=False
    (default) y borra results['correlative_table'] antes del return.
    """
    from record_linkage.deduplication.unified import deduplicate_unified

    # Mini DataFrame sintético con 1 duplicado obvio
    df = pd.DataFrame(
        {
            "NIT": ["900123456", "900123456", "800999111", "700555222", "700555222"],
            "RAZON_SOCIAL": [
                "ACME COLOMBIA SAS",
                "ACME COLOMBIA S.A.S.",
                "INVERSIONES BETA",
                "GAMMA INDUSTRIES SAS",
                "GAMMA INDUSTRIES S A S",
            ],
        }
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        correlativa, _conexiones = deduplicate_unified(
            df_input=df,
            col_nit="NIT",
            col_name="RAZON_SOCIAL",
            mode="BALANCEADO",
            profile="deduplication_standard",
            output_dir=tmpdir,
            validate_against_legacy=False,
        )
    assert isinstance(correlativa, pd.DataFrame)
    assert not correlativa.empty
    # 5 registros de entrada → 5 filas en correlativa (un registro por origen)
    assert len(correlativa) == len(df)
    assert "ID_GRUPO" in correlativa.columns


# ─────────────────────────────────────────────────────────────────────────────
# Bug #5 — _phase_cleanup no es un context manager
# ─────────────────────────────────────────────────────────────────────────────
def test_bug_05_phase_cleanup_is_usable_as_context_manager():
    """_phase_cleanup debe poder usarse con la sintaxis `with _phase_cleanup():`.
    En la línea base es una función con yield SIN @contextmanager, así que
    levanta:
        TypeError: 'generator' object does not support the context manager protocol
    """
    from record_linkage.pipeline._internal import _phase_cleanup

    # Si está decorado correctamente, este bloque corre sin error
    with _phase_cleanup():
        pass


if __name__ == "__main__":
    # Permitir ejecución directa para diagnóstico rápido
    pytest.main([__file__, "-v", "--tb=short", "--no-header"])
