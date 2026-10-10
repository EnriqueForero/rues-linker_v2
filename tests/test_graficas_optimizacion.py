"""F2.14 — visualización ligera de una optimización (matplotlib, sin dashboard).

Porta UNA vez lo útil de ``OptimizationVisualizerLite`` (retirado en F2.8):
la convergencia (score de cada trial y mejor acumulado) y, nuevo, la
importancia de parámetros que Optuna calcula. Sin plotly ni optuna-dashboard.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from record_linkage.evaluation.graficas_optimizacion import (
    graficar_convergencia,
    graficar_importancia,
)

pytest.importorskip("matplotlib")


def _historial() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trial": [0, 1, 2, 3],
            "score": [0.5, 0.7, 0.6, 0.8],
            "elapsed_s": [1.0, 1.0, 1.0, 1.0],
            "status": ["completed", "completed", "failed", "completed"],
            "param_score_threshold": [0.5, 0.6, 0.7, 0.8],
        }
    )


def test_convergencia_devuelve_figura_y_guarda(tmp_path: Path) -> None:
    ruta = tmp_path / "convergencia.png"
    figura = graficar_convergencia(_historial(), ruta=ruta)
    assert ruta.is_file() and ruta.stat().st_size > 0
    ejes = figura.axes[0]
    # Tres trazos: puntos de los completados, mejor acumulado y cruces de fallidos.
    assert len(ejes.lines) == 3
    assert len(ejes.lines[0].get_xdata()) == 3
    mejor = ejes.lines[1].get_ydata()
    assert list(mejor) == [0.5, 0.7, 0.7, 0.8], "el fallido (0.6) no mueve el mejor acumulado"
    assert list(ejes.lines[2].get_xdata()) == [2]


def test_convergencia_sin_historial_falla_rapido() -> None:
    with pytest.raises(ValueError, match="historial"):
        graficar_convergencia(pd.DataFrame())
    with pytest.raises(ValueError, match="score"):
        graficar_convergencia(pd.DataFrame({"trial": [0]}))


def test_importancia_con_study(tmp_path: Path) -> None:
    optuna = pytest.importorskip("optuna")
    optuna.logging.set_verbosity(optuna.logging.ERROR)

    def objetivo(trial):
        x = trial.suggest_float("x", 0.0, 1.0)
        y = trial.suggest_float("y", 0.0, 1.0)
        return x * 10 + y * 0.01

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.RandomSampler(seed=0))
    study.optimize(objetivo, n_trials=12)
    ruta = tmp_path / "importancia.png"
    figura = graficar_importancia(study, ruta=ruta)
    assert ruta.is_file()
    etiquetas = [t.get_text() for t in figura.axes[0].get_yticklabels()]
    assert set(etiquetas) == {"x", "y"}


def test_importancia_sin_trials_falla_rapido() -> None:
    optuna = pytest.importorskip("optuna")
    study = optuna.create_study()
    with pytest.raises(ValueError, match="trials"):
        graficar_importancia(study)
