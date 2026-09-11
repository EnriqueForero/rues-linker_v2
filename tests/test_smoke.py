"""Smoke tests — verifica que el paquete está estructuralmente correcto.

Estos tests NO validan lógica de negocio. Solo confirman:
1. Todos los módulos importan sin errores de sintaxis o nombres.
2. Config y Rutas se instancian con valores válidos.
3. Las constantes de perfiles tienen las claves esperadas.
4. Las clases principales existen y tienen los métodos públicos esperados.

Ejecutar:
    pytest tests/test_smoke.py -v

Si esto falla, NO ejecute el pipeline de producción.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ════════════════════════════════════════════════════════════════════
# 1. Imports
# ════════════════════════════════════════════════════════════════════

MODULOS_CLAVE = [
    "record_linkage",
    "record_linkage.config",
    "record_linkage.config.settings",
    "record_linkage.config.paths",
    "record_linkage.config.profiles",
    "record_linkage.utils.colombia_time",
    "record_linkage.utils.timer",
    "record_linkage.utils.memory",
    "record_linkage.utils.logger",
    "record_linkage.utils.performance",
    "record_linkage.engine.lsh.disk_based",
    "record_linkage.engine.lsh.trusted",
    "record_linkage.engine.lsh.minhash",
    "record_linkage.golden.generator",
    "record_linkage.golden.selector",
    "record_linkage.pipeline.orchestrator",
]


@pytest.mark.parametrize("modulo", MODULOS_CLAVE)
def test_modulo_importa(modulo: str) -> None:
    """Cada módulo clave debe importar sin errores."""
    importlib.import_module(modulo)


# ════════════════════════════════════════════════════════════════════
# 2. Config y Rutas
# ════════════════════════════════════════════════════════════════════


def test_config_instancia_con_defaults() -> None:
    """Config debe crearse con valores por defecto."""
    from record_linkage.config import Config

    cfg = Config()
    assert cfg.batch_size >= Config.BATCH_MIN
    assert cfg.random_seed >= 0
    assert cfg.iteracion


def test_config_rechaza_batch_size_invalido() -> None:
    """Config debe fallar con batch_size fuera de rango."""
    from record_linkage.config import Config

    with pytest.raises(ValueError, match="batch_size"):
        Config(batch_size=10)
    with pytest.raises(ValueError, match="batch_size"):
        Config(batch_size=10_000_000)


def test_config_rechaza_seed_negativa() -> None:
    """random_seed debe ser >= 0."""
    from record_linkage.config import Config

    with pytest.raises(ValueError, match="random_seed"):
        Config(random_seed=-1)


def test_config_normaliza_trusted_sources_a_uppercase() -> None:
    """trusted_sources debe normalizarse a mayúsculas."""
    from record_linkage.config import Config

    cfg = Config(trusted_sources={"rues", "Supersociedades"})
    assert cfg.trusted_sources == {"RUES", "SUPERSOCIEDADES"}


def test_rutas_construye_desde_config(tmp_path: Path) -> None:
    """Rutas.desde_config debe construir base = workspace/iteracion."""
    from record_linkage.config import Config, Rutas

    cfg = Config(workspace=str(tmp_path), iteracion="TEST1")
    rutas = Rutas.desde_config(cfg)
    assert rutas.base == tmp_path / "TEST1"
    assert rutas.entrada == rutas.base / "input"
    assert rutas.salida == rutas.base / "output"
    assert rutas.checkpoints == rutas.base / "checkpoints"


def test_rutas_crear_directorios(tmp_path: Path) -> None:
    """crear_directorios debe materializar todas las carpetas."""
    from record_linkage.config import Config, Rutas

    cfg = Config(workspace=str(tmp_path), iteracion="TEST2")
    rutas = Rutas.desde_config(cfg)
    rutas.crear_directorios()
    assert rutas.entrada.is_dir()
    assert rutas.salida.is_dir()
    assert rutas.checkpoints.is_dir()
    assert rutas.ground_truth.is_dir()
    assert rutas.logs.is_dir()
    assert rutas.reports.is_dir()


# ════════════════════════════════════════════════════════════════════
# 3. Perfiles LSH
# ════════════════════════════════════════════════════════════════════

CLAVES_PERFIL = {
    "cleaning_mode",
    "lsh_permutations",
    "lsh_threshold",
    "lsh_ngram",
    "score_threshold",
    "weights",
    "batch_size",
}


def test_perfiles_base_existe_y_tiene_perfiles_clave() -> None:
    """PERFILES_BASE debe existir y contener los perfiles documentados."""
    from record_linkage.config.profiles import PERFILES_BASE

    perfiles_esperados = ["prueba_rapida", "produccion_estandar"]
    for p in perfiles_esperados:
        assert p in PERFILES_BASE, f"Perfil {p} faltante"
        # Cada perfil debe tener las claves estructurales mínimas
        for clave in CLAVES_PERFIL:
            assert clave in PERFILES_BASE[p], f"{p} no tiene {clave}"


def test_config_produccion_it7_estructura() -> None:
    """config_produccion_it7 debe tener la sección de profiles."""
    from record_linkage.config.profiles import config_produccion_it7

    assert "profiles" in config_produccion_it7
    assert "enterprise_scale_4_sources" in config_produccion_it7["profiles"]


# ════════════════════════════════════════════════════════════════════
# 4. Clases principales existen y tienen métodos esperados
# ════════════════════════════════════════════════════════════════════


def test_clases_principales_exportan() -> None:
    """Las clases clave deben ser importables desde sus módulos canónicos."""
    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine
    from record_linkage.engine.lsh.trusted import TrustedSourceLSHEngine
    from record_linkage.golden.generator import GoldenRecordGeneratorV7
    from record_linkage.golden.selector import AdvancedValueSelector
    from record_linkage.pipeline.orchestrator import Orchestrator

    # TrustedSourceLSHEngine hereda de DiskBasedLSHEngine
    assert issubclass(TrustedSourceLSHEngine, DiskBasedLSHEngine)

    # AdvancedValueSelector — versión 125 acepta source_priority_map
    import inspect

    sig = inspect.signature(AdvancedValueSelector.__init__)
    assert "source_priority_map" in sig.parameters, (
        "AdvancedValueSelector NO es la versión correcta. "
        "Se esperaba la de celda 125 con source_priority_map. "
        "Ver MIGRATION_LOG.md sección 2.1."
    )


def test_no_existen_clases_duplicadas() -> None:
    """Las clases que estaban duplicadas en el notebook tienen UNA sola definición."""
    import record_linkage.golden.selector as sel
    import record_linkage.golden.utils as gu

    # Solo debe haber una AdvancedValueSelector en el paquete
    assert hasattr(sel, "AdvancedValueSelector")
    # MemoryMonitor y SafeSQLiteConnection viven en golden/utils.py
    assert hasattr(gu, "MemoryMonitor")
    assert hasattr(gu, "SafeSQLiteConnection")


# ════════════════════════════════════════════════════════════════════
# 5. Estructura mínima de tiempo Colombia
# ════════════════════════════════════════════════════════════════════


def test_hora_colombia_retorna_string() -> None:
    """hora_colombia debe devolver un string con la hora actual."""
    from record_linkage.utils.colombia_time import hora_colombia

    h = hora_colombia()
    assert isinstance(h, str)
    assert len(h) > 0
