"""Rutas y arranque comunes del arnés de paridad de la vinculación.

Todo cuelga del repositorio; nada depende de una máquina. ``ORACULO_DIR`` (por omisión
``<repo>/.oraculo_vinculacion``, fuera de git) guarda el motor original extraído, los insumos inventados y las
referencias que produce ``correr_original.py``. ``LIB_SRC`` es el ``src/`` de la librería a probar.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ORACULO_DIR = Path(os.environ.get("ORACULO_DIR", str(REPO / ".oraculo_vinculacion")))
LIB_SRC = Path(os.environ.get("LIB_SRC", str(REPO / "src")))
CARPETA_MOTOR_ORIGINAL = ORACULO_DIR / "oracle"
CARPETA_PRUEBAS = REPO / "tests"

#: Último commit en que el motor vivía dentro de la celda ``%%writefile`` del notebook 10.
COMMIT_ORIGEN = "bab03c2"
#: SHA-256 del archivo ``vinculador_empresas.py`` tal como sale de esa celda.
SHA256_MOTOR = "b4484359b8e096b574327f13eb8b972b4f49cc5455d93a651bb7c440fcd13959"


def usar_biblioteca() -> None:
    """Pone ``LIB_SRC`` al frente de ``sys.path`` y comprueba que ``record_linkage`` salga de ahí.

    Sin esto, una instalación editable de otro checkout puede ganar el import y el arnés mediría la librería
    equivocada sin avisar.
    """
    sys.path.insert(0, str(LIB_SRC))
    import record_linkage

    if not Path(record_linkage.__file__).resolve().is_relative_to(LIB_SRC.resolve()):
        raise RuntimeError(
            f"record_linkage se importó de {record_linkage.__file__}, no de {LIB_SRC}.\n"
            "Qué hacer: defina LIB_SRC con el src/ del checkout a probar."
        )


def usar_motor_original() -> None:
    """Hace importable ``vinculador_empresas`` (el motor ORIGINAL extraído por ``extraer_oraculo.py``)."""
    if not (CARPETA_MOTOR_ORIGINAL / "vinculador_empresas.py").exists():
        raise SystemExit(
            "Falta el motor original: corra primero scripts/oraculo_vinculacion/extraer_oraculo.py"
        )
    sys.path.insert(0, str(CARPETA_MOTOR_ORIGINAL))


def usar_apoyo_de_pruebas() -> None:
    """Hace importable ``apoyo_vinculacion`` (los insumos inventados de ``tests/``)."""
    sys.path.insert(0, str(CARPETA_PRUEBAS))
