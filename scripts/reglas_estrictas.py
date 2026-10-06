"""Trinquete por archivo y regla para el código que toca un PR.

El paso «Reglas estrictas en código tocado» del CI lintaba con las reglas
estrictas completas (PTH, BLE001, E722, T201, C901 con complejidad máxima
15) TODOS los ``.py`` de ``src/`` y ``scripts/`` que el PR toca, y fallaba
con cualquier violación del archivo, incluidas las heredadas: sobre el PR de
F1 (45 archivos) daba 158 errores, casi todos deuda previa de módulos
legados. Una compuerta que no puede pasar no es estricta: está rota.

Este script la convierte en un trinquete: para cada archivo tocado cuenta,
por regla (código de ruff), las violaciones en la versión BASE y en HEAD, y
falla SOLO si para alguna pareja (archivo, regla) HEAD > BASE. Un archivo
nuevo tiene BASE = 0, así que debe salir limpio. La deuda heredada se tolera
donde está; lo que no puede es crecer, ni siquiera dentro de un archivo que
ya la tenía, ni cambiando una deuda por otra (quitar un BLE001 no compra un
PTH110).

Cómo se compara
---------------
- «Base» es el *merge-base* entre ``--base`` y ``--head``: la misma
  referencia contra la que ``git diff base...head`` decide qué archivos tocó
  el PR. En el CI (``pull_request``) coincide con la punta de la rama destino.
- Las dos versiones se leen con ``git show <rev>:<ruta>`` y se lintan por
  stdin (``ruff --stdin-filename``): no se escribe nada en disco y el árbol de
  trabajo no influye. Un archivo renombrado se compara con su ruta de origen.
- Las ubicaciones nuevas se listan comparando multiconjuntos de
  ``(regla, mensaje)``: una violación que solo cambió de línea no es nueva.
  Para C901 se ignora el «(N > 15)» del mensaje: lo que identifica la
  violación es la función, no su complejidad exacta.
- Para ``scripts/`` se excluye T201: imprimen a propósito.
- Los ``# noqa`` se respetan (no se pasa ``--ignore-noqa``): un ``noqa``
  deliberado y justificado en el PR es la válvula para una función heredada
  que un cambio mínimo empuja por encima de la complejidad máxima. El
  trinquete de deuda (``scripts/deuda.py``) sí los ignora y sigue contando.

Uso
---
::

    python scripts/reglas_estrictas.py --base origin/main               # CI
    python scripts/reglas_estrictas.py --base claude/f0-fundaciones --head HEAD

Códigos de salida: 0 (ningún archivo añade violaciones), 1 (alguna pareja
archivo/regla sube), 2 (no se pudo medir: git o ruff fallaron).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from herramientas_lint import (
    Diagnostico,
    ErrorDeMedicion,
    Veredicto,
    correr,
    diagnosticos_ruff_de_texto,
)

RAIZ = Path(__file__).resolve().parents[1]

#: C901 dice «`f` is too complex (17 > 15)»; el número no identifica la violación.
_PATRON_COMPLEJIDAD = re.compile(r"\s*\(\d+ > \d+\)$")


# ---------------------------------------------------------------------------
# Configuración y resultados
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReglasEstrictas:
    """Qué se exige al código tocado: un solo sitio para reglas, umbral y alcance."""

    select: tuple[str, ...] = ("PTH", "BLE001", "E722", "T201", "C901")
    complejidad_maxima: int = 15
    directorios: tuple[str, ...] = ("src", "scripts")
    #: Reglas que no aplican bajo un directorio: los scripts imprimen a propósito.
    excluidas_por_directorio: Mapping[str, frozenset[str]] = field(
        default_factory=lambda: {"scripts": frozenset({"T201"})}
    )

    @property
    def pathspecs(self) -> list[str]:
        # ':(glob)' es necesario: sin él, 'scripts/**/*.py' no casa con los
        # archivos directamente bajo scripts/ (pathspec de git).
        return [f":(glob){d}/**/*.py" for d in self.directorios]

    @property
    def config_ruff(self) -> tuple[str, ...]:
        return (f"lint.mccabe.max-complexity={self.complejidad_maxima}",)

    def excluidas(self, ruta: str) -> frozenset[str]:
        """Reglas que no se cuentan para ``ruta`` según su primer directorio."""
        partes = Path(ruta).parts
        return self.excluidas_por_directorio.get(partes[0] if partes else "", frozenset())


@dataclass(frozen=True)
class ArchivoTocado:
    """Un ``.py`` que el PR añade, modifica o renombra; ``ruta_base`` es ``None`` si es nuevo."""

    ruta: str
    ruta_base: str | None = None

    @property
    def es_nuevo(self) -> bool:
        return self.ruta_base is None


@dataclass(frozen=True)
class ComparacionArchivo:
    """Violaciones por regla en la base y en HEAD, y las de HEAD sin pareja en la base."""

    archivo: ArchivoTocado
    base: Mapping[str, int]
    head: Mapping[str, int]
    nuevas: tuple[Diagnostico, ...]

    @property
    def reglas(self) -> list[str]:
        return sorted(set(self.base) | set(self.head))

    @property
    def suben(self) -> list[str]:
        return [r for r in self.reglas if self.head.get(r, 0) > self.base.get(r, 0)]

    @property
    def empeora(self) -> bool:
        return bool(self.suben)

    @property
    def mejora(self) -> bool:
        return not self.empeora and sum(self.head.values()) < sum(self.base.values())


@dataclass(frozen=True)
class Evaluacion:
    """Lo medido sobre un PR: referencias, merge-base y una comparación por archivo."""

    base: str
    head: str
    merge_base: str
    head_resuelto: str
    comparaciones: tuple[ComparacionArchivo, ...]
    reglas: ReglasEstrictas

    @property
    def empeoran(self) -> list[ComparacionArchivo]:
        return [c for c in self.comparaciones if c.empeora]

    @property
    def mejoran(self) -> list[ComparacionArchivo]:
        return [c for c in self.comparaciones if c.mejora]


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def _ejecutable_git() -> str:
    git = shutil.which("git")
    if git is None:
        raise ErrorDeMedicion(
            "No se encuentra `git` en el PATH. Sin git no hay base contra la que comparar; "
            "instálelo o corra el script desde un entorno que lo tenga."
        )
    return git


def _git(raiz: Path, *args: str) -> str:
    salida = correr([_ejecutable_git(), *args], raiz)
    if salida.returncode != 0:
        raise ErrorDeMedicion(
            f"`git {' '.join(args)}` falló (código {salida.returncode}) en {raiz}:\n"
            f"{salida.stderr.strip()}\nSin esa lectura no hay veredicto; corrija la causa."
        )
    return salida.stdout


def merge_base(raiz: Path, base: str, head: str) -> str:
    """Commit desde el que se mide lo que ``head`` cambió respecto a ``base``."""
    salida = correr([_ejecutable_git(), "merge-base", base, head], raiz)
    if salida.returncode != 0:
        raise ErrorDeMedicion(
            f"No se pudo calcular el merge-base entre {base} y {head} (git devolvió "
            f"{salida.returncode}: {salida.stderr.strip() or 'sin detalle'}). Sin base no hay "
            f"contra qué comparar. Compruebe que las dos referencias existen; si falta la rama "
            f"destino, tráigala con `git fetch --no-tags origin <rama>` (el CI lo hace antes "
            f"de este paso)."
        )
    return salida.stdout.strip()


def archivos_tocados(
    raiz: Path, desde: str, hasta: str, reglas: ReglasEstrictas
) -> list[ArchivoTocado]:
    """``.py`` bajo los directorios vigilados que ``hasta`` añade, modifica o renombra desde ``desde``."""
    salida = _git(
        raiz,
        "diff",
        "--name-status",
        "-z",
        "-M",
        "--diff-filter=AMR",
        desde,
        hasta,
        "--",
        *reglas.pathspecs,
    )
    campos = salida.split("\0")
    tocados: list[ArchivoTocado] = []
    i = 0
    while i < len(campos) and campos[i]:
        estado = campos[i]
        if estado[0] == "R":
            tocados.append(ArchivoTocado(ruta=campos[i + 2], ruta_base=campos[i + 1]))
            i += 3
        else:
            ruta = campos[i + 1]
            tocados.append(ArchivoTocado(ruta=ruta, ruta_base=None if estado == "A" else ruta))
            i += 2
    return tocados


def diagnosticos_en(raiz: Path, rev: str, ruta: str, reglas: ReglasEstrictas) -> list[Diagnostico]:
    """Hallazgos de las reglas estrictas sobre ``ruta`` tal como está en ``rev``."""
    contenido = _git(raiz, "show", f"{rev}:{ruta}")
    excluidas = reglas.excluidas(ruta)
    hallazgos = diagnosticos_ruff_de_texto(
        raiz, ",".join(reglas.select), ruta, contenido, reglas.config_ruff, contexto=f"en {rev}"
    )
    return [d for d in hallazgos if d.codigo not in excluidas]


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------


def _clave(d: Diagnostico) -> tuple[str, str]:
    """Lo que identifica una violación entre versiones: la regla y el mensaje, no la línea."""
    mensaje = _PATRON_COMPLEJIDAD.sub("", d.mensaje) if d.codigo == "C901" else d.mensaje
    return (d.codigo, mensaje)


def comparar_archivo(
    archivo: ArchivoTocado, en_base: Sequence[Diagnostico], en_head: Sequence[Diagnostico]
) -> ComparacionArchivo:
    """Cuenta por regla y, para las que suben, aparta las de HEAD sin pareja en la base."""
    base = Counter(d.codigo for d in en_base)
    head = Counter(d.codigo for d in en_head)
    suben = {r for r in head if head[r] > base[r]}
    presupuesto = Counter(_clave(d) for d in en_base)
    nuevas: list[Diagnostico] = []
    for d in sorted(en_head, key=lambda x: (x.linea, x.columna)):
        if d.codigo not in suben:
            continue
        clave = _clave(d)
        if presupuesto[clave] > 0:
            presupuesto[clave] -= 1
        else:
            nuevas.append(d)
    return ComparacionArchivo(archivo, dict(base), dict(head), tuple(nuevas))


def evaluar(raiz: Path, base: str, head: str, reglas: ReglasEstrictas) -> Evaluacion:
    """Mide cada archivo tocado en el merge-base y en ``head`` y los compara."""
    mb = merge_base(raiz, base, head)
    head_resuelto = _git(raiz, "rev-parse", "--short", head).strip()
    comparaciones = []
    for archivo in archivos_tocados(raiz, mb, head, reglas):
        en_base = (
            []
            if archivo.ruta_base is None
            else diagnosticos_en(raiz, mb, archivo.ruta_base, reglas)
        )
        en_head = diagnosticos_en(raiz, head, archivo.ruta, reglas)
        comparaciones.append(comparar_archivo(archivo, en_base, en_head))
    return Evaluacion(base, head, mb, head_resuelto, tuple(comparaciones), reglas)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------


def _resumen(c: ComparacionArchivo) -> str:
    if not c.reglas:
        return "limpio (nuevo)" if c.archivo.es_nuevo else "limpio"
    partes = []
    for regla in c.reglas:
        b, h = c.base.get(regla, 0), c.head.get(regla, 0)
        marca = f" ({h - b:+d})" if h != b else ""
        partes.append(f"{regla} {b}→{h}{marca}")
    etiqueta = "EMPEORA" if c.empeora else "baja" if c.mejora else "igual"
    return " · ".join(partes) + f"   [{etiqueta}]"


def informe(ev: Evaluacion) -> Veredicto:
    """Tabla por archivo (base→head por regla, ubicaciones nuevas) y el resultado."""
    donde = ", ".join(f"{d}/" for d in ev.reglas.directorios)
    lineas = [
        f"Reglas estrictas por archivo y regla · base {ev.base} (merge-base {ev.merge_base[:7]}) "
        f"→ {ev.head} ({ev.head_resuelto})"
    ]
    if not ev.comparaciones:
        lineas.append(f"Sin archivos .py tocados en {donde} respecto a {ev.base}. PASA.")
        return Veredicto(0, lineas)
    lineas.append(f"Archivos .py tocados en {donde}: {len(ev.comparaciones)}")
    ancho = max(len(c.archivo.ruta) for c in ev.comparaciones)
    for c in ev.comparaciones:
        lineas.append(f"   {c.archivo.ruta:<{ancho}}  {_resumen(c)}")
        lineas += [
            f"      + {c.archivo.ruta}:{d.linea}:{d.columna} {d.codigo} {d.mensaje}"
            for d in c.nuevas
        ]
    if ev.empeoran:
        nombres = ", ".join(c.archivo.ruta for c in ev.empeoran)
        lineas.append(
            f"RESULTADO: FALLA. Qué pasó: {len(ev.empeoran)} archivo(s) añaden violaciones de reglas "
            f"estrictas respecto a {ev.base}: {nombres}. Por qué importa: la deuda heredada se tolera "
            f"donde está, pero el código que un PR toca no puede dejar más de la que había por regla, "
            f"y un archivo nuevo debe salir limpio. Qué hacer: corrija las ubicaciones marcadas con "
            f"«+»; una excepción deliberada (p. ej. C901 en una función heredada que un cambio mínimo "
            f"empuja sobre el máximo) se anota con `# noqa: <regla>` y se justifica en el PR."
        )
        return Veredicto(1, lineas)
    cola = f"; {len(ev.mejoran)} archivo(s) las reducen." if ev.mejoran else "."
    lineas.append(
        f"RESULTADO: PASA. Ningún archivo añade violaciones por regla respecto a {ev.base}{cola}"
    )
    return Veredicto(0, lineas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Trinquete por archivo y regla: el código que un PR toca no puede añadir "
            "violaciones de las reglas estrictas respecto a su rama base."
        )
    )
    parser.add_argument("--raiz", type=Path, default=RAIZ, help="raíz del repositorio")
    parser.add_argument(
        "--base", required=True, help="referencia de la rama destino (p. ej. origin/main)"
    )
    parser.add_argument("--head", default="HEAD", help="referencia a juzgar (por defecto HEAD)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    try:
        veredicto = informe(evaluar(args.raiz.resolve(), args.base, args.head, ReglasEstrictas()))
    except ErrorDeMedicion as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(veredicto.texto)
    return veredicto.codigo


if __name__ == "__main__":
    sys.exit(main())
