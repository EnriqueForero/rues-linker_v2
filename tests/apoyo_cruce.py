"""Apoyo compartido por las pruebas de ``flujo.cruce``.

Vive aquí —y no copiado en cada archivo— para que el constructor mínimo de
``ConfigCruce`` y el logger inerte se escriban una sola vez. Los fixtures con
empresas inventadas están en ``conftest.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from record_linkage.flujo import ConfigCruce


def config_cruce(fuentes: Any, tmp_path: Path, **extra: Any) -> ConfigCruce:
    """``ConfigCruce`` mínima para pruebas: sin smoke, sin Excel, PADRON confiable."""
    base: dict[str, Any] = {
        "fuentes": fuentes,
        "workspace": tmp_path / "salida",
        "confiables": {"PADRON"},
        "dir_trabajo": tmp_path / "trabajo",
        "filas_smoke": 0,
        "exportar_excel": False,
    }
    base.update(extra)
    return ConfigCruce(**base)


class LogNulo:
    """Logger inerte para ejercitar helpers sin ruido en la salida."""

    def info(self, *_a: Any, **_k: Any) -> None: ...

    def warning(self, *_a: Any, **_k: Any) -> None: ...
