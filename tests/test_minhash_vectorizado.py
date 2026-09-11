"""Paridad bit a bit y rendimiento del MinHash vectorizado v0.12.0 (H5).

Hasta 0.11.x ``signatures_batch`` iteraba texto a texto y el hash FNV se
calculaba byte a byte en Python (~9.1K firmas/s medidas en la auditoría,
experimento E8). La versión 0.12.0 vectoriza el camino ASCII con NumPy puro.

Contrato:
    1. PARIDAD BIT A BIT con el camino escalar ``signature()`` (que no cambió)
       para: ASCII, no-ASCII (tildes, coreano), vacíos, cortos, mezclados.
       ⇒ los caches de firmas existentes siguen siendo válidos.
    2. Determinismo entre llamadas y entre sub-lotes.
    3. Rendimiento: ≥ 30.000 firmas/s en 2 vCPU (0.11.x: ~9.100).
"""

from __future__ import annotations

import time

import numpy as np

from record_linkage.engine.lsh.vectorized_minhash import VectorizedMinHasher


def _casos_ascii(n: int = 3000, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    bases = [
        "COMERCIALIZADORA ANDINA SAS",
        "TEXTILES DEL PACIFICO",
        "CAFE DE COLOMBIA EXPORT",
        "AB",  # más corto que el ngram → firma de vacío
        "",
        "AAA",  # un solo n-grama repetible
        "FERRETERIA CENTRAL LTDA 123",
    ]
    return np.array(
        [f"{bases[int(rng.integers(0, len(bases)))]} {i % 97}" for i in range(n)], dtype=object
    )


def test_paridad_bit_a_bit_ascii():
    h = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    texts = _casos_ascii()
    lote = h.signatures_batch(texts)
    for i in (0, 1, 7, 100, 1500, 2999):
        esperado = h.signature(str(texts[i]))
        np.testing.assert_array_equal(lote[i], esperado, err_msg=f"texto[{i}]={texts[i]!r}")
    # Paridad EXHAUSTIVA sobre una muestra completa pequeña
    chico = texts[:200]
    esperados = np.stack([h.signature(str(t)) for t in chico])
    np.testing.assert_array_equal(h.signatures_batch(chico), esperados)


def test_paridad_no_ascii_y_mezclado():
    """Tildes/ñ/coreano van por el camino escalar: mismos valores igual."""
    h = VectorizedMinHasher(num_perm=64, ngram=3, seed=42)
    texts = np.array(
        [
            "CAFÉ DE LA MONTAÑA",
            "주식회사 한국무역",
            "ACME SAS",  # ascii en medio del lote mixto
            "NIÑO Y CÍA",
            "",
            None,
            float("nan"),
        ],
        dtype=object,
    )
    lote = h.signatures_batch(texts)
    esperados = []
    for t in texts:
        if not isinstance(t, str):
            t = "" if t is None or (isinstance(t, float) and np.isnan(t)) else str(t)
        esperados.append(h.signature(t))
    np.testing.assert_array_equal(lote, np.stack(esperados))


def test_paridad_a_traves_de_sublotes():
    """El corte en sub-lotes internos no altera ninguna firma."""
    h = VectorizedMinHasher(num_perm=32, ngram=3, seed=42)
    original = h._SUB_LOTE
    try:
        h._SUB_LOTE = 7  # fuerza muchos cortes
        texts = _casos_ascii(300, seed=1)
        con_cortes = h.signatures_batch(texts)
    finally:
        h._SUB_LOTE = original
    sin_cortes = h.signatures_batch(texts)
    np.testing.assert_array_equal(con_cortes, sin_cortes)


def test_determinismo_entre_llamadas():
    h1 = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    h2 = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    texts = _casos_ascii(500, seed=9)
    np.testing.assert_array_equal(h1.signatures_batch(texts), h2.signatures_batch(texts))


def test_rendimiento_minimo_30k_firmas_s():
    """E8: 0.11.x daba ~9.1K firmas/s. Piso conservador nuevo: 30K/s."""
    h = VectorizedMinHasher(num_perm=128, ngram=3, seed=42)
    texts = _casos_ascii(20_000, seed=3)
    h.signatures_batch(texts[:500])  # warm-up
    t0 = time.time()
    h.signatures_batch(texts)
    rate = len(texts) / (time.time() - t0)
    assert rate >= 30_000, f"{rate:,.0f} firmas/s (< piso de 30.000)"
