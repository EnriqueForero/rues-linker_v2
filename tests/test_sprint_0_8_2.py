"""Tests del Sprint 0.8.2 — Optimizaciones L2_lsh y RAM.

Cubre las tareas EFECTIVAMENTE ejecutadas del sprint:
    2.2 — Cache MinHash persistente entre corridas.
    2.4 — Lazy import de reporting cuando skip_reporting=True.

Tarea 2.1 (paralelización de bandas LSH) NO se implementó: un mini-benchmark
sintético sobre ``_index_band`` mostró que la fase es 98.5% IO (SQLite) y
1.5% CPU (FNV-1a vectorizado). Paralelizar el CPU daría speedup ~1.01x.
Decisión: parquear hasta tener benchmark con corpus real (ver scripts/bench_lsh.py).

Tarea 2.3 (profiling con py-spy) entregada como harness reproducible —
ver scripts/profile_pipeline.sh y docs/PROFILING_v0_8.md.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ═════════════════════════════════════════════════════════════════════════════
# TAREA 2.2 — Cache MinHash
# ═════════════════════════════════════════════════════════════════════════════
from record_linkage.engine.lsh.cache import MinHashCache


def _sample_df(n: int = 100, seed: int = 0) -> pd.DataFrame:
    """DataFrame sintético con la columna NOMBRE_LIMPIO."""
    rng = np.random.default_rng(seed)
    nombres = [f"EMPRESA {i:04d} {rng.integers(1000, 9999)}" for i in range(n)]
    return pd.DataFrame({"NOMBRE_LIMPIO": nombres})


def _sample_signatures(n_rows: int, num_perm: int) -> np.ndarray:
    """Firmas sintéticas (no calculadas — basta con un shape correcto)."""
    rng = np.random.default_rng(42)
    return rng.integers(0, 2**63, size=(n_rows, num_perm), dtype=np.uint64)


def test_key_es_determinista(tmp_path):
    """Mismo input → misma key, sin importar instancias."""
    df = _sample_df(50)
    k1 = MinHashCache.compute_key(df, num_perm=128, ngram=3, seed=42)
    k2 = MinHashCache.compute_key(df, num_perm=128, ngram=3, seed=42)
    assert k1 == k2
    # Distintas instancias del cache también dan la misma key:
    c1 = MinHashCache(tmp_path / "a")
    c2 = MinHashCache(tmp_path / "b")
    assert c1.compute_key(df, 128, 3, 42) == c2.compute_key(df, 128, 3, 42)


def test_key_cambia_con_num_perm():
    """Cambiar num_perm debe producir key distinta — son firmas incompatibles."""
    df = _sample_df(50)
    k128 = MinHashCache.compute_key(df, 128, 3, 42)
    k256 = MinHashCache.compute_key(df, 256, 3, 42)
    assert k128 != k256


def test_key_cambia_con_ngram():
    df = _sample_df(50)
    assert MinHashCache.compute_key(df, 128, 3, 42) != MinHashCache.compute_key(df, 128, 4, 42)


def test_key_cambia_con_seed():
    df = _sample_df(50)
    assert MinHashCache.compute_key(df, 128, 3, 42) != MinHashCache.compute_key(df, 128, 3, 99)


def test_key_cambia_con_contenido():
    """Datasets distintos → keys distintas."""
    df_a = _sample_df(100, seed=1)
    df_b = _sample_df(100, seed=2)
    assert MinHashCache.compute_key(df_a, 128, 3, 42) != MinHashCache.compute_key(df_b, 128, 3, 42)


def test_key_cambia_con_tamano():
    df = _sample_df(100)
    df_truncado = df.iloc[:50].copy()
    assert MinHashCache.compute_key(df, 128, 3, 42) != MinHashCache.compute_key(
        df_truncado, 128, 3, 42
    )


def test_miss_devuelve_none(tmp_path):
    """Cache vacío: get debe devolver None y contar miss."""
    cache = MinHashCache(tmp_path)
    df = _sample_df(50)
    out = cache.get(df, num_perm=128, ngram=3, seed=42)
    assert out is None
    assert cache.misses == 1
    assert cache.hits == 0


def test_put_then_get_devuelve_mismas_firmas(tmp_path):
    """Roundtrip: put + get → array idéntico."""
    cache = MinHashCache(tmp_path)
    df = _sample_df(50)
    sigs = _sample_signatures(50, 128)
    key = cache.put(df, 128, 3, 42, sigs)
    assert isinstance(key, str) and len(key) == 16  # default _KEY_HEX_LEN

    got = cache.get(df, 128, 3, 42)
    assert got is not None
    np.testing.assert_array_equal(got, sigs)
    assert cache.hits == 1
    assert cache.misses == 0


def test_get_con_columna_inexistente_lanza_keyerror(tmp_path):
    """Sin la columna NOMBRE_LIMPIO el cache no puede calcular la key."""
    cache = MinHashCache(tmp_path)
    df = pd.DataFrame({"otra_columna": ["a", "b", "c"]})
    with pytest.raises(KeyError, match="NOMBRE_LIMPIO"):
        cache.get(df, 128, 3, 42)


def test_archivo_corrupto_se_evicta_silenciosamente(tmp_path):
    """Un .npy roto debe tratarse como miss y borrarse para futuros puts."""
    cache = MinHashCache(tmp_path)
    df = _sample_df(50)
    sigs = _sample_signatures(50, 128)
    cache.put(df, 128, 3, 42, sigs)

    # Corromper el archivo.
    key = cache.compute_key(df, 128, 3, 42)
    archivo = tmp_path / f"signatures_{key}.npy"
    archivo.write_bytes(b"esto no es un npy valido")

    got = cache.get(df, 128, 3, 42)
    assert got is None, "Archivo corrupto debería devolver None"
    assert not archivo.exists(), "Archivo corrupto debería haber sido eliminado"
    assert cache.misses == 1


def test_shape_inconsistente_se_evicta(tmp_path):
    """Si por algún motivo el num_perm cambió pero la key colisionó (no debería),
    el get detecta el mismatch y evicta. Simulado manipulando el archivo."""
    cache = MinHashCache(tmp_path)
    df = _sample_df(50)
    sigs_64 = _sample_signatures(50, 64)  # num_perm=64

    # Guardar con num_perm=64, pero pedir get con num_perm=128 — la key será
    # distinta y NO habría match real. Para forzar el caso, guardamos en el
    # path que tendría la key del get con num_perm=128.
    key_128 = cache.compute_key(df, 128, 3, 42)
    np.save(tmp_path / f"signatures_{key_128}.npy", sigs_64)

    got = cache.get(df, 128, 3, 42)
    assert got is None
    assert not (tmp_path / f"signatures_{key_128}.npy").exists()


def test_clear_vacia_el_cache(tmp_path):
    cache = MinHashCache(tmp_path)
    for s in range(5):
        df = _sample_df(50, seed=s)
        cache.put(df, 128, 3, 42, _sample_signatures(50, 128))
    n_evicted = cache.clear()
    assert n_evicted == 5
    assert list(tmp_path.glob("signatures_*.npy")) == []


def test_stats_reporta_hits_y_misses(tmp_path):
    cache = MinHashCache(tmp_path)
    df_a = _sample_df(50, seed=1)
    df_b = _sample_df(50, seed=2)
    sigs = _sample_signatures(50, 128)
    cache.put(df_a, 128, 3, 42, sigs)
    cache.get(df_a, 128, 3, 42)  # hit
    cache.get(df_a, 128, 3, 42)  # hit
    cache.get(df_b, 128, 3, 42)  # miss
    s = cache.stats()
    assert s["hits"] == 2
    assert s["misses"] == 1
    assert abs(s["hit_rate"] - 2 / 3) < 1e-9
    assert s["n_entries"] == 1


def test_eviction_por_tamano(tmp_path):
    """Cuando se excede max_size_gb, entries LRU se evictan."""
    # max_size 0.001 GB = 1 MB. Firmas de 50x128 uint64 = 50 KB.
    # 25 entries ≈ 1.2 MB, debería evictar al menos algunas.
    cache = MinHashCache(tmp_path, max_size_gb=0.001)
    for s in range(25):
        df = _sample_df(50, seed=s)
        cache.put(df, 128, 3, 42, _sample_signatures(50, 128))
        # Pequeña pausa para que mtime sea distinto y el LRU sea determinista
        time.sleep(0.005)
    s = cache.stats()
    assert s["n_entries"] < 25, "Debió evictar al menos una entry"
    assert s["size_gb"] <= 0.001 * 1.1, "Tras eviction el cache debe estar dentro del límite"


def test_escritura_atomica_no_deja_basura(tmp_path):
    """Tras puts exitosos, no debe haber archivos .tmp ni .partial."""
    cache = MinHashCache(tmp_path)
    df = _sample_df(50)
    cache.put(df, 128, 3, 42, _sample_signatures(50, 128))
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == [], f"Archivos temporales sin limpiar: {tmps}"


def test_format_version_invalida_caches_viejos(tmp_path):
    """Si FORMAT_VERSION sube en código, las keys viejas no deberían matchear.

    No podemos manipular FORMAT_VERSION en tests, pero verificamos que la
    constante esté en el hash (sustituyendo manualmente para confirmar).
    """
    # Confirmamos que FORMAT_VERSION existe y se usa en la key:
    df = _sample_df(50)
    k_v1 = MinHashCache.compute_key(df, 128, 3, 42)

    original = MinHashCache.FORMAT_VERSION
    try:
        MinHashCache.FORMAT_VERSION = f"{original}-test-distinto"  # type: ignore[misc]
        k_v2 = MinHashCache.compute_key(df, 128, 3, 42)
    finally:
        MinHashCache.FORMAT_VERSION = original  # type: ignore[misc]
    assert k_v1 != k_v2, (
        "FORMAT_VERSION debe afectar la key — si no, no podemos invalidar "
        "caches viejos al cambiar el formato del .npy."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Integración con DiskBasedLSHEngine
# ─────────────────────────────────────────────────────────────────────────────


def test_engine_acepta_minhash_cache_dir():
    """El constructor debe leer minhash_cache_dir sin romper."""
    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine

    engine = DiskBasedLSHEngine(profile={"minhash_cache_dir": "/tmp/x"})
    assert engine._minhash_cache_dir == Path("/tmp/x")
    assert engine._minhash_cache_max_gb == 5.0  # default


def test_engine_default_no_cache():
    """Sin minhash_cache_dir, el engine debe seguir funcionando sin cache."""
    from record_linkage.engine.lsh.disk_based import DiskBasedLSHEngine

    engine = DiskBasedLSHEngine()
    assert engine._minhash_cache_dir is None


# ═════════════════════════════════════════════════════════════════════════════
# TAREA 2.4 — Lazy import de reporting
# ═════════════════════════════════════════════════════════════════════════════


def test_reporting_no_se_importa_al_importar_orchestrator():
    """Importar Orchestrator no debe arrastrar matplotlib/seaborn/plotly.

    El package reporting carga ~500 MB de dependencias gráficas. Si los
    usuarios solo quieren correr el pipeline (skip_reporting=True), no
    deberían pagar ese costo.

    NOTA: este test asume que se ejecuta en un proceso fresco. Si otros
    tests ya importaron matplotlib, lo detectaríamos como falso positivo.
    Por eso se ejecuta en subproceso aislado.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import sys
        # Importar el orchestrator (camino que un usuario tomaría):
        from record_linkage.pipeline.orchestrator import Orchestrator  # noqa: F401

        # Pasa si NINGUNO de estos módulos pesados quedó importado:
        heavy = ["matplotlib", "matplotlib.pyplot", "seaborn", "plotly"]
        imported = [m for m in heavy if m in sys.modules]
        if imported:
            print("FAIL:" + ",".join(imported))
            sys.exit(1)
        print("OK")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Importar Orchestrator arrastró módulos pesados.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK" in result.stdout
