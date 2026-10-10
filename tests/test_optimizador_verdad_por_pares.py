"""F2.15 — lo que el notebook 12 necesita del ÚNICO optimizador y no existía.

Tres piezas, todas en ``evaluation/`` y escritas una sola vez:

* ``evaluar_pares_etiquetados`` (``evaluation.pairwise``): precisión, recall y
  F1 sobre los pares que una persona etiquetó (``ID_1``, ``ID_2``,
  ``IS_MATCH``: el archivo que produce ``GroundTruthGenerator``). Es la
  métrica honesta cuando la verdad son pares y no una partición completa:
  cerrar los pares positivos en componentes y evaluar la partición cuenta
  como falso positivo toda unión correcta entre registros que nadie etiquetó.
* ``OrchestratorOptimizer`` acepta esa verdad por pares además de la verdad
  por registro (``ID_REGISTRO``, ``ID_GROUP``); ``evaluar_correlativa`` es la
  vía pública para medir una correlativa producida por ``linkage()``.
* ``guardar_estudio``: el estudio (objetivo, mejores parámetros, ajustes para
  ``linkage``, historial) como JSON reproducible.

Empresas inventadas; ningún nombre real entra al repositorio.
"""

from __future__ import annotations

import inspect
import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.evaluation import OPTUNA_AVAILABLE
from record_linkage.evaluation.pairwise import (
    MetricasParesEtiquetados,
    evaluar_pares_etiquetados,
    validar_etiquetas,
)

if not OPTUNA_AVAILABLE:
    pytest.skip("Optuna no instalado; el optimizador no se puede probar.", allow_module_level=True)

import optuna

from record_linkage.config.profiles import crear_config_orchestrator
from record_linkage.evaluation import (
    OrchestratorOptimizer,
    default_search_space,
    espacio_de_busqueda,
    orchestrator_hyperparameters,
)

RAIZ = Path(__file__).resolve().parents[1]
SINTETICA = RAIZ / "tests" / "data_sintetica" / "dataset_sintetico_p2_extra_features.csv"


# ─────────────────────────────────────────────────────────────────────────
#  evaluar_pares_etiquetados: la regla, sobre un caso que se puede contar a mano
# ─────────────────────────────────────────────────────────────────────────
def _grupos_predichos() -> pd.Series:
    """a y b juntos; c y d juntos; e solo. f no existe en la predicción."""
    return pd.Series({"a": "g1", "b": "g1", "c": "g2", "d": "g2", "e": "g3"})


def _pares_etiquetados() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ID_1": ["a", "a", "c", "e", "a", "a"],
            "ID_2": ["b", "c", "d", "b", "f", "e"],
            # a-b positivo y predicho junto → TP; a-c negativo y separado → TN;
            # c-d negativo y predicho junto → FP; e-b positivo y separado → FN;
            # a-f: f no está en la predicción → no evaluable; a-e: sin etiqueta.
            "IS_MATCH": [1, 0, 0, 1, 1, None],
        }
    )


def test_cuenta_tp_fp_fn_solo_sobre_pares_etiquetados_y_evaluables() -> None:
    metricas = evaluar_pares_etiquetados(_pares_etiquetados(), _grupos_predichos())
    assert isinstance(metricas, MetricasParesEtiquetados)
    assert (metricas.tp, metricas.fp, metricas.fn) == (1, 1, 1)
    assert metricas.precision == 0.5 and metricas.recall == 0.5 and metricas.f1 == 0.5
    assert metricas.n_pares_etiquetados == 5
    assert metricas.n_pares_evaluados == 4
    assert metricas.n_pares_sin_registro == 1
    assert metricas.n_registros == 5


def test_las_etiquetas_admiten_texto_y_booleanos() -> None:
    pares = _pares_etiquetados().iloc[:4].copy()
    pares["IS_MATCH"] = ["1", "0", "0.0", True]
    metricas = evaluar_pares_etiquetados(pares, _grupos_predichos())
    assert (metricas.tp, metricas.fp, metricas.fn) == (1, 1, 1)


def test_la_cadena_vacia_es_un_par_sin_revisar() -> None:
    """Revisión F2.15 (hallazgo 4): el archivo etiquetado releído como el
    notebook lee su base (``dtype=str`` + ``fillna("")``) trae ``""`` en los
    pares sin revisar; es «sin revisar», no una etiqueta inválida."""
    pares = pd.DataFrame(
        {"ID_1": ["a", "a", "c"], "ID_2": ["b", "c", "d"], "IS_MATCH": ["1", "", "0"]}
    )
    metricas = evaluar_pares_etiquetados(pares, _grupos_predichos())
    assert metricas.n_pares_etiquetados == 2
    assert (metricas.tp, metricas.fp, metricas.fn) == (1, 1, 0)
    # Un espacio en blanco también es «sin revisar».
    pares["IS_MATCH"] = ["1", "  ", "0"]
    assert evaluar_pares_etiquetados(pares, _grupos_predichos()).n_pares_etiquetados == 2


def test_validar_etiquetas_es_publica_y_acepta_los_mismos_valores() -> None:
    assert validar_etiquetas(pd.Series([1, "0", True, "1.0"])).tolist() == [True, False, True, True]
    with pytest.raises(ValueError, match="IS_MATCH"):
        validar_etiquetas(pd.Series(["si"]))


def test_una_etiqueta_que_no_es_0_ni_1_falla_rapido() -> None:
    pares = _pares_etiquetados().iloc[:2].copy()
    pares["IS_MATCH"] = ["si", 0]
    with pytest.raises(ValueError, match="IS_MATCH"):
        evaluar_pares_etiquetados(pares, _grupos_predichos())


def test_sin_pares_evaluables_falla_con_mensaje_accionable() -> None:
    pares = pd.DataFrame({"ID_1": ["x"], "ID_2": ["y"], "IS_MATCH": [1]})
    with pytest.raises(ValueError, match="ning"):
        evaluar_pares_etiquetados(pares, _grupos_predichos())


def test_faltan_columnas_falla_rapido() -> None:
    with pytest.raises(ValueError, match="IS_MATCH"):
        evaluar_pares_etiquetados(pd.DataFrame({"ID_1": ["a"], "ID_2": ["b"]}), _grupos_predichos())


def test_un_id_repetido_en_la_prediccion_falla_rapido() -> None:
    grupos = pd.Series(["g1", "g2"], index=["a", "a"])
    with pytest.raises(ValueError, match="repetid"):
        evaluar_pares_etiquetados(_pares_etiquetados(), grupos)


# ─────────────────────────────────────────────────────────────────────────
#  El optimizador con verdad por pares
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def base_cfg() -> dict:
    return crear_config_orchestrator(perfil="produccion_estandar", validate=False)


#: ``produccion_estandar`` no declara ``nit_empty_passes_filter`` (que el espacio
#: por defecto propone), así que las pruebas pasan un espacio explícito.
ESPACIO = espacio_de_busqueda({"score_threshold": (0.45, 0.80, 0.05)})


@pytest.fixture(scope="module")
def sintetica() -> pd.DataFrame:
    base = pd.read_csv(SINTETICA, dtype=str).fillna("")
    base["ID"] = [f"R{i:03d}" for i in range(len(base))]
    return base


def _pares_desde_entidad(base: pd.DataFrame, semilla: int = 7) -> pd.DataFrame:
    """Pares al azar de la sintética, etiquetados con su ID_GROUP (verdad simulada)."""
    rng = np.random.default_rng(semilla)
    n = len(base)
    i = rng.integers(0, n, size=120)
    j = rng.integers(0, n, size=120)
    pares = pd.DataFrame({"ID_1": base["ID"].to_numpy()[i], "ID_2": base["ID"].to_numpy()[j]})
    pares = pares[pares["ID_1"] != pares["ID_2"]].drop_duplicates().reset_index(drop=True)
    grupo = base.set_index("ID")["ID_GROUP"]
    pares["IS_MATCH"] = (pares["ID_1"].map(grupo) == pares["ID_2"].map(grupo)).astype(int)
    return pares


def test_el_constructor_acepta_la_verdad_por_pares(base_cfg: dict, sintetica: pd.DataFrame) -> None:
    pares = _pares_desde_entidad(sintetica)
    opt = OrchestratorOptimizer(base_cfg, {}, pares, search_space=ESPACIO)
    assert opt.verdad_por_pares is True
    assert list(opt.truth.columns) == ["ID_1", "ID_2", "IS_MATCH"]
    # La verdad por registro sigue funcionando igual que antes.
    por_registro = OrchestratorOptimizer(
        base_cfg, {}, pd.DataFrame({"ID_REGISTRO": ["1"], "ID_GROUP": ["a"]}), search_space=ESPACIO
    )
    assert por_registro.verdad_por_pares is False


def test_una_verdad_sin_ninguno_de_los_dos_formatos_falla_rapido(base_cfg: dict) -> None:
    with pytest.raises(ValueError, match="ID_1"):
        OrchestratorOptimizer(
            base_cfg, {}, pd.DataFrame({"ID_REGISTRO": ["1"]}), search_space=ESPACIO
        )


def test_una_etiqueta_invalida_falla_al_construir_el_optimizador(base_cfg: dict) -> None:
    """Revisión F2.15 (hallazgo 1a): «si»/«no» pasaban el constructor (solo mira
    columnas) y cada trial moría en silencio con score 0,0 y ``status='completed'``.
    La etiqueta se valida ANTES del primer trial, con el mensaje accionable."""
    verdad = pd.DataFrame(
        {"ID_1": ["R000", "R001"], "ID_2": ["R001", "R002"], "IS_MATCH": ["si", "no"]}
    )
    with pytest.raises(ValueError, match="IS_MATCH"):
        OrchestratorOptimizer(base_cfg, {}, verdad, search_space=ESPACIO)
    # Los pares sin revisar (NaN o "") no cuentan, pero sin ninguno revisado no hay verdad.
    sin_revisar = pd.DataFrame({"ID_1": ["R000", "R001"], "ID_2": ["R001", "R002"]})
    sin_revisar["IS_MATCH"] = [None, ""]
    with pytest.raises(ValueError, match="ning"):
        OrchestratorOptimizer(base_cfg, {}, sin_revisar, search_space=ESPACIO)
    # Con un par revisado (y otro vacío) se construye.
    sin_revisar["IS_MATCH"] = ["1", ""]
    OrchestratorOptimizer(base_cfg, {}, sin_revisar, search_space=ESPACIO)


def test_un_trial_sin_pares_evaluables_no_cuenta_como_completado(
    base_cfg: dict, sintetica: pd.DataFrame, tmp_path: Path
) -> None:
    """Revisión F2.15 (hallazgo 1b): ids que no son los de la correlativa daban un
    estudio «completo» con todos los trials a 0,0 y ``guardar_estudio`` lo escribía."""
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    fuentes = {"DEMO": sintetica[["ID", "NIT", "RAZON_SOCIAL", "CIUDAD"]].copy()}
    verdad = pd.DataFrame({"ID_1": ["X1", "X2"], "ID_2": ["X2", "X3"], "IS_MATCH": [1, 0]})
    opt = OrchestratorOptimizer(base_cfg, fuentes, verdad, search_space=ESPACIO, silent=True)
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        antes = opt.evaluar_config({}, "f1")
        resultado = opt.optimize(n_trials=1, optimization_target="f1", show_progress_bar=False)
    assert antes["score"] == 0.0 and "Ningún par etiquetado es evaluable" in antes["error"]
    assert resultado["total_trials_completed"] == 0
    assert resultado["best_params"] is None and resultado["best_metrics"] == {}
    assert [h["status"] for h in resultado["optimization_history"]] == ["failed"]
    assert "Ningún par etiquetado es evaluable" in resultado["optimization_history"][0]["error"]
    with pytest.raises(ValueError, match="optimize"):
        opt.guardar_estudio(tmp_path / "estudio.json")


def test_f_beta_pesa_el_recall_cuatro_veces() -> None:
    """Revisión F2.15 (hallazgo 2): la fórmula de F-beta vivía dos veces.

    Ronda 2 (hallazgo 3): el nombre anterior prometía «se escribe una vez» sin
    mirar el módulo; ahora se cuenta la fórmula en la fuente, de modo que
    volver a escribirla inline en ``_evaluate`` o ``_evaluar_por_pares`` falla.
    """
    _f_beta = orchestrator_hyperparameters._f_beta

    assert _f_beta(0.5, 0.5) == pytest.approx(0.5)
    assert _f_beta(1.0, 0.5) == pytest.approx(5 * 0.5 / (4 + 0.5))  # beta=2 favorece recall
    assert _f_beta(1.0, 0.5, beta=1.0) == pytest.approx(2 / 3)
    assert _f_beta(0.0, 0.0) == 0.0
    fuente = inspect.getsource(orchestrator_hyperparameters)
    assert fuente.count("* precision * recall /") == 1, "la fórmula de F-beta vive solo en _f_beta"
    assert "beta2" not in fuente, "la copia inline (beta2 = ...) volvió a aparecer"


class _TrialFalso:
    """Recorre un espacio sin Optuna: ``suggest_*`` devuelve el mínimo o la primera opción."""

    @staticmethod
    def suggest_float(nombre: str, minimo: float, maximo: float, **_: object) -> float:
        return minimo

    @staticmethod
    def suggest_int(nombre: str, minimo: int, maximo: int, **_: object) -> int:
        return minimo

    @staticmethod
    def suggest_categorical(nombre: str, opciones: list) -> object:
        return opciones[0]


def test_las_claves_del_espacio_por_defecto_son_las_que_el_cuerpo_sugiere() -> None:
    """Ronda 2 (hallazgo 2): ``default_search_space.claves`` se escribía a mano,
    separado de los ``trial.suggest_*`` del cuerpo; si se añade un parámetro y
    se olvida el atributo, ``_validar_espacio`` queda ciega para esa clave."""
    params = default_search_space(_TrialFalso())
    assert set(params) == default_search_space.claves
    assert params["weight_name"] + params["weight_nit"] == pytest.approx(1.0)
    # espacio_de_busqueda declara las claves del dict que recibe; weight_nit derivado
    # no cuenta porque _validar_espacio excluye los pesos de todos modos.
    espacio = espacio_de_busqueda({"score_threshold": (0.4, 0.8, 0.1), "weight_name": (0.3, 0.7)})
    assert set(espacio(_TrialFalso())) - {"weight_nit"} == espacio.claves


def test_el_mejor_trial_del_estudio_es_uno_completado(
    base_cfg: dict, sintetica: pd.DataFrame, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ronda 2 (hallazgo 4): un trial ``failed`` devuelve 0,0 a Optuna; si el mejor
    completado también puntúa 0,0, ``study.best_trial`` es el fallido (Optuna
    conserva el primero en el empate) y el JSON apuntaba a un trial cuyos params
    no eran ``mejores_parametros``. Ahora mejor_trial/mejor_score salen de la
    misma entrada del historial que ``best_params``."""
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    pares = _pares_desde_entidad(sintetica)
    opt = OrchestratorOptimizer(
        base_cfg, {}, pares, search_space=espacio_de_busqueda({"score_threshold": 0.6})
    )
    separada = sintetica[["ID"]].assign(ID_GRUPO=range(len(sintetica)))
    llamadas: list[dict] = []

    def orquestador_falso(cfg: dict) -> tuple[pd.DataFrame | None, float]:
        llamadas.append(cfg)
        return (None, 0.01) if len(llamadas) == 1 else (separada.copy(), 0.01)

    monkeypatch.setattr(opt, "_run_orchestrator", orquestador_falso)
    resultado = opt.optimize(n_trials=2, optimization_target="precision", show_progress_bar=False)
    assert [h["status"] for h in resultado["optimization_history"]] == ["failed", "completed"]
    assert resultado["total_trials_completed"] == 1 and resultado["best_score"] == 0.0
    assert (
        resultado["study"].best_trial.number == 0
    )  # el empate lo gana el fallido: por eso no se usa
    estudio = json.loads(opt.guardar_estudio(tmp_path / "estudio.json").read_text(encoding="utf-8"))
    assert estudio["mejor_trial"] == 1
    assert estudio["mejor_score"] == 0.0
    mejor = next(h for h in estudio["historial"] if h["trial"] == estudio["mejor_trial"])
    assert mejor["status"] == "completed" and mejor["params"] == estudio["mejores_parametros"]


def test_col_id_propio_cruza_la_correlativa_con_la_verdad(
    base_cfg: dict, sintetica: pd.DataFrame
) -> None:
    """Ronda 2 (hallazgo 1): la fuente identifica sus registros con ``CODIGO`` (no
    ``ID``/``ID_REGISTRO``); sin ``col_id`` el error dice qué columnas se aceptan y
    qué hacer; con ``col_id="CODIGO"`` la correlativa se cruza con la verdad."""
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    base = sintetica.rename(columns={"ID": "CODIGO"})
    pares = _pares_desde_entidad(sintetica)  # los mismos ids R000…, ahora en CODIGO
    fuentes = {"DEMO": base[["CODIGO", "NIT", "RAZON_SOCIAL", "CIUDAD"]].copy()}
    sin_col_id = OrchestratorOptimizer(base_cfg, fuentes, pares, search_space=ESPACIO, silent=True)
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        medida = sin_col_id.evaluar_config({}, "f1")
    assert medida["score"] == 0.0 and "precision" not in medida
    assert "ID_REGISTRO" in medida["error"] and "col_id" in medida["error"]

    con_col_id = OrchestratorOptimizer(
        base_cfg, fuentes, pares, search_space=ESPACIO, silent=True, col_id="CODIGO"
    )
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        medida = con_col_id.evaluar_config({}, "f1")
    assert "error" not in medida and {"precision", "recall", "f1"} <= set(medida)
    assert medida["n_pares_evaluados"] == len(pares)
    perfecta = base[["CODIGO", "ID_GROUP"]].rename(columns={"ID_GROUP": "ID_GRUPO"})
    assert con_col_id.evaluar_correlativa(perfecta, "f1")[0] == 1.0
    # Una columna que ninguna fuente trae falla al construir, no en el trial 1.
    with pytest.raises(ValueError, match="CODIGO_EMPRESA"):
        OrchestratorOptimizer(
            base_cfg, fuentes, pares, search_space=ESPACIO, col_id="CODIGO_EMPRESA"
        )


def test_evaluar_correlativa_mide_sobre_los_pares_etiquetados(
    base_cfg: dict, sintetica: pd.DataFrame
) -> None:
    pares = _pares_desde_entidad(sintetica)
    opt = OrchestratorOptimizer(base_cfg, {}, pares, search_space=ESPACIO)
    # Partición perfecta: cada ID_GROUP un grupo → F1 = 1.
    perfecta = sintetica[["ID", "ID_GROUP"]].rename(columns={"ID_GROUP": "ID_GRUPO"})
    score, metricas = opt.evaluar_correlativa(perfecta, "f1")
    assert score == 1.0 and metricas["f1"] == 1.0
    assert metricas["n_pares_evaluados"] == len(pares)
    # Todo separado: recall 0, precisión 0 (no se unió nada).
    separada = sintetica[["ID"]].assign(ID_GRUPO=range(len(sintetica)))
    score, metricas = opt.evaluar_correlativa(separada, "recall")
    assert score == 0.0 and metricas["tp"] == 0
    assert metricas["fn"] == int(pares["IS_MATCH"].sum())


def test_macro_f1_no_existe_para_la_verdad_por_pares(
    base_cfg: dict, sintetica: pd.DataFrame
) -> None:
    opt = OrchestratorOptimizer(base_cfg, {}, _pares_desde_entidad(sintetica), search_space=ESPACIO)
    with pytest.raises(ValueError, match="pares"):
        opt.optimize(n_trials=1, optimization_target="macro_f1", show_progress_bar=False)


def test_la_validacion_cruzada_exige_verdad_por_registro(
    base_cfg: dict, sintetica: pd.DataFrame
) -> None:
    opt = OrchestratorOptimizer(base_cfg, {}, _pares_desde_entidad(sintetica), search_space=ESPACIO)
    with pytest.raises(ValueError, match="ID_GROUP"):
        opt.validacion_cruzada({}, pliegues=2)


def test_el_espacio_se_contrasta_con_el_perfil_activo_antes_del_primer_trial(
    sintetica: pd.DataFrame,
) -> None:
    """Hallazgo del notebook 12: ``nit_empty_passes_filter`` no es clave de
    ``produccion_estandar`` (el scorer la lee con default True); el estudio
    corría entero y ``linkage(ajustes_perfil=...)`` lo rechazaba al final."""
    estandar = crear_config_orchestrator(perfil="produccion_estandar", validate=False)
    pares = _pares_desde_entidad(sintetica)
    espacio = espacio_de_busqueda({"nit_empty_passes_filter": [True, False]})
    with pytest.raises(ValueError, match=r"nit_empty_passes_filter.*produccion_estandar"):
        OrchestratorOptimizer(estandar, {}, pares, search_space=espacio)
    # default_search_space también la propone: mismo fallo, mismo mensaje.
    with pytest.raises(ValueError, match="produccion_calibrada"):
        OrchestratorOptimizer(estandar, {}, pares)
    # Con un perfil que la declara, o sin ella, se construye.
    calibrada = crear_config_orchestrator(perfil="produccion_calibrada", validate=False)
    OrchestratorOptimizer(calibrada, {}, pares, search_space=espacio)
    OrchestratorOptimizer(
        estandar, {}, pares, search_space=espacio_de_busqueda({"max_nit_distance": (0, 3)})
    )
    # Un espacio propio sin claves declaradas no se puede contrastar: se admite.
    OrchestratorOptimizer(estandar, {}, pares, search_space=lambda trial: {})


def test_guardar_estudio_antes_de_optimizar_falla_rapido(
    base_cfg: dict, sintetica: pd.DataFrame, tmp_path: Path
) -> None:
    opt = OrchestratorOptimizer(base_cfg, {}, _pares_desde_entidad(sintetica), search_space=ESPACIO)
    with pytest.raises(ValueError, match="optimize"):
        opt.guardar_estudio(tmp_path / "estudio.json")


@pytest.mark.slow
def test_optimiza_sobre_pares_y_guarda_el_estudio(
    base_cfg: dict, sintetica: pd.DataFrame, tmp_path: Path
) -> None:
    """E2E: dos trials sobre la sintética con verdad por pares; el JSON del
    estudio se puede leer de vuelta y sus ajustes son los de ``linkage``."""
    optuna.logging.set_verbosity(optuna.logging.ERROR)
    pares = _pares_desde_entidad(sintetica)
    fuentes = {"DEMO": sintetica[["ID", "NIT", "RAZON_SOCIAL", "CIUDAD"]].copy()}
    opt = OrchestratorOptimizer(base_cfg, fuentes, pares, search_space=ESPACIO, silent=True)
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        antes = opt.evaluar_config({}, "f1")
        resultado = opt.optimize(n_trials=2, optimization_target="f1", show_progress_bar=False)
    assert "n_pares_evaluados" in antes and antes["score"] == antes["f1"]
    assert resultado["total_trials_completed"] == 2
    assert resultado["best_metrics"]["n_pares_evaluados"] > 0

    ruta = opt.guardar_estudio(tmp_path / "estudio.json")
    estudio = json.loads(ruta.read_text(encoding="utf-8"))
    assert estudio["objetivo"] == "f1"
    assert estudio["verdad"] == "pares"
    assert estudio["trials_completados"] == 2
    assert estudio["mejor_score"] == pytest.approx(resultado["best_score"])
    assert estudio["mejores_parametros"] == resultado["best_params"]
    assert estudio["ajustes_perfil"] == opt.ajustes_perfil()
    assert len(estudio["historial"]) == 2
    assert {"trial", "params", "score", "elapsed_s", "status", "metrics"} <= set(
        estudio["historial"][0]
    )
    assert estudio["version"] and estudio["fecha"]
