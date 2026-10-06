"""Carga un script de ``scripts/`` como módulo para probarlo.

Los scripts no forman parte del paquete, así que se cargan por ruta. Esta es
la única copia de ese mecanismo (regla: una regla se escribe una vez); las
pruebas de ``scripts/*.py`` lo importan en lugar de repetirlo.

El módulo se registra en ``sys.modules`` ANTES de ejecutarse: los
``@dataclass`` con ``from __future__ import annotations`` resuelven sus
anotaciones buscando el módulo por nombre y, sin el registro, fallan.

``scripts/`` se añade a ``sys.path`` (al final, para no tapar nada) porque
algunos scripts importan a un hermano (``deuda.py`` y ``reglas_estrictas.py``
comparten ``herramientas_lint``); al ejecutarlos con ``python scripts/x.py``
lo resuelve el propio intérprete, que pone ``scripts/`` en ``sys.path[0]``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

DIRECTORIO_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def cargar_script(nombre: str, *, nombre_modulo: str | None = None) -> ModuleType:
    """Devuelve ``scripts/<nombre>.py`` ejecutado como módulo ``nombre_modulo``.

    ``nombre_modulo`` (por defecto ``nombre``) es la clave en ``sys.modules``;
    se usa un nombre distinto cuando el del script podría chocar con un módulo
    ya importado.
    """
    ruta = DIRECTORIO_SCRIPTS / f"{nombre}.py"
    if not ruta.is_file():
        raise FileNotFoundError(f"No existe el script {ruta}; los scripts viven en scripts/.")
    clave = nombre_modulo or nombre
    if str(DIRECTORIO_SCRIPTS) not in sys.path:
        sys.path.append(str(DIRECTORIO_SCRIPTS))
    spec = importlib.util.spec_from_file_location(clave, ruta)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo construir la especificación de módulo para {ruta}.")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[clave] = modulo
    spec.loader.exec_module(modulo)
    return modulo
