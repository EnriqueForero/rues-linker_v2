"""Tests del VectorizedMinHasher (v2.3.0).

Verifica las tres propiedades que lo hacen un reemplazo válido del MinHash por
objeto: (1) estima el Jaccard de n-gramas con bajo error, (2) es determinista
entre instancias con la misma semilla (requisito del checkpointing del índice),
(3) maneja los casos borde igual que el motor original (texto corto, no-string).
"""

from __future__ import annotations

import numpy as np
import pytest

from record_linkage.engine.lsh.vectorized_minhash import (
    VectorizedMinHasher,
    _stable_hash32,
)


def _ngrams(s: str, k: int = 3) -> set[str]:
    return {s[i : i + k] for i in range(len(s) - k + 1)}


def _jaccard(a: str, b: str, k: int = 3) -> float:
    A, B = _ngrams(a, k), _ngrams(b, k)
    return len(A & B) / len(A | B) if (A | B) else 0.0


@pytest.mark.parametrize(
    "a,b",
    [
        ("ECOPETROL S A", "ECOPETROL S.A."),
        ("BAVARIA S A", "BAVARIA SA"),
        ("COMERCIALIZADORA ANDINA", "DISTRIBUIDORA PACIFICO"),
        ("GRUPO EXITO COLOMBIA", "ALMACENES EXITO COLOMBIA"),
        ("AAAAAA", "AAAAAA"),
    ],
)
def test_estima_jaccard(a: str, b: str) -> None:
    """La similitud de firmas estima el Jaccard con error acotado.

    Con 256 permutaciones el error estándar teórico es ~1/sqrt(256)≈0.06;
    permitimos 0.12 de margen para no ser frágiles.
    """
    mh = VectorizedMinHasher(num_perm=256, ngram=3, seed=42)
    est = (mh.signature(a) == mh.signature(b)).mean()
    real = _jaccard(a, b)
    assert abs(est - real) < 0.12, f"jaccard={real:.3f} minhash={est:.3f}"


def test_identicos_similitud_uno() -> None:
    """Dos textos idénticos tienen firma idéntica (similitud 1.0)."""
    mh = VectorizedMinHasher(num_perm=128, ngram=3)
    s = mh.signature("INDUSTRIAS METALICAS DEL CARIBE")
    assert (s == mh.signature("INDUSTRIAS METALICAS DEL CARIBE")).all()


def test_determinismo_entre_instancias() -> None:
    """Misma semilla en instancias distintas -> firmas idénticas.

    Crítico: tras un reinicio de sesión de Colab, el motor crea una instancia
    nueva; si las firmas no fueran reproducibles, el índice LSH parcial quedaría
    inconsistente con las bandas nuevas.
    """
    a = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    b = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    for txt in ["ECOPETROL", "BAVARIA S A S", "x"]:
        assert (a.signature(txt) == b.signature(txt)).all()


def test_stable_hash_es_determinista() -> None:
    """El hash base es estable (no depende de PYTHONHASHSEED)."""
    assert _stable_hash32("ABC") == _stable_hash32("ABC")
    assert _stable_hash32("ABC") != _stable_hash32("ABD")


def test_texto_corto_devuelve_firma_vacia() -> None:
    """Texto más corto que un n-grama -> firma de máximos (igual que el motor)."""
    mh = VectorizedMinHasher(num_perm=64, ngram=3)
    sig = mh.signature("AB")  # len 2 < ngram 3
    assert sig.shape == (64,)
    assert (sig == ((1 << 32) - 1)).all()


def test_batch_equivale_a_individual() -> None:
    """signatures_batch == signature aplicado uno a uno."""
    mh = VectorizedMinHasher(num_perm=64, ngram=3, seed=7)
    textos = np.array(["ECOPETROL S A", "BAVARIA", "AB", "GRUPO EXITO"], dtype=object)
    batch = mh.signatures_batch(textos)
    for i, t in enumerate(textos):
        assert (batch[i] == mh.signature(t)).all()


def test_batch_maneja_no_strings() -> None:
    """El batch tolera None y no-strings sin reventar."""
    mh = VectorizedMinHasher(num_perm=32, ngram=3)
    textos = np.array(["VALIDO TEXTO", None, 12345], dtype=object)
    out = mh.signatures_batch(textos)
    assert out.shape == (3, 32)


# ════════════════════════════════════════════════════════════════════════════
# FIN — 327 archivos procesados
# ════════════════════════════════════════════════════════════════════════════
