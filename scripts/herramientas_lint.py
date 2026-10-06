"""Lo que comparten ``deuda.py`` y ``reglas_estrictas.py`` (una regla se escribe una vez).

Localiza ``ruff`` y ``mypy`` (como módulo del intérprete actual o, si no los
tiene, por el PATH; nunca por ruta fija), los corre y lee el JSON de ruff
como ``Diagnostico``. No es un script: no tiene ``main``.

Los scripts lo importan como módulo hermano (``from herramientas_lint import
...``). Al ejecutarlos con ``python scripts/<x>.py`` el propio ``scripts/`` es
``sys.path[0]``; ``tests/cargar_script.py`` lo añade al cargarlos por ruta.
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: Cómo marca ruff un archivo que no pudo analizar: ``null`` hasta 0.11 e
#: ``invalid-syntax`` desde entonces. Ninguno de los dos es una regla.
CODIGOS_DE_ANALISIS = (None, "invalid-syntax")


class ErrorDeMedicion(RuntimeError):
    """Una herramienta no pudo correr o devolvió algo que no se entiende."""


@dataclass(frozen=True)
class Diagnostico:
    """Un hallazgo de ruff: dónde está, qué regla y el mensaje tal cual."""

    archivo: str
    linea: int
    columna: int
    codigo: str
    mensaje: str


@dataclass(frozen=True)
class Veredicto:
    """Código de salida y las líneas del informe que lo justifican."""

    codigo: int
    lineas: list[str]

    @property
    def texto(self) -> str:
        return "\n".join(self.lineas)


# ---------------------------------------------------------------------------
# Herramientas externas
# ---------------------------------------------------------------------------


@functools.cache
def comando_herramienta(modulo: str) -> tuple[str, ...]:
    """``(intérprete, -m, módulo)`` si el intérprete actual lo tiene; si no, el del PATH."""
    sondeo = subprocess.run(
        [sys.executable, "-m", modulo, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if sondeo.returncode == 0:
        return (sys.executable, "-m", modulo)
    ejecutable = shutil.which(modulo)
    if ejecutable is None:
        raise ErrorDeMedicion(
            f"No se encuentra `{modulo}` ni como módulo de {sys.executable} ni en el PATH. "
            f"Sin él no se puede medir. Instale las extras de desarrollo: "
            f"pip install -e '.[dev]'."
        )
    return (ejecutable,)


def correr(
    comando: Sequence[str], cwd: Path, entrada: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Corre ``comando`` en ``cwd`` y captura su salida (UTF-8; lo ilegible se reemplaza)."""
    return subprocess.run(
        list(comando),
        cwd=cwd,
        input=entrada,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def version_herramienta(modulo: str, cwd: Path) -> str:
    """Primera línea de ``<módulo> --version`` o «desconocida»."""
    salida = correr([*comando_herramienta(modulo), "--version"], cwd)
    return salida.stdout.strip().splitlines()[0] if salida.stdout.strip() else "desconocida"


# ---------------------------------------------------------------------------
# ruff en JSON
# ---------------------------------------------------------------------------


def _leer_diagnosticos(salida_json: str, contexto: str) -> list[Diagnostico]:
    try:
        crudos = json.loads(salida_json)
    except json.JSONDecodeError as exc:
        raise ErrorDeMedicion(f"ruff no devolvió JSON {contexto}: {salida_json[:300]!r}") from exc
    diagnosticos: list[Diagnostico] = []
    for d in crudos:
        if d.get("code") in CODIGOS_DE_ANALISIS:
            # Un error de análisis (sintaxis, codificación) no es una regla:
            # el archivo no se puede medir, y «no medido» no es «cero».
            raise ErrorDeMedicion(
                f"ruff no pudo analizar {d.get('filename')} {contexto}: {d.get('message')}. "
                f"Un archivo que no se puede analizar no se puede medir; corríjalo antes."
            )
        diagnosticos.append(
            Diagnostico(
                archivo=str(d["filename"]),
                linea=int(d["location"]["row"]),
                columna=int(d["location"]["column"]),
                codigo=str(d["code"]),
                mensaje=str(d["message"]),
            )
        )
    return diagnosticos


def _ruff(
    raiz: Path,
    select: str,
    config: Sequence[str],
    argumentos: Sequence[str],
    entrada: str | None,
    contexto: str,
) -> list[Diagnostico]:
    comando = [*comando_herramienta("ruff"), "check", "--isolated", "--no-cache", "--exit-zero"]
    comando += ["--select", select, "--output-format", "json"]
    for ajuste in config:
        comando += ["--config", ajuste]
    comando += list(argumentos)
    salida = correr(comando, raiz, entrada)
    if salida.returncode != 0:
        raise ErrorDeMedicion(
            f"ruff falló (código {salida.returncode}) {contexto}:\n{salida.stderr.strip()}\n"
            f"Sin esa medición no hay veredicto; corrija la causa."
        )
    return _leer_diagnosticos(salida.stdout, contexto)


def diagnosticos_ruff(
    raiz: Path,
    select: str,
    rutas: Sequence[str | Path],
    config: Sequence[str] = (),
    *,
    ignorar_noqa: bool = False,
) -> list[Diagnostico]:
    """Hallazgos de ``ruff check --isolated --select <select>`` sobre ``rutas``.

    ``--isolated`` ignora el ``pyproject.toml`` del repositorio (sus
    ``per-file-ignores`` silencian justamente la deuda que se mide);
    ``ignorar_noqa`` añade ``--ignore-noqa`` (el trinquete de deuda mide la
    deuda real; la compuerta por archivo del CI respeta los ``noqa``).
    ``config`` añade ajustes puntuales (``lint.mccabe.max-complexity=19``).
    """
    argumentos = (["--ignore-noqa"] if ignorar_noqa else []) + [str(r) for r in rutas]
    contexto = f"midiendo {select} sobre {', '.join(str(r) for r in rutas)}"
    return _ruff(raiz, select, config, argumentos, None, contexto)


def diagnosticos_ruff_de_texto(
    raiz: Path,
    select: str,
    nombre: str,
    contenido: str,
    config: Sequence[str] = (),
    contexto: str = "",
) -> list[Diagnostico]:
    """Como ``diagnosticos_ruff``, pero sobre ``contenido`` por stdin como si fuera ``nombre``.

    Sirve para lintar una versión que no está en el árbol de trabajo (``git
    show <rev>:<ruta>``) sin escribir archivos temporales.
    """
    donde = f"midiendo {select} sobre {nombre} {contexto}".rstrip()
    return _ruff(raiz, select, config, ["--stdin-filename", nombre, "-"], contenido, donde)
