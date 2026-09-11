"""Coherencia de versión en múltiples puntos (F0.5, v0.8.0).

Verifica que la versión declarada sea UNA sola en todo el repo:
  1. pyproject.toml            → [project].version
  2. CHANGELOG.md              → primera entrada "## [X.Y.Z]"
  3. importlib.metadata        → distribución instalada (si lo está)
  4. src/.../__init__.py       → el fallback es el CENTINELA, nunca una
                                 versión real (el fallback rancio "3.2.3"
                                 causó una publicación incoherente).

Exit 0 si todo coincide; exit 1 con mensaje accionable si no.
La Celda A.5 del notebook cubre el quinto punto (Celda A ↔ pyproject) en
Colab; los tags de git se verifican en el paso de release.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

RAIZ = Path(__file__).resolve().parents[1]
CENTINELA = "0.0.0+sin.instalar"


def main() -> int:
    with open(RAIZ / "pyproject.toml", "rb") as f:
        v_pyproject = tomllib.load(f)["project"]["version"]

    changelog = (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
    v_changelog = m.group(1) if m else "(sin entrada)"

    init = (RAIZ / "src" / "record_linkage" / "__init__.py").read_text(encoding="utf-8")
    fallback_ok = f'__version__ = "{CENTINELA}"' in init

    errores: list[str] = []
    if v_changelog != v_pyproject:
        errores.append(
            f"CHANGELOG tope [{v_changelog}] != pyproject [{v_pyproject}]. "
            f"Añada/ajuste la entrada del CHANGELOG antes de publicar."
        )
    try:
        from importlib.metadata import version

        v_instalada = version("rues-linker")
        if v_instalada != v_pyproject:
            errores.append(
                f"Instalada [{v_instalada}] != pyproject [{v_pyproject}]. "
                f"Reinstale: pip install -e ."
            )
    except Exception:
        print("aviso: rues-linker no instalado; se omite el punto 3.")
    if not fallback_ok:
        errores.append(
            f"El fallback de __init__ no es el centinela '{CENTINELA}'. "
            f"Nunca use una versión real como fallback (lección 0.7.6)."
        )

    if errores:
        print("ERROR: coherencia de version ROTA:")
        for e in errores:
            print(f"   - {e}")
        return 1
    # Mantener salida ASCII: en Windows no siempre hay una consola UTF-8 y el
    # propio verificador no debe fallar después de comprobar que todo coincide.
    print(f"OK: version coherente en todos los puntos: {v_pyproject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
