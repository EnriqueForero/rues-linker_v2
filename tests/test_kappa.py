"""Validación de ``cohen_kappa`` (scripts/medir_kappa.py) contra scikit-learn.

La compuerta dura de la Fase 2 exige reportar Cohen's kappa. Si la fórmula está
mal, el chequeo de integridad del ground truth (kappa >= 0.80) sería falso. Este
test compara nuestra implementación contra ``sklearn.metrics.cohen_kappa_score``
(oráculo) sobre vectores aleatorios y casos a mano.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import cohen_kappa_score

# medir_kappa.py es un script (no parte del paquete): se carga por ruta.
_PATH = Path(__file__).resolve().parent.parent / "scripts" / "medir_kappa.py"
_spec = importlib.util.spec_from_file_location("medir_kappa", _PATH)
medir_kappa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(medir_kappa)
cohen_kappa = medir_kappa.cohen_kappa
parse_label = medir_kappa.parse_label


@pytest.mark.parametrize("seed", range(10))
def test_kappa_coincide_con_sklearn(seed: int) -> None:
    """kappa propio == kappa de sklearn sobre etiquetas binarias aleatorias."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(20, 300))
    a = rng.integers(0, 2, n).astype(bool)
    # b correlacionado con a (algunos flips) para tener kappa en rango variado.
    flips = rng.random(n) < rng.uniform(0.05, 0.5)
    b = np.where(flips, ~a, a)
    esperado = cohen_kappa_score(a, b)
    obtenido = cohen_kappa(a, b)
    assert np.isclose(obtenido, esperado, atol=1e-9), f"seed={seed}: {obtenido} vs {esperado}"


def test_kappa_acuerdo_perfecto() -> None:
    a = np.array([True, False, True, True, False])
    assert cohen_kappa(a, a) == pytest.approx(1.0)


def test_kappa_todos_misma_clase_y_de_acuerdo() -> None:
    """Caso degenerado pe==1 con acuerdo total → kappa = 1.0 (como sklearn)."""
    a = np.array([True, True, True, True])
    assert cohen_kappa(a, a) == pytest.approx(1.0)


def test_kappa_independiente_da_cero_aprox() -> None:
    """Etiquetas independientes balanceadas → kappa cercano a 0."""
    rng = np.random.default_rng(0)
    a = rng.integers(0, 2, 5000).astype(bool)
    b = rng.integers(0, 2, 5000).astype(bool)
    assert abs(cohen_kappa(a, b)) < 0.1


def test_parse_label_acepta_formatos_humanos() -> None:
    assert parse_label("SI") is True
    assert parse_label("sí") is True
    assert parse_label("1") is True
    assert parse_label("NO") is False
    assert parse_label("0") is False
    assert parse_label("") is None
    assert parse_label("quizás") is None
