"""Tests del cierre de deuda v0.7.4 — checkpoints stale en firmas MinHash.

Bug histórico: `_validate_signatures_file` validaba solo por
`(n_records, num_perm, ngram)`. Dos datasets DISTINTOS con el mismo número
de filas reusaban firmas incorrectas. Observado dos veces:
    - Sprint 0.8.1 (reutilización HDF5 ciega).
    - Sprint 0.9.0 (baseline truncado a 1131/12427 por checkpoint stale).

Fix: huella de contenido (`_content_fingerprint`) en los attrs del HDF5,
validada en cada reuso.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from record_linkage.engine.lsh.disk_based import (
    DiskBasedLSHEngine,
    _content_fingerprint,
)


def _engine(tmp: str) -> DiskBasedLSHEngine:
    eng = DiskBasedLSHEngine(profile={})
    eng._signatures_file = Path(tmp) / "sig.h5"
    eng._num_perm, eng._ngram, eng._chunk_size = 128, 3, 50_000
    return eng


# ─────────────────────────────────────────────────────────────────────────
# _content_fingerprint
# ─────────────────────────────────────────────────────────────────────────


def test_fingerprint_distingue_corpus_mismo_tamano():
    """Dos corpus distintos con el MISMO número de filas → huellas distintas.

    Este es el corazón del bug: antes, ambos pasaban la validación por
    coincidir en n_records.
    """
    a = np.array([f"EMPRESA ALPHA {i}" for i in range(5000)])
    b = np.array([f"COMPANY BETA {i}" for i in range(5000)])
    assert _content_fingerprint(a, 128, 3) != _content_fingerprint(b, 128, 3)


def test_fingerprint_detecta_cambio_fuera_de_la_muestra_historica():
    """Una fila no múltiplo de 1000 también invalida el checkpoint."""
    a = np.array([f"EMPRESA {i}" for i in range(3_000)], dtype=object)
    b = a.copy()
    b[1_337] = "CONTENIDO CAMBIADO"
    assert _content_fingerprint(a, 128, 3) != _content_fingerprint(b, 128, 3)


def test_fingerprint_determinista():
    a = np.array([f"X {i}" for i in range(2000)])
    assert _content_fingerprint(a, 128, 3) == _content_fingerprint(a, 128, 3)


def test_fingerprint_sensible_a_num_perm():
    a = np.array([f"X {i}" for i in range(2000)])
    assert _content_fingerprint(a, 128, 3) != _content_fingerprint(a, 256, 3)


def test_fingerprint_sensible_a_ngram():
    a = np.array([f"X {i}" for i in range(2000)])
    assert _content_fingerprint(a, 128, 3) != _content_fingerprint(a, 128, 4)


def test_fingerprint_sensible_a_tamano():
    a = np.array([f"X {i}" for i in range(2000)])
    b = np.array([f"X {i}" for i in range(1000)])
    assert _content_fingerprint(a, 128, 3) != _content_fingerprint(b, 128, 3)


def test_fingerprint_longitud_estable():
    a = np.array([f"X {i}" for i in range(100)])
    fp = _content_fingerprint(a, 128, 3)
    assert isinstance(fp, str)
    assert len(fp) == 16


# ─────────────────────────────────────────────────────────────────────────
# Integración con _generate_signatures / _validate_signatures_file
# ─────────────────────────────────────────────────────────────────────────


def test_reusa_mismo_corpus():
    """Mismo corpus → el checkpoint se reusa (validación True)."""
    texts = np.array([f"EMPRESA {i}" for i in range(3000)])
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(tmp)
        eng._generate_signatures(pd.DataFrame({"NOMBRE_LIMPIO": texts}))
        fp = _content_fingerprint(texts, 128, 3)
        assert eng._validate_signatures_file(3000, content_fingerprint=fp) is True


def test_no_reusa_corpus_distinto_mismo_tamano():
    """Corpus distinto, mismo número de filas → NO reusa (el bug cerrado)."""
    texts_a = np.array([f"EMPRESA ALPHA {i}" for i in range(3000)])
    texts_b = np.array([f"COMPANY BETA {i}" for i in range(3000)])
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(tmp)
        eng._generate_signatures(pd.DataFrame({"NOMBRE_LIMPIO": texts_a}))
        fp_b = _content_fingerprint(texts_b, 128, 3)
        assert eng._validate_signatures_file(3000, content_fingerprint=fp_b) is False


def test_checkpoint_viejo_sin_huella_no_se_reusa():
    """Un HDF5 sin attr 'content_fp' (formato pre-v0.7.4) NO se reusa cuando
    se exige huella — fuerza regeneración segura una vez."""
    import h5py

    texts = np.array([f"EMPRESA {i}" for i in range(1000)])
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(tmp)
        # Simular checkpoint viejo: escribir attrs SIN content_fp.
        with h5py.File(str(eng._signatures_file), "w") as hf:
            hf.create_dataset("signatures", data=np.zeros((1000, 128), dtype="uint64"))
            hf.attrs["n_records"] = 1000
            hf.attrs["num_perm"] = 128
            hf.attrs["ngram"] = 3
        fp = _content_fingerprint(texts, 128, 3)
        assert eng._validate_signatures_file(1000, content_fingerprint=fp) is False


def test_validacion_sin_huella_mantiene_compat():
    """Si no se pasa huella (content_fingerprint=None), la validación cae al
    comportamiento clásico por (n_records, num_perm, ngram) — retrocompat."""
    texts = np.array([f"EMPRESA {i}" for i in range(1000)])
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(tmp)
        eng._generate_signatures(pd.DataFrame({"NOMBRE_LIMPIO": texts}))
        # Sin huella → valida solo metadata básica.
        assert eng._validate_signatures_file(1000) is True
        assert eng._validate_signatures_file(999) is False  # n distinto


def test_regenera_escribe_huella_nueva():
    """Tras regenerar con corpus B, la huella almacenada corresponde a B."""
    import h5py

    texts_a = np.array([f"ALPHA {i}" for i in range(1500)])
    texts_b = np.array([f"BETA {i}" for i in range(1500)])
    with tempfile.TemporaryDirectory() as tmp:
        eng = _engine(tmp)
        eng._generate_signatures(pd.DataFrame({"NOMBRE_LIMPIO": texts_a}))
        # Regenerar con B (mismo engine, mismo path).
        eng._generate_signatures(pd.DataFrame({"NOMBRE_LIMPIO": texts_b}))
        with h5py.File(str(eng._signatures_file), "r") as hf:
            stored = hf.attrs.get("content_fp")
        assert stored == _content_fingerprint(texts_b, 128, 3)
