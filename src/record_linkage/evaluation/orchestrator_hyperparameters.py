"""evaluation.orchestrator_hyperparameters — Optuna sobre Orchestrator (v3.2.6).

A diferencia de `HyperparameterOptimizer` (que trabaja con `linkage_pipeline.run()`),
esta clase optimiza hiperparámetros sobre el flujo de producción real
(`Orchestrator.run()`), evaluando contra un ground truth a nivel de pares.

Diseño:
    - Recibe un `base_config` dict (el que pasarías al Orchestrator).
    - Recibe `sources` (dict de DataFrames) y `truth` (DF con ID_REGISTRO + ID_GROUP).
    - En cada trial:
        1. Copia profunda del config.
        2. Aplica params sugeridos sobre profiles[active].
        3. Corre Orchestrator en un work_dir temporal.
        4. Alinea correlativa con truth → calcula F1 vía evaluar_pares().
        5. Retorna el score (F1 por defecto).
    - Devuelve un `best_config` listo para producción + historial completo.

Uso típico:
    from record_linkage.evaluation.orchestrator_hyperparameters import (
        OrchestratorOptimizer
    )

    base_cfg = crear_config_orchestrator(perfil="produccion_calibrada",
                                          validate=False)
    optimizer = OrchestratorOptimizer(
        base_config=base_cfg,
        sources=mis_fuentes,
        truth=mi_ground_truth,
    )
    result = optimizer.optimize(n_trials=20, optimization_target="f1")
    best_cfg = result["best_config"]  # listo para Orchestrator
    ajustes = optimizer.ajustes_perfil()  # listo para linkage(ajustes_perfil=...)

F2.14 (2026-10-07). Enrique decidió conservar la optimización con Optuna
(«sirve para optimizar parámetros») pero el paquete ``optimization/`` retirado
en F2.8 (``OptimizationEngine``, ``ParameterSpace``, ``OptunaIntegration``,
``OptimizationVisualizerLite``; deprecado desde v3.2.7 a favor de esta clase)
NO vuelve: habría dos optimizadores (regla 3). Lo que aquel daba y este no
tenía se añadió aquí UNA vez:

* ``espacio_de_busqueda``: espacio propio por parámetro sin escribir
  ``trial.suggest_*`` (lo que ``ParameterSpace`` resolvía).
* objetivo ``macro_f1``: reutiliza ``evaluation.banco.evaluar_calidad`` (la
  misma regla que lee el banco) cuando la verdad trae ``ESTRATO``.
* ``evaluar_config`` + ``validacion_cruzada``: la antigua
  ``GroundTruthEvaluator.cross_validate``, con los pliegues por GRUPO de
  ``evaluation.banco`` (partir por fila rompe grupos verdaderos).
* ``ajustes_perfil``: el ``best_params`` como sobrescrituras del perfil, el
  formato de ``linkage(ajustes_perfil=...)``.
* Las gráficas (convergencia e importancia) viven en
  ``evaluation.graficas_optimizacion`` (matplotlib, sin dashboard).

F2.15 (2026-10-10), para el notebook 12. La verdad puede ser también el
archivo de PARES etiquetados que produce ``GroundTruthGenerator``
(``ID_1``, ``ID_2``, ``IS_MATCH``): entonces cada trial se mide con
``evaluation.pairwise.evaluar_pares_etiquetados`` SOLO sobre esos pares.
Cerrar los positivos en componentes y evaluar la partición resultante
contaría como falso positivo toda unión correcta entre registros que nadie
etiquetó (un tercer registro del mismo NIT, por ejemplo), así que no se hace.
Con esa verdad no hay ``macro_f1`` (no hay estratos) ni validación cruzada
(parte por grupo). ``evaluar_correlativa`` es la vía pública para medir una
correlativa producida por ``linkage()``; ``guardar_estudio`` deja el estudio
como JSON.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..utils.logger import CustomLogger
from ._flags import OPTUNA_AVAILABLE
from .banco import evaluar_calidad
from .pairwise import (
    COLUMNAS_PARES_ETIQUETADOS,
    PairwiseMetrics,
    esta_revisado,
    evaluar_pares,
    evaluar_pares_etiquetados,
    validar_etiquetas,
)

#: Métricas que ``optimize`` sabe maximizar. ``macro_f1`` exige ``ESTRATO`` en la verdad.
OBJETIVOS_VALIDOS: frozenset[str] = frozenset({"f1", "f2", "precision", "recall", "macro_f1"})

#: Columnas de la verdad por REGISTRO (una partición completa).
_COLUMNAS_VERDAD_REGISTRO: tuple[str, ...] = ("ID_REGISTRO", "ID_GROUP")

#: Columnas de la verdad que, si vienen, se conservan para las métricas del banco.
_COLUMNAS_BANCO: tuple[str, ...] = ("ESTRATO", "REGIMEN", "CASO")

#: Claves de ``params`` que no son del perfil sino de ``weights``.
_PESOS: dict[str, str] = {"weight_name": "name", "weight_nit": "nit", "weight_phonetic": "phonetic"}

#: Columnas con las que una fuente o la correlativa identifican sus registros.
_COLUMNAS_ID: tuple[str, ...] = ("ID_REGISTRO", "ID", "id_registro")


def _f_beta(precision: float, recall: float, beta: float = 2.0) -> float:
    """F-beta: ``(1+β²)·P·R / (β²·P + R)``; 0.0 sin denominador.

    ``beta=2`` (el ``f2`` que ``optimize`` sabe maximizar) pesa el recall
    cuatro veces más que la precisión: útil cuando los duplicados perdidos
    cuestan más. Escrita una vez para los dos caminos de ``_evaluate``
    (verdad por registro y por pares).
    """
    denominador = beta**2 * precision + recall
    if denominador <= 0:
        return 0.0
    return (1 + beta**2) * precision * recall / denominador


def _trial_fallido(
    numero: int, params: Mapping[str, Any], elapsed: float, error: str
) -> dict[str, Any]:
    """Entrada del historial para un trial que no produjo métricas."""
    return {
        "trial": numero,
        "params": dict(params),
        "score": 0.0,
        "elapsed_s": elapsed,
        "status": "failed",
        "metrics": {},
        "error": error,
    }


def _a_json(valor: Any) -> Any:
    """Serializa lo que ``json`` no sabe: escalares numpy, rutas, tuplas de params."""
    if isinstance(valor, np.generic):
        return valor.item()
    if isinstance(valor, np.ndarray):
        return valor.tolist()
    if isinstance(valor, Path):
        return str(valor)
    if isinstance(valor, (set, frozenset)):
        return sorted(valor)
    return str(valor)


# ─────────────────────────────────────────────────────────────────────────
#  Estrategia de búsqueda — define el espacio Optuna
# ─────────────────────────────────────────────────────────────────────────
def default_search_space(trial) -> dict[str, Any]:
    """Espacio de búsqueda por defecto para el Orchestrator.

    Cubre los parámetros que el código REALMENTE LEE (verificados en
    auditoría Fase 1). Cualquiera puede pasar su propia función al
    constructor de OrchestratorOptimizer si necesita otro espacio.

    Incluye ``nit_empty_passes_filter``: solo válido con perfiles que la
    declaren (``produccion_calibrada``, ``alta_precision``); con
    ``produccion_estandar`` el constructor falla antes del primer trial
    (``_validar_espacio``): use ``espacio_de_busqueda({...})`` sin esa clave.

    Returns:
        Dict con valores sugeridos para cada parámetro. Estructura plana
        (se inyecta directo en profiles[active] por OrchestratorOptimizer).
    """
    params = {
        # LSH (filtro previo — más permisivo aquí, más estricto en scoring)
        "lsh_threshold": trial.suggest_float("lsh_threshold", 0.50, 0.75, step=0.05),
        # Filtros previos al scoring (los más decisivos para precision)
        "score_threshold": trial.suggest_float("score_threshold", 0.45, 0.85, step=0.05),
        "min_name_similarity": trial.suggest_float("min_name_similarity", 0.40, 0.85, step=0.05),
        "max_nit_distance": trial.suggest_int("max_nit_distance", 0, 3),
        "nit_empty_passes_filter": trial.suggest_categorical(
            "nit_empty_passes_filter", [True, False]
        ),
        # Pesos (con phonetic=0 por evidencia, name/nit a calibrar)
        "weight_name": trial.suggest_float("weight_name", 0.30, 0.70, step=0.05),
    }
    # Asegurar suma = 1.0; phonetic = 0 (recomendación heredada IT-7)
    params["weight_nit"] = 1.0 - params["weight_name"]
    return params


#: Claves que ``default_search_space`` propone: el optimizador las contrasta con
#: el perfil activo ANTES del primer trial (ver ``_validar_espacio``).
default_search_space.claves = frozenset(  # type: ignore[attr-defined]
    {
        "lsh_threshold",
        "score_threshold",
        "min_name_similarity",
        "max_nit_distance",
        "nit_empty_passes_filter",
        "weight_name",
        "weight_nit",
    }
)


def _claves_de_perfil() -> frozenset[str]:
    """Claves que un perfil acepta (más los pesos planos): valida el espacio."""
    from ..config.profiles import PERFILES_BASE

    claves: set[str] = set(_PESOS)
    for perfil in PERFILES_BASE.values():
        claves.update(perfil)
    return frozenset(claves)


def espacio_de_busqueda(rangos: Mapping[str, Any]) -> Callable[[Any], dict[str, Any]]:
    """Construye un espacio de búsqueda propio a partir de un dict por parámetro.

    Es la forma en que un notebook declara QUÉ optimizar sin escribir
    ``trial.suggest_*``. La función devuelta tiene la misma firma que
    ``default_search_space`` y se pasa como ``search_space=`` al optimizador.

    Args:
        rangos: ``{parámetro: especificación}`` con estas formas:
            ``(mín, máx)`` → ``suggest_int`` si ambos son enteros, si no
            ``suggest_float``; ``(mín, máx, paso)`` → igual, con paso;
            ``[a, b, ...]`` → ``suggest_categorical``; un escalar → valor fijo.
            Si viene ``weight_name`` y no ``weight_nit``, este se deriva como
            ``1 − weight_name`` (phonetic = 0), igual que en
            ``default_search_space``.

    Returns:
        ``(trial) -> dict`` listo para ``OrchestratorOptimizer``.

    Raises:
        ValueError: parámetro que ningún perfil conoce, rango invertido o
            no numérico, lista vacía o especificación con otra forma. Se valida al
            construir, no en el trial 1 después de minutos de cómputo.
    """
    conocidas = _claves_de_perfil()
    desconocidas = sorted(set(rangos) - conocidas)
    if desconocidas:
        raise ValueError(
            f"Parámetros que ningún perfil conoce: {desconocidas}. Las claves válidas "
            "son las del perfil (config.profiles.PERFILES_BASE) y weight_name/weight_nit/"
            "weight_phonetic; una clave mal escrita se optimizaría sin efecto."
        )
    especificaciones: dict[str, Any] = {}
    for nombre, espec in rangos.items():
        if isinstance(espec, tuple):
            if len(espec) not in (2, 3):
                raise ValueError(f"{nombre}: una tupla debe ser (mín, máx) o (mín, máx, paso).")
            if any(not isinstance(v, int | float) or isinstance(v, bool) for v in espec):
                raise ValueError(
                    f"{nombre}: una tupla define un rango numérico (mín, máx[, paso]); "
                    "para opciones discretas use una lista."
                )
            minimo, maximo = espec[0], espec[1]
            if minimo > maximo:
                raise ValueError(f"{nombre}: rango invertido ({minimo} > {maximo}).")
            paso = espec[2] if len(espec) == 3 else None
            entero = all(isinstance(v, int) and not isinstance(v, bool) for v in espec)
            especificaciones[nombre] = ("int" if entero else "float", minimo, maximo, paso)
        elif isinstance(espec, list):
            if not espec:
                raise ValueError(f"{nombre}: la lista de opciones está vacía.")
            especificaciones[nombre] = ("categorico", list(espec))
        else:
            especificaciones[nombre] = ("fijo", espec)

    def espacio(trial: Any) -> dict[str, Any]:
        params: dict[str, Any] = {}
        for nombre, espec in especificaciones.items():
            tipo = espec[0]
            if tipo == "int":
                params[nombre] = trial.suggest_int(nombre, espec[1], espec[2], step=espec[3] or 1)
            elif tipo == "float":
                params[nombre] = trial.suggest_float(nombre, espec[1], espec[2], step=espec[3])
            elif tipo == "categorico":
                params[nombre] = trial.suggest_categorical(nombre, espec[1])
            else:
                params[nombre] = espec[1]
        if "weight_name" in params and "weight_nit" not in params:
            params["weight_nit"] = 1.0 - params["weight_name"]
        return params

    espacio.claves = frozenset(rangos)  # type: ignore[attr-defined]
    return espacio


# ─────────────────────────────────────────────────────────────────────────
#  Optimizador
# ─────────────────────────────────────────────────────────────────────────
class OrchestratorOptimizer:
    """Optimizador Optuna que trabaja con `Orchestrator.run()` real.

    Args:
        base_config: dict de configuración completo (con 'profile' y 'profiles').
            Sirve de plantilla; los params del trial se aplican encima.
        sources: dict {nombre_fuente: DataFrame}.
        truth: la verdad, en uno de dos formatos. Por REGISTRO: columnas
            ``ID_REGISTRO`` e ``ID_GROUP`` (partición completa; admite
            ``ESTRATO``/``REGIMEN``/``CASO`` para las métricas del banco y
            ``macro_f1``). Por PARES: ``ID_1``, ``ID_2`` e ``IS_MATCH`` (el
            archivo de ``GroundTruthGenerator`` revisado; se mide solo sobre
            esos pares, ver ``evaluation.pairwise.evaluar_pares_etiquetados``).
            Los ids deben ser los de la columna de identificador de las
            fuentes, que el Orchestrator conserva en la correlativa: por
            defecto ``ID_REGISTRO`` o ``ID``; con otro nombre, pase ``col_id``.
        col_id: nombre de la columna única por fila con la que las fuentes y
            la correlativa identifican sus registros (la misma que
            ``linkage(col_id=...)``). ``None`` (default) busca ``ID_REGISTRO``
            e ``ID``. Si se pasa, toda fuente debe traerla (fail-fast).
        search_space: función `(trial) -> dict[str, Any]` que define el
            espacio de búsqueda. Default: `default_search_space`.
        silent: si True (default), silencia logs del Orchestrator durante
            los trials para no inundar la consola.
        keep_workdir: si True, conserva los work_dirs de cada trial (útil
            para debug). Default False (se borran al terminar).

    Attributes:
        verdad_por_pares: True si ``truth`` es el archivo de pares etiquetados.

    Raises:
        ImportError: si Optuna no está instalado.
        ValueError: si truth no tiene las columnas de ninguno de los dos
            formatos, o si ``col_id`` nombra una columna que alguna fuente no trae.
    """

    def __init__(
        self,
        base_config: dict[str, Any],
        sources: dict[str, pd.DataFrame],
        truth: pd.DataFrame,
        search_space: Callable | None = None,
        silent: bool = True,
        keep_workdir: bool = False,
        col_id: str | None = None,
    ):
        if not OPTUNA_AVAILABLE:
            raise ImportError(
                "Optuna no está instalado. Instalar con: pip install 'rues-linker[optimization]'"
            )

        self.verdad_por_pares = self._es_verdad_por_pares(truth)
        # Validar base_config
        if "profile" not in base_config or "profiles" not in base_config:
            raise ValueError("base_config debe tener claves 'profile' y 'profiles'.")
        active = base_config["profile"]
        if active not in base_config["profiles"]:
            raise ValueError(
                f"base_config['profile']='{active}' no está en base_config['profiles']."
            )

        self.base_config = base_config
        self.sources = sources
        self.col_id = col_id
        self._validar_col_id()
        if self.verdad_por_pares:
            columnas = list(COLUMNAS_PARES_ETIQUETADOS)
        else:
            columnas = list(_COLUMNAS_VERDAD_REGISTRO) + [
                c for c in _COLUMNAS_BANCO if c in truth.columns
            ]
        self.truth = truth[columnas].copy()
        if self.verdad_por_pares:
            self._validar_verdad_por_pares(self.truth)
        self.search_space = search_space or default_search_space
        self._validar_espacio(base_config["profiles"][active], active)
        self.silent = silent
        self.keep_workdir = keep_workdir

        self.logger = CustomLogger("OrchestratorOptimizer")
        self.best_params: dict[str, Any] | None = None
        self.best_config: dict[str, Any] | None = None
        self.optimization_history: list[dict[str, Any]] = []
        self._tmp_workdirs: list[Path] = []  # para cleanup
        #: Lo que ``optimize`` dejó y ``guardar_estudio`` escribe (objetivo, mejor score…).
        self._resumen_estudio: dict[str, Any] | None = None

    # ─────────────────────────────────────────────────────────────────────
    #  Helpers internos
    # ─────────────────────────────────────────────────────────────────────
    def _validar_espacio(self, perfil: Mapping[str, Any], nombre_perfil: str) -> None:
        """Fail-fast: cada clave del espacio existe en el perfil ACTIVO.

        El Orchestrator acepta cualquier clave en el perfil (el scorer lee
        ``nit_empty_passes_filter`` aunque ``produccion_estandar`` no la
        declare), pero ``linkage(ajustes_perfil=...)`` valida contra las claves
        del perfil y rechaza las desconocidas. Sin esta guarda el estudio corre
        entero y la aplicación falla al final (hallazgo del notebook 12, F2.15).
        Solo se puede comprobar cuando el espacio declara sus claves
        (``espacio_de_busqueda`` y ``default_search_space`` lo hacen).
        """
        claves = getattr(self.search_space, "claves", None)
        if claves is None:
            return
        desconocidas = sorted(set(claves) - set(perfil) - set(_PESOS))
        if desconocidas:
            from ..config.profiles import PERFILES_BASE

            con_la_clave = sorted(
                nombre
                for nombre, otro in PERFILES_BASE.items()
                if all(c in otro for c in desconocidas)
            )
            raise ValueError(
                f"Qué pasó: el espacio de búsqueda incluye {desconocidas}, que el perfil "
                f"'{nombre_perfil}' no declara. Por qué importa: los trials correrían, pero "
                "linkage(ajustes_perfil=...) rechaza las claves que el perfil no tiene y los "
                "mejores parámetros no se podrían aplicar. Qué hacer: quite esas claves del "
                "espacio o use un perfil que las declare"
                + (f" ({', '.join(con_la_clave)})" if con_la_clave else "")
                + "."
            )

    def _validar_col_id(self) -> None:
        """Fail-fast: si ``col_id`` se pasó, cada fuente la trae.

        Sin esta guarda el estudio corría entero y cada trial moría al cruzar
        la correlativa con la verdad (revisión F2.15, ronda 2).
        """
        if self.col_id is None:
            return
        sin_columna = sorted(n for n, f in self.sources.items() if self.col_id not in f.columns)
        if sin_columna:
            raise ValueError(
                f"Qué pasó: col_id='{self.col_id}' no es columna de las fuentes {sin_columna}. "
                "Por qué importa: con esa columna se cruza la correlativa con la verdad; sin "
                "ella ningún trial se puede medir. Qué hacer: pase el nombre exacto de la "
                "columna única por fila de cada fuente, o renómbrela a 'ID'."
            )

    @staticmethod
    def _validar_verdad_por_pares(truth: pd.DataFrame) -> None:
        """Fail-fast: las etiquetas son 0/1 y hay al menos un par revisado.

        Sin esta guarda, un ``IS_MATCH`` con «si»/«no» pasaba el constructor
        (solo mira columnas) y cada trial moría dentro de
        ``evaluar_pares_etiquetados`` con score 0,0: el mensaje accionable
        existía pero nunca llegaba a quien optimiza (revisión F2.15).
        """
        revisadas = truth.loc[esta_revisado(truth["IS_MATCH"]), "IS_MATCH"]
        if revisadas.empty:
            raise ValueError(
                f"Qué pasó: ninguno de los {len(truth)} pares de `truth` tiene IS_MATCH "
                "(todos vacíos). Por qué importa: sin pares revisados no hay verdad contra "
                "la que medir y todos los trials darían 0. Qué hacer: etiquete pares "
                "(1 = misma empresa, 0 = distinta) y vuelva a construir el optimizador."
            )
        validar_etiquetas(revisadas)

    @staticmethod
    def _es_verdad_por_pares(truth: pd.DataFrame) -> bool:
        """Decide el formato de la verdad por sus columnas; falla si no es ninguno."""
        columnas = set(truth.columns)
        if set(_COLUMNAS_VERDAD_REGISTRO) <= columnas:
            return False
        if set(COLUMNAS_PARES_ETIQUETADOS) <= columnas:
            return True
        raise ValueError(
            "`truth` no tiene las columnas de ningún formato conocido. Por registro: "
            f"{list(_COLUMNAS_VERDAD_REGISTRO)}; por pares etiquetados: "
            f"{list(COLUMNAS_PARES_ETIQUETADOS)} (la salida de GroundTruthGenerator con "
            f"IS_MATCH llenado). Columnas recibidas: {sorted(columnas)}."
        )

    @staticmethod
    def _params_a_ajustes(params: Mapping[str, Any]) -> dict[str, Any]:
        """Params planos del trial → sobrescrituras del perfil (una sola regla).

        Los pesos planos (``weight_name``…) se vuelven el dict ``weights`` con
        sus tres claves (phonetic = 0 por defecto); el resto va tal cual.
        """
        ajustes: dict[str, Any] = {}
        pesos: dict[str, Any] = {}
        for clave, valor in params.items():
            if clave in _PESOS:
                pesos[_PESOS[clave]] = valor
            else:
                ajustes[clave] = valor
        if pesos:
            ajustes["weights"] = {
                "name": pesos.get("name", 0.5),
                "nit": pesos.get("nit", 0.5),
                "phonetic": pesos.get("phonetic", 0.0),
            }
        return ajustes

    def _build_config_for_trial(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Inyecta los params del trial en una copia del base_config."""
        cfg = copy.deepcopy(self.base_config)
        cfg["profiles"][cfg["profile"]].update(self._params_a_ajustes(params))
        return cfg

    def ajustes_perfil(self, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Params como sobrescrituras del perfil: lo que ``linkage(ajustes_perfil=...)`` recibe.

        Args:
            params: params planos (p. ej. ``result["best_params"]``). Si es
                None se usan los mejores de la última ``optimize``.

        Raises:
            ValueError: si no hay params y aún no se ha optimizado.
        """
        if params is None:
            if self.best_params is None:
                raise ValueError(
                    "No hay best_params: llame a optimize() primero o pase params. "
                    "Sin eso no hay ajustes que aplicar a linkage()."
                )
            params = self.best_params
        return self._params_a_ajustes(params)

    def _run_orchestrator(self, cfg: dict[str, Any]) -> tuple[pd.DataFrame | None, float]:
        """Corre Orchestrator en un work_dir temporal y devuelve correlativa + tiempo.

        Args:
            cfg: configuración inyectada del trial.

        Returns:
            (correlativa_df, tiempo_segundos). Si el run falla, correlativa=None.
        """
        from ..pipeline.orchestrator import Orchestrator

        tmp_dir = Path(tempfile.mkdtemp(prefix="optuna_trial_"))
        if not self.keep_workdir:
            self._tmp_workdirs.append(tmp_dir)
        cfg["output_directory"] = str(tmp_dir)

        t0 = time.perf_counter()
        try:
            if self.silent:
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    orch = Orchestrator(
                        config=cfg,
                        sources=copy.deepcopy(self.sources),
                        work_dir=str(tmp_dir),
                    )
                    result = orch.run(skip_reporting=True)
            else:
                orch = Orchestrator(
                    config=cfg,
                    sources=copy.deepcopy(self.sources),
                    work_dir=str(tmp_dir),
                )
                result = orch.run(skip_reporting=True)
        except Exception as exc:
            self.logger.warning(f"Trial falló durante Orchestrator.run(): {exc}")
            return None, time.perf_counter() - t0

        return result.get("correlative"), time.perf_counter() - t0

    def _columna_id(self, marco: pd.DataFrame) -> str | None:
        """Columna de identificador de registro: ``col_id`` si se pasó, si no una conocida."""
        candidatas = (self.col_id, *_COLUMNAS_ID) if self.col_id else _COLUMNAS_ID
        for candidata in candidatas:
            if candidata in marco.columns:
                return candidata
        return None

    def _mensaje_sin_columna_id(self, que: str) -> str:
        """Mensaje accionable cuando ``que`` (la correlativa, una fuente) no trae id."""
        conocidas = ", ".join(_COLUMNAS_ID[:2])
        esperada = f"'{self.col_id}' ni " if self.col_id else ""
        return (
            f"Qué pasó: {que} no trae {esperada}ninguna columna de identificador conocida "
            f"({conocidas}). Por qué importa: sin ella no se cruza con la verdad y ningún "
            "trial se puede medir. Qué hacer: pase col_id=<columna única por fila de sus "
            "fuentes> al construir el optimizador, o renombre esa columna a 'ID'."
        )

    def _evaluate(
        self,
        correlative: pd.DataFrame,
        optimization_target: str,
        truth: pd.DataFrame | None = None,
    ) -> tuple[float, dict[str, Any]]:
        """Alinea correlativa con la verdad y calcula métricas.

        Args:
            correlative: correlativa del Orchestrator (``ID_GRUPO`` + id).
            optimization_target: clave de ``OBJETIVOS_VALIDOS`` que da el score.
            truth: verdad contra la que evaluar; por defecto ``self.truth``
                (la validación cruzada pasa un pliegue).

        Returns:
            (score_target, metrics_dict). Si la verdad trae ``ESTRATO`` se
            añaden las métricas del banco (``macro_f1``, ``b3_f1``,
            ``f1_estrato_<nombre>``, ``fp_que_tocan_negativo``) calculadas
            con ``evaluation.banco.evaluar_calidad``.
        """
        verdad = self.truth if truth is None else truth
        # Distingue «objetivo desconocido» de «macro_f1 sin ESTRATO» antes de
        # evaluar: el notebook de F2.15 llama a evaluar_correlativa y una clave
        # mal escrita no debe culpar a ESTRATO (hallazgo de revisión, ronda 3).
        self._validar_objetivo(optimization_target, verdad)
        if "ID_GRUPO" not in correlative.columns:
            return 0.0, {"error": "correlativa sin ID_GRUPO"}

        col_id = self._columna_id(correlative)
        if col_id is None:
            return 0.0, {"error": self._mensaje_sin_columna_id("la correlativa")}

        if self._es_verdad_por_pares(verdad):
            return self._evaluar_por_pares(correlative, col_id, verdad, optimization_target)

        # Merge — usar inner join + sin validate estricto (algunos pipelines
        # pueden duplicar registros y queremos seguir evaluando)
        df = verdad.merge(
            correlative[[col_id, "ID_GRUPO"]].rename(columns={col_id: "ID_REGISTRO"}),
            on="ID_REGISTRO",
            how="inner",
        )
        if len(df) == 0:
            return 0.0, {"error": "no hay solapamiento entre truth y correlativa"}

        try:
            m: PairwiseMetrics = evaluar_pares(
                df["ID_GROUP"].astype(str).values,
                df["ID_GRUPO"].astype(str).values,
            )
        except Exception as exc:
            return 0.0, {"error": f"evaluar_pares falló: {exc}"}

        metrics: dict[str, Any] = {
            "precision": m.precision,
            "recall": m.recall,
            "f1": m.f1,
            "tp": m.tp,
            "fp": m.fp,
            "fn": m.fn,
            "n_records": m.n_records,
            "n_pred_groups": m.n_pred_groups,
        }
        metrics["f2"] = _f_beta(m.precision, m.recall)

        if "ESTRATO" in df.columns:
            metrics.update(self._metricas_banco(df))

        if optimization_target not in metrics:
            # Guarda defensiva (``_validar_objetivo`` ya cubre el caso normal):
            # un objetivo que no se pudo calcular no debe devolver el F1 global
            # en silencio como si fuera el objetivo.
            raise ValueError(
                f"La verdad evaluada no permite calcular '{optimization_target}' "
                "(falta ESTRATO): se devolvería otra métrica como si fuera el objetivo. "
                "Pase una verdad con ESTRATO o use 'f1'."
            )
        score = float(metrics[optimization_target])
        if optimization_target == "macro_f1" and not math.isfinite(score):
            # evaluar_calidad da NaN cuando ningún estrato tiene pares (grupos
            # de un registro, pliegue minúsculo). Un NaN entra a Optuna como
            # trial «completed» que el mejor-hasta-ahora ignora en silencio.
            metrics["advertencia"] = "macro_f1 sin estratos con pares"
            score = 0.0
        return score, metrics

    @staticmethod
    def _evaluar_por_pares(
        correlative: pd.DataFrame, col_id: str, verdad: pd.DataFrame, optimization_target: str
    ) -> tuple[float, dict[str, Any]]:
        """Verdad por pares: la regla vive en ``evaluation.pairwise``; aquí solo se alinea."""
        grupos = pd.Series(
            correlative["ID_GRUPO"].astype(str).to_numpy(),
            index=correlative[col_id].astype(str).to_numpy(),
        )
        try:
            medidas = evaluar_pares_etiquetados(verdad, grupos)
        except ValueError as exc:
            return 0.0, {"error": f"evaluar_pares_etiquetados falló: {exc}"}
        metrics: dict[str, Any] = medidas.como_dict()
        metrics["f2"] = _f_beta(medidas.precision, medidas.recall)
        return float(metrics[optimization_target]), metrics

    @staticmethod
    def _metricas_banco(df: pd.DataFrame) -> dict[str, Any]:
        """Métricas estratificadas con la regla del banco (escrita una vez allí).

        ``evaluar_calidad`` exige ``REGIMEN`` y ``CASO``; si la verdad no los
        trae se rellenan con un marcador explícito. Sin ``CASO`` ningún caso
        empieza por ``negativo``, así que ``fp_que_tocan_negativo`` vale 0 por
        construcción: se añade ``nota_fp_negativos`` para que no llegue al
        notebook como si se hubiera medido.
        """
        referencia = df.copy()
        for columna in ("REGIMEN", "CASO"):
            if columna not in referencia.columns:
                referencia[columna] = "sin_informar"
        calidad = evaluar_calidad(referencia, referencia["ID_GRUPO"].astype(str).to_numpy())
        resultado: dict[str, Any] = {
            "macro_f1": calidad.macro_f1,
            "b3_f1": calidad.b3_f1,
            "fp_que_tocan_negativo": calidad.fp_que_tocan_negativo,
        }
        if "CASO" not in df.columns:
            resultado["nota_fp_negativos"] = (
                "CASO ausente en la verdad: fp_que_tocan_negativo no medido (0 por construcción)"
            )
        for estrato, metricas in calidad.calidad_por_estrato.items():
            resultado[f"f1_estrato_{estrato}"] = metricas["f1"]
        return resultado

    def _validar_objetivo(
        self, optimization_target: str, verdad: pd.DataFrame | None = None
    ) -> None:
        """Fail-fast: objetivo conocido y, para ``macro_f1``, verdad con ``ESTRATO``.

        Args:
            optimization_target: clave de ``OBJETIVOS_VALIDOS``.
            verdad: la verdad que de veras se va a evaluar; por defecto
                ``self.truth``. ``evaluar_config(truth=otra)`` valida ``otra``.
        """
        verdad = self.truth if verdad is None else verdad
        if optimization_target not in OBJETIVOS_VALIDOS:
            raise ValueError(
                f"optimization_target debe ser uno de {sorted(OBJETIVOS_VALIDOS)}, "
                f"recibido: '{optimization_target}'"
            )
        if optimization_target == "macro_f1" and self._es_verdad_por_pares(verdad):
            raise ValueError(
                "optimization_target='macro_f1' no existe para una verdad por pares "
                "etiquetados: el macro-F1 promedia el F1 por ESTRATO de una partición "
                "completa. Con pares use 'f1', 'f2', 'precision' o 'recall'; para "
                "macro_f1 pase una verdad por registro (ID_REGISTRO, ID_GROUP, ESTRATO)."
            )
        if optimization_target == "macro_f1" and "ESTRATO" not in verdad.columns:
            raise ValueError(
                "optimization_target='macro_f1' exige la columna ESTRATO en truth: el "
                "macro-F1 es el promedio del F1 por estrato (evaluation.banco). Añada "
                "ESTRATO a la verdad (como data/benchmark/benchmark_institucional.csv.gz) "
                "o use 'f1'."
            )

    def _cleanup_workdirs(self) -> None:
        """Borra workdirs temporales acumulados."""
        import shutil

        for d in self._tmp_workdirs:
            try:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass
        self._tmp_workdirs.clear()

    # ─────────────────────────────────────────────────────────────────────
    #  API pública
    # ─────────────────────────────────────────────────────────────────────
    def optimize(
        self,
        n_trials: int = 20,
        optimization_target: str = "f1",
        time_budget_minutes: float | None = None,
        time_penalty_seconds: float = 600.0,
        show_progress_bar: bool = True,
        sampler=None,
        pruner=None,
        seed: int | None = 42,
    ) -> dict[str, Any]:
        """Ejecuta el bucle de optimización.

        Args:
            n_trials: número máximo de trials.
            optimization_target: métrica a maximizar. Opciones: 'f1' (default),
                'f2' (favorece recall), 'precision', 'recall', 'macro_f1'
                (promedio del F1 por ESTRATO, regla del banco; exige ESTRATO
                en truth).
            time_budget_minutes: presupuesto máximo en minutos (None = sin tope).
            time_penalty_seconds: si un trial tarda más, penaliza el score
                proporcionalmente. Default 600s (10 min). Set 0 para desactivar.
            show_progress_bar: barra de progreso Optuna.
            sampler: sampler de Optuna opcional (ej. TPESampler). Si se
                proporciona, tiene prioridad sobre ``seed``.
            pruner: pruner de Optuna opcional.
            seed: semilla del TPESampler creado por defecto. Default 42 para
                que el ejemplo documentado sea reproducible. Use None para el
                comportamiento aleatorio de Optuna.

        Returns:
            Dict con:
              - 'best_params': mejores hiperparámetros encontrados.
              - 'best_score': mejor score (de optimization_target) entre los
                trials completados (los fallidos no cuentan aunque Optuna los vea como 0,0).
              - 'best_config': config completo listo para Orchestrator.
              - 'best_metrics': métricas completas del mejor trial.
              - 'optimization_history': lista de dicts por trial.
              - 'study': objeto Optuna study (acceso completo).
              - 'total_trials_completed': cantidad de trials no-fallidos.
        """
        import optuna

        self._validar_objetivo(optimization_target)

        self.logger.info(
            f"🎯 Iniciando optimización sobre Orchestrator: "
            f"n_trials={n_trials}, target={optimization_target}"
        )

        best_metrics_holder: dict[str, Any] = {}

        def objective(trial):
            params = self.search_space(trial)
            cfg = self._build_config_for_trial(params)

            correlative, elapsed = self._run_orchestrator(cfg)
            if correlative is None:
                self.optimization_history.append(
                    _trial_fallido(trial.number, params, elapsed, "Orchestrator.run() falló")
                )
                return 0.0

            score, metrics = self._evaluate(correlative, optimization_target)
            if "error" in metrics:
                # La correlativa no se pudo medir contra la verdad (ids que no
                # alinean, etc.): es un trial FALLIDO, no un score 0,0. Si todos
                # fallan, el estudio termina sin trials completados y
                # guardar_estudio se niega (revisión F2.15).
                self.logger.warning(f"Trial falló al evaluar la correlativa: {metrics['error']}")
                self.optimization_history.append(
                    _trial_fallido(trial.number, params, elapsed, str(metrics["error"]))
                )
                return 0.0

            # Penalización por tiempo (opcional)
            if time_penalty_seconds > 0 and elapsed > time_penalty_seconds:
                penalty = min((elapsed - time_penalty_seconds) / 1000.0, 0.5)
                score = score * (1 - penalty)

            self.optimization_history.append(
                {
                    "trial": trial.number,
                    "params": params,
                    "score": float(score),
                    "elapsed_s": elapsed,
                    "status": "completed",
                    "metrics": metrics,
                }
            )

            # Guardar métricas completas del mejor trial COMPLETADO visto hasta
            # ahora. Es la única fuente de best_params, best_score y mejor_trial:
            # un trial failed devuelve 0,0 a Optuna y, si el mejor completado
            # también puntúa 0,0, study.best_trial es el fallido (ronda 2).
            if not best_metrics_holder or score > best_metrics_holder.get("score", -1):
                best_metrics_holder["trial"] = trial.number
                best_metrics_holder["score"] = float(score)
                best_metrics_holder["metrics"] = metrics
                best_metrics_holder["params"] = params

            return float(score)

        # Crear estudio
        study_kwargs: dict[str, Any] = {"direction": "maximize"}
        if sampler is None and seed is not None:
            sampler = optuna.samplers.TPESampler(seed=seed)
        if sampler is not None:
            study_kwargs["sampler"] = sampler
        if pruner is not None:
            study_kwargs["pruner"] = pruner
        study = optuna.create_study(**study_kwargs)

        timeout_s = time_budget_minutes * 60 if time_budget_minutes else None
        try:
            study.optimize(
                objective,
                n_trials=n_trials,
                timeout=timeout_s,
                show_progress_bar=show_progress_bar,
            )
        finally:
            self._cleanup_workdirs()

        # Construir best_config
        completed_trials = [h for h in self.optimization_history if h["status"] == "completed"]
        if not completed_trials:
            self.logger.warning("Ningún trial completó exitosamente.")
            return {
                "best_params": None,
                "best_score": 0.0,
                "best_config": None,
                "best_metrics": {},
                "optimization_history": self.optimization_history,
                "study": study,
                "total_trials_completed": 0,
            }

        self.best_params = best_metrics_holder["params"]
        self.best_config = self._build_config_for_trial(self.best_params)
        best_metrics = best_metrics_holder["metrics"]
        mejor_trial: int = best_metrics_holder["trial"]
        mejor_score: float = best_metrics_holder["score"]
        self._resumen_estudio = {
            "objetivo": optimization_target,
            "n_trials": n_trials,
            "trials_completados": len(completed_trials),
            "mejor_trial": mejor_trial,
            "mejor_score": mejor_score,
            "mejores_metricas": best_metrics,
        }

        self.logger.info(
            f"✅ Optimización completa. Mejor {optimization_target}: "
            f"{mejor_score:.4f} (trial #{mejor_trial}). "
            f"Trials completados: {len(completed_trials)}/{n_trials}"
        )

        return {
            "best_params": self.best_params,
            "best_score": mejor_score,
            "best_config": self.best_config,
            "best_metrics": best_metrics,
            "optimization_history": self.optimization_history,
            "study": study,
            "total_trials_completed": len(completed_trials),
        }

    def evaluar_config(
        self,
        params: Mapping[str, Any],
        optimization_target: str = "f1",
        truth: pd.DataFrame | None = None,
        sources: Mapping[str, pd.DataFrame] | None = None,
    ) -> dict[str, Any]:
        """Corre el Orchestrator UNA vez con ``params`` y devuelve sus métricas.

        Es el bloque que ``optimize`` repite por trial y que la validación
        cruzada repite por pliegue; público para medir un candidato concreto.

        Returns:
            Métricas de ``_evaluate`` más ``score`` (la del objetivo) y
            ``elapsed_s``. Si el run falla o la correlativa no se puede medir
            contra la verdad (ids que no alinean): ``{"error": ..., "score":
            0.0, "elapsed_s": ...}``, sin métricas; el mismo caso que
            ``optimize`` registra como trial ``failed``.
        """
        self._validar_objetivo(optimization_target, truth)
        cfg = self._build_config_for_trial(params)
        fuentes_previas = self.sources
        if sources is not None:
            self.sources = dict(sources)
        try:
            correlativa, elapsed = self._run_orchestrator(cfg)
        finally:
            self.sources = fuentes_previas
            self._cleanup_workdirs()
        if correlativa is None:
            return {"error": "Orchestrator.run() falló", "score": 0.0, "elapsed_s": elapsed}
        score, metricas = self._evaluate(correlativa, optimization_target, truth)
        if "error" in metricas:
            self.logger.warning(f"No se pudo medir la correlativa: {metricas['error']}")
            return {"error": metricas["error"], "score": 0.0, "elapsed_s": elapsed}
        return {**metricas, "score": score, "elapsed_s": elapsed}

    def validacion_cruzada(
        self,
        params: Mapping[str, Any],
        pliegues: int = 3,
        optimization_target: str = "f1",
    ) -> dict[str, Any]:
        """Evalúa ``params`` en ``pliegues`` subconjuntos de GRUPOS completos.

        Reemplaza a ``GroundTruthEvaluator.cross_validate`` (retirada en F2.8
        con ``OptimizationEngine``). Diferencia deliberada: aquella partía la
        verdad por FILA con ``KFold``, lo que separa los registros de un mismo
        grupo entre pliegues y vuelve imposible medir recall. Aquí se reutiliza
        ``evaluation.banco._seleccionar_pliegue`` (hash estable del
        identificador de grupo: el mismo pliegue contiene siempre los mismos
        grupos) y las fuentes se recortan a los registros de ese pliegue.

        Returns:
            ``objetivo``, ``pliegues``, ``por_pliegue`` (score por pliegue),
            ``media``, ``desviacion`` y ``metricas_por_pliegue``.

        Raises:
            ValueError: ``pliegues`` < 2, objetivo inválido, o una fuente sin
                columna de identificador conocida (no se puede recortar).
        """
        from .banco import _seleccionar_pliegue

        if pliegues < 2:
            raise ValueError(f"pliegues debe ser >= 2 para validar cruzado; recibido {pliegues}.")
        if self.verdad_por_pares:
            raise ValueError(
                "La validación cruzada reparte GRUPOS completos entre pliegues y exige la "
                "verdad por registro (ID_REGISTRO, ID_GROUP); la verdad por pares etiquetados "
                "no tiene grupos. Mida con evaluar_config/optimize, o construya una verdad "
                "por registro."
            )
        self._validar_objetivo(optimization_target)
        for nombre, fuente in self.sources.items():
            if self._columna_id(fuente) is None:
                raise ValueError(
                    self._mensaje_sin_columna_id(f"la fuente '{nombre}'")
                    + " Sin esa columna tampoco se puede recortar la fuente al pliegue."
                )

        puntajes: list[float] = []
        metricas_por_pliegue: list[dict[str, Any]] = []
        for pliegue in range(pliegues):
            verdad = _seleccionar_pliegue(self.truth, pliegue, pliegues)
            ids = set(verdad["ID_REGISTRO"].astype(str))
            fuentes = {
                nombre: fuente.loc[fuente[self._columna_id(fuente)].astype(str).isin(ids)]
                .reset_index(drop=True)
                .copy()
                for nombre, fuente in self.sources.items()
            }
            fuentes = {n: f for n, f in fuentes.items() if not f.empty}
            metricas = self.evaluar_config(params, optimization_target, verdad, fuentes)
            metricas["pliegue"] = pliegue
            puntajes.append(float(metricas["score"]))
            metricas_por_pliegue.append(metricas)
            self.logger.info(
                f"Pliegue {pliegue + 1}/{pliegues}: {optimization_target}={puntajes[-1]:.4f}"
            )

        return {
            "objetivo": optimization_target,
            "pliegues": pliegues,
            "por_pliegue": puntajes,
            "media": float(np.mean(puntajes)),
            "desviacion": float(np.std(puntajes)),
            "metricas_por_pliegue": metricas_por_pliegue,
        }

    def evaluar_correlativa(
        self, correlativa: pd.DataFrame, optimization_target: str = "f1"
    ) -> tuple[float, dict[str, Any]]:
        """Mide una correlativa ya producida (p. ej. por ``linkage()``) contra la verdad.

        Es la misma regla con la que ``optimize`` puntúa cada trial, expuesta
        para comprobar que los ajustes aplicados en producción reproducen el
        score del estudio. La correlativa debe traer ``ID_GRUPO`` y la columna
        de identificador que usa la verdad (``col_id`` o, por defecto,
        ``ID_REGISTRO``/``ID``): la del estándar de salida trae todas las
        columnas de la fuente, así que ``res.correlativa[[col_id, "ID_GRUPO"]]``
        basta.

        Returns:
            ``(score, métricas)`` como ``_evaluate``; si la correlativa no se
            puede alinear, ``score`` es 0.0 y ``métricas`` trae ``error``.
        """
        return self._evaluate(correlativa, optimization_target)

    def guardar_estudio(self, ruta: str | Path) -> Path:
        """Escribe el estudio como JSON: lo que hace falta para reproducir y aplicar.

        Contenido: ``version`` de la librería, ``fecha``, ``objetivo``,
        ``verdad`` (``"registro"`` o ``"pares"``), ``n_trials``,
        ``trials_completados``, ``mejor_trial``, ``mejor_score``,
        ``mejores_parametros``, ``mejores_metricas``, ``ajustes_perfil`` (lo
        que recibe ``linkage(ajustes_perfil=...)``) e ``historial`` (un
        registro por trial con ``params``, ``score``, ``elapsed_s``,
        ``status`` y ``metrics``; los fallidos traen además ``error``).

        Raises:
            ValueError: si aún no se ha optimizado (no hay estudio que guardar).
        """
        if self._resumen_estudio is None or self.best_params is None:
            raise ValueError(
                "No hay estudio que guardar: llame a optimize() primero y con al menos un "
                "trial completado."
            )
        from datetime import datetime, timezone

        from .. import __version__

        estudio = {
            "version": __version__,
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "verdad": "pares" if self.verdad_por_pares else "registro",
            **self._resumen_estudio,
            "mejores_parametros": self.best_params,
            "ajustes_perfil": self.ajustes_perfil(),
            "historial": self.optimization_history,
        }
        destino = Path(ruta)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(estudio, ensure_ascii=False, indent=2, default=_a_json), encoding="utf-8"
        )
        self.logger.info(f"Estudio guardado en: {destino}")
        return destino

    def history_df(self) -> pd.DataFrame:
        """Devuelve el historial de optimización como DataFrame plano."""
        if not self.optimization_history:
            return pd.DataFrame()
        rows = []
        for h in self.optimization_history:
            row = {
                "trial": h["trial"],
                "score": h["score"],
                "elapsed_s": h["elapsed_s"],
                "status": h["status"],
            }
            if h.get("error"):
                row["error"] = h["error"]
            row.update({f"param_{k}": v for k, v in h["params"].items()})
            row.update({f"metric_{k}": v for k, v in h["metrics"].items()})
            rows.append(row)
        return pd.DataFrame(rows)
