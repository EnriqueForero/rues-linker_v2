"""evaluation.graficas_optimizacion — dos figuras ligeras de una optimización.

Porta UNA vez (F2.14) lo que ``optimization.visualizer.OptimizationVisualizerLite``
(retirado en F2.8, plotly → HTML) aportaba: la convergencia del objetivo
(score de cada trial y mejor acumulado). Añade la importancia de parámetros
que Optuna ya calcula (``optuna.importance.get_param_importances``). Todo con
``matplotlib`` (extra ``viz``), sin plotly ni optuna-dashboard, y sin ``pyplot``:
se construye una ``Figure`` autónoma, que funciona igual en Colab, en un
script sin pantalla y en las pruebas.

Uso::

    historial = optimizer.history_df()
    graficar_convergencia(historial, ruta="convergencia.png")
    graficar_importancia(result["study"], ruta="importancia.png")
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _nueva_figura(ancho: float, alto: float) -> Any:
    """``matplotlib.figure.Figure`` con mensaje accionable si falta la librería."""
    try:
        from matplotlib.figure import Figure
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise ImportError(
            "matplotlib no está instalado y las gráficas de optimización lo necesitan. "
            "Instálelo con: pip install 'rues-linker[viz]'"
        ) from exc
    return Figure(figsize=(ancho, alto), constrained_layout=True)


def _guardar(figura: Any, ruta: str | Path | None) -> None:
    if ruta is not None:
        destino = Path(ruta)
        destino.parent.mkdir(parents=True, exist_ok=True)
        figura.savefig(destino, dpi=120)


def graficar_convergencia(
    historial: pd.DataFrame,
    *,
    objetivo: str = "score",
    titulo: str = "Convergencia de la optimización",
    ruta: str | Path | None = None,
) -> Any:
    """Score de cada trial (puntos) y mejor score acumulado (línea).

    Args:
        historial: ``OrchestratorOptimizer.history_df()`` o cualquier DataFrame
            con ``trial`` y la columna ``objetivo``. Los trials con
            ``status != "completed"`` (si la columna existe) se dibujan como
            cruces y no mueven el mejor acumulado.
        objetivo: columna a graficar (``score`` por defecto).
        titulo: título de la figura.
        ruta: si se indica, guarda la figura (PNG, SVG… según la extensión).

    Returns:
        ``matplotlib.figure.Figure``.

    Raises:
        ValueError: historial vacío o sin la columna del objetivo.
    """
    if historial.empty:
        raise ValueError(
            "El historial está vacío: no hay trials que graficar. Corra optimize() "
            "antes o pase history_df() de una optimización con trials completados."
        )
    if objetivo not in historial.columns or "trial" not in historial.columns:
        raise ValueError(
            f"El historial debe tener las columnas 'trial' y '{objetivo}'; tiene "
            f"{list(historial.columns)}. Use history_df() del optimizador."
        )
    marco = historial.sort_values("trial", kind="stable")
    completados = (
        marco["status"].eq("completed")
        if "status" in marco.columns
        else pd.Series(True, index=marco.index)
    )
    # ffill: un trial fallido no deja un hueco en la línea del mejor acumulado.
    mejor = marco[objetivo].where(completados).cummax().ffill()

    figura = _nueva_figura(8.0, 4.5)
    ejes = figura.add_subplot(1, 1, 1)
    ejes.plot(
        marco.loc[completados, "trial"],
        marco.loc[completados, objetivo],
        linestyle="none",
        marker="o",
        alpha=0.7,
        label="Score del trial",
    )
    ejes.plot(marco["trial"], mejor, color="tab:red", linewidth=2.0, label="Mejor acumulado")
    fallidos = ~completados
    if fallidos.any():
        ejes.plot(
            marco.loc[fallidos, "trial"],
            marco.loc[fallidos, objetivo],
            linestyle="none",
            marker="x",
            color="gray",
            label="Trial fallido",
        )
    ejes.set_title(titulo)
    ejes.set_xlabel("Trial")
    ejes.set_ylabel(objetivo)
    ejes.grid(True, alpha=0.3)
    ejes.legend(loc="lower right")
    _guardar(figura, ruta)
    return figura


def graficar_importancia(
    study: Any,
    *,
    titulo: str = "Importancia de los parámetros",
    ruta: str | Path | None = None,
) -> Any:
    """Barras horizontales con la importancia de cada parámetro según Optuna.

    Args:
        study: ``result["study"]`` de ``OrchestratorOptimizer.optimize``.
        titulo: título de la figura.
        ruta: si se indica, guarda la figura.

    Returns:
        ``matplotlib.figure.Figure``.

    Raises:
        ValueError: el estudio no tiene trials completados suficientes (Optuna
            necesita al menos dos con parámetros distintos).
    """
    import optuna

    completados = [
        t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE and t.params
    ]
    if len(completados) < 2:
        raise ValueError(
            f"El estudio tiene {len(completados)} trials completados con parámetros; la "
            "importancia necesita al menos 2 con valores distintos. Corra más trials."
        )
    importancias = optuna.importance.get_param_importances(study)
    nombres = list(importancias)
    valores = [importancias[n] for n in nombres]

    figura = _nueva_figura(8.0, 0.5 * len(nombres) + 1.5)
    ejes = figura.add_subplot(1, 1, 1)
    posiciones = range(len(nombres))
    ejes.barh(list(posiciones), valores[::-1], color="tab:blue")
    ejes.set_yticks(list(posiciones))
    ejes.set_yticklabels(nombres[::-1])
    ejes.set_xlabel("Importancia (fracción)")
    ejes.set_title(titulo)
    ejes.grid(True, axis="x", alpha=0.3)
    _guardar(figura, ruta)
    return figura
