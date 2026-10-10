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


# ─────────────────────────────────────────────────────────────────────
#  Tests 6 (F2.14): lo que el notebook de F2.15 necesita del ÚNICO optimizador
# ─────────────────────────────────────────────────────────────────────
from record_linkage.evaluation.orchestrator_hyperparameters import (  # noqa: E402
    OBJETIVOS_VALIDOS,
    espacio_de_busqueda,
)


class TestEspacioDeBusqueda:
    """``espacio_de_busqueda`` arma un espacio propio por parámetro sin que el
    usuario escriba ``trial.suggest_*`` (lo que ``ParameterSpace`` daba)."""

    def test_rangos_por_tipo(self):
        espacio = espacio_de_busqueda(
            {
                "score_threshold": (0.5, 0.9, 0.1),  # float con paso
                "lsh_threshold": (0.55, 0.75),  # float continuo
                "max_nit_distance": (0, 3),  # int
                "nit_empty_passes_filter": [True, False],  # categórico
                "cleaning_mode": "AGRESIVO",  # fijo
                "weight_name": (0.3, 0.7, 0.1),
            }
        )
        study = optuna.create_study(sampler=optuna.samplers.RandomSampler(seed=1))
        for _ in range(5):
            params = espacio(study.ask())
            assert 0.5 <= params["score_threshold"] <= 0.9
            assert 0.55 <= params["lsh_threshold"] <= 0.75
            assert isinstance(params["max_nit_distance"], int)
            assert 0 <= params["max_nit_distance"] <= 3
            assert params["nit_empty_passes_filter"] in (True, False)
            assert params["cleaning_mode"] == "AGRESIVO"
            # weight_nit se deriva como en default_search_space
            assert abs(params["weight_name"] + params["weight_nit"] - 1.0) < 1e-9

    def test_rango_invalido_falla_rapido(self):
        with pytest.raises(ValueError, match="score_threshold"):
            espacio_de_busqueda({"score_threshold": (0.9, 0.5)})
        with pytest.raises(ValueError, match="vacía"):
            espacio_de_busqueda({"cleaning_mode": []})
        with pytest.raises(ValueError, match="perfil"):
            espacio_de_busqueda({"parametro_inexistente": (0, 1)})
        # Hallazgo de revisión (ronda 1): una tupla de cadenas pasaba la
        # construcción y reventaba en ``suggest_float`` del trial 1.
        with pytest.raises(ValueError, match=r"tupla.*lista"):
            espacio_de_busqueda({"cleaning_mode": ("AGRESIVO", "SUAVE")})
        with pytest.raises(ValueError, match=r"tupla.*lista"):
            espacio_de_busqueda({"max_nit_distance": (0, "a")})

    def test_se_usa_en_el_optimizador(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        espacio = espacio_de_busqueda({"score_threshold": (0.6, 0.8, 0.1)})
        opt = OrchestratorOptimizer(base_cfg, {}, truth, search_space=espacio)
        assert opt.search_space is espacio


class TestAjustesPerfil:
    def test_params_del_trial_como_ajustes_para_linkage(self, base_cfg):
        """``best_config`` sirve al Orchestrator; ``ajustes_perfil()`` sirve a
        ``linkage(ajustes_perfil=...)``: el mismo mapeo, escrito una vez."""
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        params = {"score_threshold": 0.7, "weight_name": 0.6, "weight_nit": 0.4}
        ajustes = opt.ajustes_perfil(params)
        assert ajustes == {
            "score_threshold": 0.7,
            "weights": {"name": 0.6, "nit": 0.4, "phonetic": 0.0},
        }
        cfg = opt._build_config_for_trial(params)
        prof = cfg["profiles"][cfg["profile"]]
        assert {k: prof[k] for k in ajustes} == ajustes

    def test_sin_optimizar_falla_rapido(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        with pytest.raises(ValueError, match="optimize"):
            opt.ajustes_perfil()

    def test_ajustes_los_acepta_crear_config_orchestrator(self, base_cfg):
        """Las claves que devuelve son las del perfil: ``linkage`` las valida
        con ``crear_config_orchestrator`` y una desconocida fallaría aquí."""
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        ajustes = opt.ajustes_perfil({"score_threshold": 0.7, "max_nit_distance": 2})
        cfg = crear_config_orchestrator(perfil="produccion_calibrada", validate=False, **ajustes)
        assert cfg["profiles"]["produccion_calibrada"]["score_threshold"] == 0.7


class TestObjetivoMacroF1:
    """``macro_f1`` reutiliza ``evaluation.banco.evaluar_calidad`` (F1 por
    ESTRATO promediado): la misma regla que lee el banco, escrita una vez."""

    @staticmethod
    def _truth_estratificada():
        return pd.DataFrame(
            {
                "ID_REGISTRO": ["a1", "a2", "b1", "b2", "c1", "c2", "n1", "n2"],
                "ID_GROUP": ["A", "A", "B", "B", "C", "C", "N1", "N2"],
                "ESTRATO": ["REAL", "REAL", "REAL", "REAL", "RUIDO", "RUIDO", "RUIDO", "RUIDO"],
                "REGIMEN": ["CON_ID"] * 8,
                "CASO": ["positivo"] * 6 + ["negativo_unico"] * 2,
            }
        )

    def test_macro_f1_en_objetivos_validos(self):
        assert "macro_f1" in OBJETIVOS_VALIDOS

    def test_evaluate_calcula_macro_f1_y_fp_negativos(self, base_cfg):
        truth = self._truth_estratificada()
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        # REAL perfecto; en RUIDO se parte C y se une n1 con n2 (FP sobre negativos).
        correlativa = pd.DataFrame(
            {
                "ID_REGISTRO": truth["ID_REGISTRO"],
                "ID_GRUPO": ["g1", "g1", "g2", "g2", "g3", "g4", "g5", "g5"],
            }
        )
        score, metricas = opt._evaluate(correlativa, "macro_f1")
        assert metricas["f1_estrato_REAL"] == 1.0
        assert metricas["f1_estrato_RUIDO"] == 0.0
        assert score == metricas["macro_f1"] == 0.5
        assert metricas["fp_que_tocan_negativo"] == 1
        # El F1 global sigue estando para quien lo pida.
        assert 0 < metricas["f1"] < 1

    def test_macro_f1_sin_estrato_falla_rapido(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1", "2"], "ID_GROUP": ["a", "a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        with pytest.raises(ValueError, match="ESTRATO"):
            opt.optimize(n_trials=1, optimization_target="macro_f1", show_progress_bar=False)

    def test_macro_f1_sin_estratos_con_pares_da_cero_no_nan(self, base_cfg):
        """Hallazgo de revisión (ronda 1): ``evaluar_calidad`` devuelve NaN
        cuando ningún estrato tiene pares (grupos de un solo registro) y el
        NaN llegaba a Optuna como trial «completed»."""
        truth = pd.DataFrame(
            {
                "ID_REGISTRO": ["a", "b", "c"],
                "ID_GROUP": ["A", "B", "C"],
                "ESTRATO": ["REAL", "REAL", "RUIDO"],
            }
        )
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        correlativa = pd.DataFrame({"ID_REGISTRO": ["a", "b", "c"], "ID_GRUPO": ["x", "y", "z"]})
        score, metricas = opt._evaluate(correlativa, "macro_f1")
        assert score == 0.0 and score == score, f"score debe ser 0.0, no {score!r}"
        assert "advertencia" in metricas and "estratos" in metricas["advertencia"]
        # Con objetivo f1 la verdad sigue evaluándose igual que antes.
        score_f1, metricas_f1 = opt._evaluate(correlativa, "f1")
        assert score_f1 == score_f1 and "advertencia" not in metricas_f1

    def test_sin_estrato_no_hay_metricas_del_banco(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1", "2"], "ID_GROUP": ["a", "a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        correlativa = pd.DataFrame({"ID_REGISTRO": ["1", "2"], "ID_GRUPO": ["x", "x"]})
        score, metricas = opt._evaluate(correlativa, "f1")
        assert score == 1.0
        assert "macro_f1" not in metricas

    def test_evaluate_macro_f1_con_verdad_sin_estrato_falla_rapido(self, base_cfg):
        """Hallazgo de revisión (ronda 2): ``_validar_objetivo`` miraba solo
        ``self.truth``; una verdad explícita sin ESTRATO devolvía el F1 global
        en silencio como si fuera macro_f1."""
        truth = self._truth_estratificada()
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        correlativa = pd.DataFrame(
            {"ID_REGISTRO": truth["ID_REGISTRO"], "ID_GRUPO": ["g1"] * 4 + ["g2"] * 4}
        )
        with pytest.raises(ValueError, match="ESTRATO"):
            opt._evaluate(correlativa, "macro_f1", truth.drop(columns="ESTRATO"))
        # Con la verdad estratificada sigue funcionando.
        score, metricas = opt._evaluate(correlativa, "macro_f1")
        assert score == metricas["macro_f1"]

    def test_evaluate_objetivo_desconocido_culpa_al_objetivo_no_a_estrato(self, base_cfg):
        """Hallazgo de revisión (ronda 3): ``_evaluate`` con una clave mal
        escrita (``f1_macro``) decía «falta ESTRATO» aunque la verdad la
        traía; el «qué hacer» apuntaba a la causa equivocada."""
        truth = self._truth_estratificada()
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        correlativa = pd.DataFrame(
            {"ID_REGISTRO": truth["ID_REGISTRO"], "ID_GRUPO": ["g1"] * 4 + ["g2"] * 4}
        )
        with pytest.raises(ValueError, match="optimization_target") as exc:
            opt._evaluate(correlativa, "f1_macro")
        assert "ESTRATO" not in str(exc.value)

    def test_evaluar_config_valida_la_verdad_que_evalua(self, base_cfg):
        """``evaluar_config(..., truth=otra)`` falla ANTES de correr el
        Orchestrator si ``otra`` no permite el objetivo (no devuelve el dict
        de error del run)."""
        truth = self._truth_estratificada()
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        with pytest.raises(ValueError, match="ESTRATO"):
            opt.evaluar_config({}, "macro_f1", truth=truth.drop(columns="ESTRATO"))

    def test_sin_caso_se_dice_que_fp_negativos_no_se_midio(self, base_cfg):
        """Hallazgo de revisión (ronda 2): sin CASO, ``fp_que_tocan_negativo``
        vale 0 por construcción y debe llegar con una nota, no como medido."""
        truth = self._truth_estratificada()
        correlativa = pd.DataFrame(
            {"ID_REGISTRO": truth["ID_REGISTRO"], "ID_GRUPO": ["g1"] * 4 + ["g2"] * 4}
        )
        _, con_caso = OrchestratorOptimizer(base_cfg, {}, truth)._evaluate(correlativa, "macro_f1")
        assert "nota_fp_negativos" not in con_caso
        sin_caso = truth.drop(columns="CASO")
        _, metricas = OrchestratorOptimizer(base_cfg, {}, sin_caso)._evaluate(
            correlativa, "macro_f1"
        )
        assert metricas["fp_que_tocan_negativo"] == 0
        assert "CASO" in metricas["nota_fp_negativos"]


class TestValidacionCruzada:
    """``cross_validate`` vuelve UNA vez, sobre el optimizador, con los pliegues
    por GRUPO de ``evaluation.banco`` (partir por fila rompe grupos)."""

    def test_evaluar_config_devuelve_metricas(self, gt_micro, base_cfg):
        opt = OrchestratorOptimizer(base_cfg, gt_micro["sources"], gt_micro["truth"], silent=True)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            metricas = opt.evaluar_config({"score_threshold": 0.7})
        assert {"score", "f1", "precision", "recall", "elapsed_s"} <= set(metricas)
        assert metricas["score"] == metricas["f1"]

    def test_validacion_cruzada_por_grupo(self, gt_micro, base_cfg):
        opt = OrchestratorOptimizer(base_cfg, gt_micro["sources"], gt_micro["truth"], silent=True)
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            resultado = opt.validacion_cruzada({"score_threshold": 0.7}, pliegues=2)
        assert resultado["pliegues"] == 2
        assert len(resultado["por_pliegue"]) == 2
        assert all(0.0 <= s <= 1.0 for s in resultado["por_pliegue"])
        assert resultado["media"] == pytest.approx(sum(resultado["por_pliegue"]) / 2)
        assert resultado["desviacion"] >= 0.0
        # Cada pliegue evaluó un subconjunto de GRUPOS completos.
        registros = sum(m["n_records"] for m in resultado["metricas_por_pliegue"])
        assert registros == len(gt_micro["truth"])

    def test_pliegues_invalidos_falla_rapido(self, base_cfg):
        truth = pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]})
        opt = OrchestratorOptimizer(base_cfg, {}, truth)
        with pytest.raises(ValueError, match="pliegues"):
            opt.validacion_cruzada({}, pliegues=1)
