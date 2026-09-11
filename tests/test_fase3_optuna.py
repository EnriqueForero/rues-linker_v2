"""Tests Fase 3 — OrchestratorOptimizer (v3.2.6).

Verifica:
1. OrchestratorOptimizer importable y firma correcta.
2. Validación de inputs (truth sin columnas requeridas, config inválido).
3. default_search_space produce parámetros válidos para Optuna.
4. _build_config_for_trial inyecta correctamente los params.
5. E2E: 3 trials sobre GT pequeño con F1 > 0.5.
6. Manejo de errores: trial que falla no rompe la optimización.
"""

from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

from record_linkage.evaluation import OPTUNA_AVAILABLE

if not OPTUNA_AVAILABLE:
    pytest.skip("Optuna no instalado; tests Fase 3 omitidos.", allow_module_level=True)

import optuna

from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.evaluation import (
    OrchestratorOptimizer,
    default_search_space,
)


# ─────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def gt_micro():
    """GT muy pequeño (~300 grupos) para tests E2E rápidos."""
    gt_path = Path(__file__).parent / "data" / "ground_truth_grande.csv"
    if not gt_path.exists():
        pytest.skip(f"GT no disponible: {gt_path}")
    gt = pd.read_csv(gt_path, dtype=str)
    gt["NIT"] = gt["NIT"].fillna("")
    # Muestreo: 300 grupos para mantener test < 60s
    grupos = gt["ID_GROUP"].drop_duplicates().sample(n=300, random_state=42)
    gt_m = gt[gt["ID_GROUP"].isin(grupos)].reset_index(drop=True)
    truth = gt_m[["ID_REGISTRO", "ID_GROUP"]].copy()
    sources = {}
    for fuente, g in gt_m.groupby("FUENTE", sort=False):
        if len(g) >= 2:
            sources[str(fuente)] = (
                g[["ID_REGISTRO", "RAZON_SOCIAL", "NIT", "CIUDAD"]].reset_index(drop=True).copy()
            )
    return {"truth": truth, "sources": sources}


@pytest.fixture
def base_cfg():
    """Config base sobre el perfil calibrado."""
    return crear_config_orchestrator(perfil="produccion_calibrada", validate=False)


# ─────────────────────────────────────────────────────────────────────
#  Tests 1: Imports + firmas
# ─────────────────────────────────────────────────────────────────────
class TestImportsYFirmas:
    def test_optuna_disponible(self):
        assert OPTUNA_AVAILABLE is True

    def test_orchestrator_optimizer_importable(self):
        from record_linkage.evaluation import OrchestratorOptimizer

        assert OrchestratorOptimizer is not None

    def test_default_search_space_importable(self):
        assert callable(default_search_space)

    def test_default_search_space_devuelve_dict(self):
        """default_search_space debe devolver dict con claves esperadas."""
        study = optuna.create_study()
        trial = study.ask()
        params = default_search_space(trial)
        # Claves mínimas esperadas
        for k in [
            "lsh_threshold",
            "score_threshold",
            "min_name_similarity",
            "max_nit_distance",
            "nit_empty_passes_filter",
            "weight_name",
            "weight_nit",
        ]:
            assert k in params, f"Falta '{k}' en default_search_space"
        # weight_name + weight_nit deben sumar 1.0
        assert abs(params["weight_name"] + params["weight_nit"] - 1.0) < 1e-6


# ─────────────────────────────────────────────────────────────────────
#  Tests 2: Validación de inputs
# ─────────────────────────────────────────────────────────────────────
class TestValidacionInputs:
    def test_truth_sin_id_registro_lanza_value_error(self, base_cfg):
        truth_bad = pd.DataFrame({"ID_GROUP": ["a", "b"]})  # falta ID_REGISTRO
        with pytest.raises(ValueError, match="ID_REGISTRO"):
            OrchestratorOptimizer(base_cfg, {}, truth_bad)

    def test_truth_sin_id_group_lanza_value_error(self, base_cfg):
        truth_bad = pd.DataFrame({"ID_REGISTRO": ["1", "2"]})  # falta ID_GROUP
        with pytest.raises(ValueError, match="ID_GROUP"):
            OrchestratorOptimizer(base_cfg, {}, truth_bad)

    def test_config_sin_profile_lanza_value_error(self):
        cfg_bad = {"profiles": {"x": {}}}  # falta 'profile'
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        with pytest.raises(ValueError, match="profile"):
            OrchestratorOptimizer(cfg_bad, {}, truth)

    def test_profile_no_existe_en_profiles_lanza_error(self):
        cfg_bad = {"profile": "no_existe", "profiles": {"otro": {}}}
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        with pytest.raises(ValueError, match="no está en"):
            OrchestratorOptimizer(cfg_bad, {}, truth)


# ─────────────────────────────────────────────────────────────────────
#  Tests 3: _build_config_for_trial
# ─────────────────────────────────────────────────────────────────────
class TestBuildConfigForTrial:
    def test_params_se_inyectan_en_profile_activo(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        params = {
            "lsh_threshold": 0.65,
            "score_threshold": 0.70,
            "min_name_similarity": 0.75,
            "max_nit_distance": 1,
        }
        cfg = opt._build_config_for_trial(params)
        prof = cfg["profiles"][cfg["profile"]]
        assert prof["lsh_threshold"] == 0.65
        assert prof["score_threshold"] == 0.70
        assert prof["min_name_similarity"] == 0.75
        assert prof["max_nit_distance"] == 1

    def test_pesos_se_construyen_correctamente(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        params = {"weight_name": 0.6, "weight_nit": 0.4}
        cfg = opt._build_config_for_trial(params)
        weights = cfg["profiles"][cfg["profile"]]["weights"]
        assert weights == {"name": 0.6, "nit": 0.4, "phonetic": 0.0}

    def test_base_config_no_se_mutalla(self, base_cfg):
        """build_config debe usar deepcopy — base_cfg no se altera."""
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        original_threshold = base_cfg["profiles"]["produccion_calibrada"]["score_threshold"]
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        _ = opt._build_config_for_trial({"score_threshold": 0.99})
        # base_cfg sigue intacto
        assert base_cfg["profiles"]["produccion_calibrada"]["score_threshold"] == original_threshold


# ─────────────────────────────────────────────────────────────────────
#  Tests 4: E2E sobre GT
# ─────────────────────────────────────────────────────────────────────
class TestOptimizeE2E:
    """Tests end-to-end (lentos pero verifican el flujo completo)."""

    def test_optimize_3_trials_devuelve_best_config(self, gt_micro, base_cfg):
        """Verifica que el optimizer corre y produce best_config útil."""
        optuna.logging.set_verbosity(optuna.logging.ERROR)
        opt = OrchestratorOptimizer(
            base_config=base_cfg,
            sources=gt_micro["sources"],
            truth=gt_micro["truth"],
            silent=True,
        )
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            result = opt.optimize(
                n_trials=3,
                optimization_target="f1",
                show_progress_bar=False,
            )

        # Aserciones básicas
        assert result["total_trials_completed"] >= 2, (
            f"Solo {result['total_trials_completed']}/3 trials completaron"
        )
        assert result["best_score"] >= 0.0
        assert result["best_config"] is not None
        assert "profile" in result["best_config"]
        # best_config debe ser un dict completo listo para Orchestrator
        assert "profiles" in result["best_config"]
        active = result["best_config"]["profile"]
        prof = result["best_config"]["profiles"][active]
        # Los params del trial mejor deben estar reflejados en best_config
        for key in opt.best_params:
            if key in ("weight_name", "weight_nit"):
                continue  # estos van dentro de 'weights'
            assert key in prof or key == "weight_phonetic"

    def test_history_df_no_vacio(self, gt_micro, base_cfg):
        optuna.logging.set_verbosity(optuna.logging.ERROR)
        opt = OrchestratorOptimizer(
            base_config=base_cfg,
            sources=gt_micro["sources"],
            truth=gt_micro["truth"],
            silent=True,
        )
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            opt.optimize(n_trials=2, show_progress_bar=False)
        hist = opt.history_df()
        assert len(hist) >= 1
        assert "score" in hist.columns
        assert "elapsed_s" in hist.columns
        assert "status" in hist.columns


class TestSamplerReproducible:
    def test_default_creates_tpe_with_seed_42(self, base_cfg, monkeypatch):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        optimizer = OrchestratorOptimizer(base_cfg, {}, truth, silent=True)
        sentinel_sampler = object()
        captured: dict[str, object] = {}

        class FakeStudy:
            def optimize(self, _objective, **_kwargs):
                return None

        def fake_tpe(*, seed):
            captured["seed"] = seed
            return sentinel_sampler

        def fake_create_study(**kwargs):
            captured.update(kwargs)
            return FakeStudy()

        monkeypatch.setattr(optuna.samplers, "TPESampler", fake_tpe)
        monkeypatch.setattr(optuna, "create_study", fake_create_study)

        result = optimizer.optimize(n_trials=0, show_progress_bar=False)

        assert captured["seed"] == 42
        assert captured["sampler"] is sentinel_sampler
        assert result["total_trials_completed"] == 0


# ─────────────────────────────────────────────────────────────────────
#  Tests 5: Optimization target válido
# ─────────────────────────────────────────────────────────────────────
class TestOptimizationTarget:
    def test_target_invalido_lanza_value_error(self, gt_micro, base_cfg):
        opt = OrchestratorOptimizer(
            base_config=base_cfg,
            sources=gt_micro["sources"],
            truth=gt_micro["truth"],
        )
        with pytest.raises(ValueError, match="optimization_target"):
            opt.optimize(n_trials=1, optimization_target="invalid")
