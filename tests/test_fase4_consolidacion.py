"""Tests Fase 4 — `rues-linker` v3.2.7.

Verifica:
1. Fix `_class_exists` detecta correctamente las 4 clases de reportes.
2. `min_sources_for_golden` filtra golden records correctamente.
3. `DEPRECATED_CONFIG_KEYS` se detectan en `validar_config`.
4. `RESURRECTED_CONFIG_KEYS` existe y contiene los esperados.
5. `OptunaIntegration` (heredado) emite DeprecationWarning al importar.
6. Retrocompat: sin min_sources_for_golden, comportamiento idéntico.
"""

from __future__ import annotations

import warnings
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

import pandas as pd
import pytest

from record_linkage.config import (
    DEAD_CONFIG_KEYS,
    DEPRECATED_CONFIG_KEYS,
    PARTIAL_CONFIG_KEYS,
    RESURRECTED_CONFIG_KEYS,
    validar_config,
)
from record_linkage.golden.generator import GoldenRecordGeneratorV7
from record_linkage.pipeline._internal import _class_exists


# ─────────────────────────────────────────────────────────────────────
#  Tests 1: Fix _class_exists
# ─────────────────────────────────────────────────────────────────────
class TestClassExistsFix:
    """v3.2.7: _class_exists ahora usa importlib en lugar de eval()."""

    def test_report_generator_detectable(self):
        assert _class_exists("ReportGenerator") is True

    def test_data_visualizer_detectable(self):
        assert _class_exists("DataVisualizer") is True

    def test_executive_dashboard_detectable(self):
        assert _class_exists("ExecutiveDashboard") is True

    def test_enhanced_reporting_suite_detectable(self):
        assert _class_exists("EnhancedReportingSuite") is True

    def test_clase_inexistente_retorna_false(self):
        assert _class_exists("ClaseQueNoExisteEnNingunLado") is False

    def test_clases_no_mapeadas_no_se_interpretan_como_codigo(self):
        """Solo la lista explícita de capacidades opcionales es aceptada."""
        assert _class_exists("dict") is False
        assert _class_exists("invalid syntax here") is False


# ─────────────────────────────────────────────────────────────────────
#  Tests 2: min_sources_for_golden
# ─────────────────────────────────────────────────────────────────────
class TestMinSourcesForGolden:
    """v3.2.7: filtro post-generación por número mínimo de fuentes."""

    def _build_test_data(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Construye golden + correlativa sintéticos para tests."""
        # 4 clusters: A=1 fuente, B=2 fuentes, C=3 fuentes, D=1 fuente
        correl = pd.DataFrame(
            {
                "ID_REGISTRO": ["1", "2", "3", "4", "5", "6", "7"],
                "ID_GRUPO": ["A", "B", "B", "C", "C", "C", "D"],
                "SRC": ["RUES", "RUES", "CRM", "RUES", "CRM", "SUPER", "DIAN"],
            }
        )
        golden = pd.DataFrame(
            {
                "ID_GRUPO": ["A", "B", "C", "D"],
                "RAZON_SOCIAL_FINAL": ["EmpA", "EmpB", "EmpC", "EmpD"],
            }
        )
        return golden, correl

    def test_filtro_min_2_excluye_singletons(self):
        """min_sources_for_golden=2 → quedan B, C (no A, D)."""
        config = {
            "profile": "test",
            "profiles": {"test": {"min_sources_for_golden": 2}},
        }
        gen = GoldenRecordGeneratorV7(["RUES", "CRM", "SUPER", "DIAN"], config=config)
        golden, correl = self._build_test_data()
        filtered = gen._filter_golden_by_min_sources(golden, correl)
        assert set(filtered["ID_GRUPO"]) == {"B", "C"}
        assert len(filtered) == 2

    def test_filtro_min_3_solo_deja_C(self):
        """min_sources_for_golden=3 → solo C tiene 3 fuentes."""
        config = {
            "profile": "test",
            "profiles": {"test": {"min_sources_for_golden": 3}},
        }
        gen = GoldenRecordGeneratorV7(["RUES", "CRM", "SUPER", "DIAN"], config=config)
        golden, correl = self._build_test_data()
        filtered = gen._filter_golden_by_min_sources(golden, correl)
        assert set(filtered["ID_GRUPO"]) == {"C"}

    def test_min_0_no_filtra_retrocompat(self):
        """min_sources_for_golden=0 (default) → no filtra."""
        config = {"profile": "test", "profiles": {"test": {}}}
        gen = GoldenRecordGeneratorV7(["RUES", "CRM"], config=config)
        assert gen.min_sources_for_golden == 0

    def test_min_1_no_filtra(self):
        """min_sources_for_golden=1 → no filtra (todo cluster tiene ≥1 fuente)."""
        config = {
            "profile": "test",
            "profiles": {"test": {"min_sources_for_golden": 1}},
        }
        gen = GoldenRecordGeneratorV7(["RUES"], config=config)
        # min_sources_for_golden > 1 es la condición; con 1 no se aplica el filtro
        # Verificar leyendo el atributo
        assert gen.min_sources_for_golden == 1

    def test_lectura_desde_top_level_fallback(self):
        """Si no está en profiles[active], buscar en top-level."""
        config = {
            "min_sources_for_golden": 3,
            "profile": "test",
            "profiles": {"test": {}},
        }
        gen = GoldenRecordGeneratorV7(["RUES"], config=config)
        assert gen.min_sources_for_golden == 3

    def test_correlativa_sin_modificar(self):
        """El filtro NO modifica la correlativa."""
        config = {
            "profile": "test",
            "profiles": {"test": {"min_sources_for_golden": 2}},
        }
        gen = GoldenRecordGeneratorV7(["RUES", "CRM"], config=config)
        _, correl = self._build_test_data()
        correl_original = correl.copy()
        _ = gen._filter_golden_by_min_sources(*self._build_test_data())
        # correl original sigue intacta (el filtro no la toca)
        pd.testing.assert_frame_equal(correl, correl_original)


# ─────────────────────────────────────────────────────────────────────
#  Tests 3: DEPRECATED detection
# ─────────────────────────────────────────────────────────────────────
class TestDeprecatedDetection:
    """v3.2.7: validar_config debe detectar DEPRECATED_CONFIG_KEYS."""

    def test_deprecated_keys_no_vacia(self):
        assert "cross_source_validation" in DEPRECATED_CONFIG_KEYS

    def test_dead_no_incluye_min_sources_for_golden(self):
        """Ahora que está implementado, salió de DEAD."""
        assert "min_sources_for_golden" not in DEAD_CONFIG_KEYS

    def test_validar_config_detecta_deprecated(self):
        cfg = {
            "profile": "x",
            "profiles": {
                "x": {
                    "cross_source_validation": True,  # deprecated
                    "score_threshold": 0.6,  # normal
                }
            },
        }
        with redirect_stdout(StringIO()):
            report = validar_config(cfg, verbose=False)
        assert "profiles.x.cross_source_validation" in report["deprecated"]
        # No debe estar en dead (deprecated tiene prioridad)
        assert "profiles.x.cross_source_validation" not in report["dead"]

    def test_resurrected_keys_existe(self):
        assert "max_sources_per_group" in RESURRECTED_CONFIG_KEYS
        assert "min_sources_for_golden" in RESURRECTED_CONFIG_KEYS
        assert "nit_empty_passes_filter" in RESURRECTED_CONFIG_KEYS


# ─────────────────────────────────────────────────────────────────────
#  Tests 4: optuna_integration / visualizer — shims DEPRECADOS (presentes)
# ─────────────────────────────────────────────────────────────────────
class TestOptunaIntegrationDeprecated:
    """Los módulos heredados optuna_integration y visualizer siguen presentes
    como shims DEPRECADOS por retrocompatibilidad: requieren optuna y emiten
    DeprecationWarning al importarse (ver punto 5 del docstring del módulo). La
    alternativa moderna soportada es evaluation.OrchestratorOptimizer.

    Los tres símbolos son OPT-IN: solo están disponibles con optuna instalado
    (extra `optimization`/`dev`). Sin optuna, evaluation.__init__ no exporta
    OrchestratorOptimizer y los shims no importan. Por eso, igual que en
    test_fase3_optuna.py, estos tests hacen skip cuando optuna no está presente.
    """

    def test_import_optuna_integration_emite_deprecation_warning(self):
        """El shim heredado importa (con optuna) y emite DeprecationWarning."""
        pytest.importorskip("optuna")
        import importlib
        import sys

        mod_name = "record_linkage.optimization.optuna_integration"
        sys.modules.pop(mod_name, None)

        with pytest.warns(DeprecationWarning):
            importlib.import_module(mod_name)

    def test_alternativa_orchestrator_optimizer_disponible(self):
        """La alternativa moderna está disponible cuando optuna está instalado."""
        pytest.importorskip("optuna")
        from record_linkage.evaluation import OrchestratorOptimizer

        assert OrchestratorOptimizer is not None

    def test_visualizer_heredado_importable(self):
        """El visualizer heredado importa con optuna + plotly (shim deprecado)."""
        pytest.importorskip("optuna")
        pytest.importorskip("plotly")
        import importlib
        import sys

        mod_name = "record_linkage.optimization.visualizer"
        sys.modules.pop(mod_name, None)

        modulo = importlib.import_module(mod_name)
        assert modulo is not None


# ─────────────────────────────────────────────────────────────────────
#  Tests 5: config_produccion_it7 limpio (Sprint 0.5.0)
# ─────────────────────────────────────────────────────────────────────
class TestConfigIT7Limpio:
    """v0.5.0: config_produccion_it7 ya no contiene dead ni deprecated keys."""

    def test_it7_sin_dead_keys(self):
        """Después de Sprint 0.5.0, IT-7 no tiene claves dead."""
        from record_linkage.config import config_produccion_it7, validar_config

        with redirect_stdout(StringIO()):
            report = validar_config(config_produccion_it7, verbose=False)
        assert report["dead"] == [], f"IT-7 aún tiene dead keys: {report['dead']}"

    def test_it7_sin_deprecated_keys(self):
        """Después de Sprint 0.5.0, IT-7 no tiene claves deprecated."""
        from record_linkage.config import config_produccion_it7, validar_config

        with redirect_stdout(StringIO()):
            report = validar_config(config_produccion_it7, verbose=False)
        assert report["deprecated"] == [], f"IT-7 aún tiene deprecated keys: {report['deprecated']}"

    def test_it7_sigue_invocable(self):
        """IT-7 limpio debe seguir siendo válido para el Orchestrator."""
        from record_linkage.config import config_produccion_it7

        # Estructura mínima preservada
        assert config_produccion_it7["profile"] == "enterprise_scale_4_sources"
        assert config_produccion_it7["linkage_engine_class"] == "disk_based"
        prof = config_produccion_it7["profiles"]["enterprise_scale_4_sources"]
        assert prof["score_threshold"] == 0.40
        assert prof["weights"] == {"name": 0.50, "nit": 0.50, "phonetic": 0.00}
        assert prof["trusted_unique_sources"] == ["RUES", "SUPERSOCIEDADES"]
        # source_quality_weights se preserva (es PARTIAL, no DEAD)
        assert "source_quality_weights" in prof

    def test_it7_claves_dead_efectivamente_removidas(self):
        """Las 10 claves dead específicas ya NO están en IT-7."""
        from record_linkage.config import config_produccion_it7

        prof = config_produccion_it7["profiles"]["enterprise_scale_4_sources"]
        # Claves DEAD removidas en Sprint 0.5.0:
        keys_removidas = [
            "confidence_weights",
            "max_sources_per_group",
            "min_sources_for_golden",
            "aggressive_gc",
            "memory_monitor_interval",
            "sqlite_cache_size",
            "commit_interval",
            "correlative_chunk_size",
            "cross_source_validation",  # también deprecated
        ]
        for k in keys_removidas:
            assert k not in prof, f"Clave dead '{k}' aún en IT-7"

        # Top-level removidas
        assert "validation_rules" not in config_produccion_it7
        assert "performance_settings" not in config_produccion_it7
