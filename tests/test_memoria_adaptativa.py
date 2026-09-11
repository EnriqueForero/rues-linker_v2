"""Tests del AdaptiveMemoryManager porcentual (v0.12.0, cierre de H4).

Hasta 0.11.x los umbrales eran GB absolutos con la comparación invertida:
"crítico" = quedar con menos de 9 GB LIBRES (casi siempre cierto en Colab
Free) y la rama warning (<8 GB) era inalcanzable. Medido en la auditoría
(E11): con 7.1 GB libres de 7.8 el gestor declaraba "memoria crítica" y
recortaba los batches de 100.000 a 5.000.

Contrato v0.12.0 (porcentaje de RAM disponible):
    >35% libre → config original (restauración con histéresis)
    <25% libre → reducir batch sizes (rama ANTES muerta, ahora viva)
    <12% libre → modo emergencia
"""

from __future__ import annotations

from collections import namedtuple

import pytest

from record_linkage.utils import memory as memoria_mod
from record_linkage.utils.memory import AdaptiveMemoryManager

_Mem = namedtuple("_Mem", ["total", "available", "percent", "used"])

_GB = 1024**3


def _fake_mem(monkeypatch, total_gb: float, available_gb: float) -> None:
    fake = _Mem(
        total=int(total_gb * _GB),
        available=int(available_gb * _GB),
        percent=100.0 * (1 - available_gb / total_gb),
        used=int((total_gb - available_gb) * _GB),
    )
    monkeypatch.setattr(memoria_mod.psutil, "virtual_memory", lambda: fake)


_CONFIG = {"batch_size": 100_000, "lsh_batch_size": 50_000, "chunk_size": 150_000}


def test_memoria_holgada_no_degrada(monkeypatch):
    """Colab recién iniciado (~85% libre): la config NO se toca.

    Este es exactamente el escenario E11 que en 0.11.x entraba en emergencia.
    """
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=10.8)
    amm = AdaptiveMemoryManager()
    out = amm.monitor_and_adapt(dict(_CONFIG))
    assert out["batch_size"] == 100_000
    assert "max_workers" not in out  # ninguna marca de emergencia


def test_memoria_media_reduce_batches(monkeypatch):
    """Entre 12% y 25% libre: reduce batches a la mitad (rama antes muerta)."""
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=2.2)  # ~17% libre
    amm = AdaptiveMemoryManager()
    out = amm.monitor_and_adapt(dict(_CONFIG))
    assert out["batch_size"] == 50_000
    assert out["lsh_batch_size"] == 25_000
    assert "max_workers" not in out  # reducido, pero NO emergencia


def test_memoria_critica_entra_en_emergencia(monkeypatch):
    """Menos del 12% libre: modo emergencia."""
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=1.0)  # ~8% libre
    amm = AdaptiveMemoryManager()
    out = amm.monitor_and_adapt(dict(_CONFIG))
    assert out["batch_size"] == 5_000
    assert out["max_workers"] == 1


def test_restauracion_con_histeresis(monkeypatch):
    """Tras una emergencia, la config original vuelve cuando hay holgura real."""
    amm = AdaptiveMemoryManager()
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=1.0)
    degradada = amm.monitor_and_adapt(dict(_CONFIG))
    assert degradada["batch_size"] == 5_000
    # 30% libre: por encima de warning (25%) pero DENTRO de la histéresis (35%):
    # se mantiene la config degradada que se le pasa (no oscila).
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=3.8)
    intermedia = amm.monitor_and_adapt(dict(degradada))
    assert intermedia["batch_size"] == 5_000
    # 60% libre: holgura clara → restaura la original.
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=7.6)
    restaurada = amm.monitor_and_adapt(dict(degradada))
    assert restaurada["batch_size"] == 100_000


def test_es_portable_entre_tamanos_de_maquina(monkeypatch):
    """El mismo % rige en una máquina de 64 GB (los GB absolutos no)."""
    _fake_mem(monkeypatch, total_gb=64.0, available_gb=48.0)  # 75% libre
    amm = AdaptiveMemoryManager()
    assert amm.monitor_and_adapt(dict(_CONFIG))["batch_size"] == 100_000
    _fake_mem(monkeypatch, total_gb=64.0, available_gb=5.0)  # ~8% libre
    assert amm.monitor_and_adapt(dict(_CONFIG))["batch_size"] == 5_000


def test_kwargs_legados_se_aceptan_pero_no_rigen(monkeypatch):
    """La firma 0.11.x no rompe, pero su semántica invertida ya no aplica."""
    _fake_mem(monkeypatch, total_gb=12.7, available_gb=7.1)  # E11: 56% libre
    amm = AdaptiveMemoryManager(warning_threshold_gb=8.0, critical_threshold_gb=9.0)
    out = amm.monitor_and_adapt(dict(_CONFIG))
    assert out["batch_size"] == 100_000  # en 0.11.x esto daba 5_000


def test_umbrales_invalidos_lanzan():
    with pytest.raises(ValueError, match="critical_pct"):
        AdaptiveMemoryManager(warning_pct=10.0, critical_pct=20.0)
