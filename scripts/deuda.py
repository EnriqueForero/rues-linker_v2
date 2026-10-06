"""Trinquete de deuda técnica (F0.8).

Mide sobre ``src/record_linkage/`` cinco conteos de deuda, cada uno con la
lista de ubicaciones ``archivo:línea`` que lo componen, y los compara contra
una referencia versionada (``docs/evidencia/deuda_f0.json``). El CI falla si
CUALQUIER conteo sube; si baja, lo dice y sugiere actualizar la referencia.
La deuda puede quedarse donde está o bajar; nunca subir.

Métricas
--------
``cc_ge_20``
    Funciones con complejidad ciclomática ≥ 20
    (``ruff --select C901`` con ``lint.mccabe.max-complexity=19``).
``except_sin_relanzar``
    Ubicaciones únicas de ``ruff --select BLE001,E722,S110``: ``except
    Exception`` ciego, ``except:`` desnudo y ``try/except/pass``.
``print``
    Llamadas a un nombre ``print`` en la librería, contadas con ``ast``
    (abajo se explica por qué no con ``ruff T201``).
``os_path``
    Ubicaciones únicas de ``ruff --select PTH`` (``os.path`` y afines donde
    corresponde ``pathlib``).
``mypy``
    Número de errores de ``mypy --config-file=pyproject.toml``, parseando
    «Found N errors». Tarda 1–2 minutos; ``--sin-mypy`` lo omite (pruebas).

Por qué ``print`` no se mide con ``ruff T201``
----------------------------------------------
La especificación pedía T201. Medido: T201 ve 11 ``print`` en la librería y
a ojo hay ≈184. La diferencia son los 25 módulos que hacen ``from
..utils.output import safe_print as print``: ruff resuelve el nombre al
alias y T201 no los reporta, así que un ``print(...)`` nuevo en cualquiera
de esos módulos pasaría sin que el trinquete lo viera. Un trinquete ciego al
94 % de la deuda no sirve: ``print`` cuenta con ``ast`` toda llamada cuyo
callee es el nombre ``print``, esté o no aliasado. Es un superconjunto
estricto de T201, no depende de la resolución de nombres de ruff y sigue
sin dependencias nuevas. (``builtins.print`` dentro de ``utils/output.py``,
la implementación de ``safe_print``, es una llamada por atributo y no cuenta.)

Uso
---
::

    python scripts/deuda.py                                   # medir e imprimir
    python scripts/deuda.py --escribir docs/evidencia/deuda_f0.json
    python scripts/deuda.py --referencia docs/evidencia/deuda_f0.json  # CI
    python scripts/deuda.py --sin-mypy                        # rápido

Códigos de salida: 0 (la deuda no sube), 1 (algún conteo sube o la
referencia está incompleta), 2 (no se pudo medir).

``ruff`` y ``mypy`` se invocan con ``sys.executable -m <módulo>`` o, si ese
intérprete no los tiene, con el ejecutable que ``shutil.which`` encuentre.
Nunca por ruta fija.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import functools
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

RAIZ = Path(__file__).resolve().parents[1]
OBJETIVO_POR_DEFECTO = Path("src") / "record_linkage"
REFERENCIA_POR_DEFECTO = Path("docs") / "evidencia" / "deuda_f0.json"

#: Complejidad ciclomática a partir de la cual una función cuenta como deuda.
UMBRAL_CC = 20
#: Orden canónico de las métricas (el JSON y los informes lo respetan).
METRICAS = ("cc_ge_20", "except_sin_relanzar", "print", "os_path", "mypy")

_PATRON_MYPY_RESUMEN = re.compile(r"^Found (\d+) errors?", re.M)
_PATRON_MYPY_ERROR = re.compile(r"^(?P<archivo>[^:\n]+):(?P<linea>\d+):(?:\d+:)? error:", re.M)
_PATRON_UBICACION = re.compile(r"^(?P<archivo>.*):(?P<linea>\d+)$")


class ErrorDeMedicion(RuntimeError):
    """Una herramienta no pudo correr o devolvió algo que no se entiende."""


@dataclass(frozen=True)
class Medicion:
    """Conteos por métrica y, para cada una, sus ubicaciones ``archivo:línea``."""

    conteos: dict[str, int] = field(default_factory=dict)
    ubicaciones: dict[str, list[str]] = field(default_factory=dict)

    def a_dict(self) -> dict[str, Any]:
        return {"conteos": dict(self.conteos), "ubicaciones": dict(self.ubicaciones)}


@dataclass(frozen=True)
class Veredicto:
    """Resultado de comparar una medición contra la referencia."""

    codigo: int
    lineas: list[str]

    @property
    def texto(self) -> str:
        return "\n".join(self.lineas)


# ---------------------------------------------------------------------------
# Herramientas externas
# ---------------------------------------------------------------------------


@functools.cache
def _comando(modulo: str) -> tuple[str, ...]:
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
            f"Sin él no se puede medir la deuda. Instale las extras de desarrollo: "
            f"pip install -e '.[dev]'."
        )
    return (ejecutable,)


def _correr(comando: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        comando,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _version_herramienta(modulo: str) -> str:
    salida = _correr([*_comando(modulo), "--version"], RAIZ)
    return salida.stdout.strip().splitlines()[0] if salida.stdout.strip() else "desconocida"


# ---------------------------------------------------------------------------
# Ubicaciones
# ---------------------------------------------------------------------------


def _clave_ubicacion(ubicacion: str) -> tuple[str, int]:
    """Clave total para ordenar ``archivo:línea`` por archivo y luego por número."""
    m = _PATRON_UBICACION.match(ubicacion)
    if m is None:
        return (ubicacion, 0)
    return (m.group("archivo"), int(m.group("linea")))


def _ubicacion(raiz: Path, archivo: str, linea: int) -> str:
    ruta = Path(archivo)
    if ruta.is_absolute():
        # Fuera de la raíz se deja absoluta: no se pierde, solo no se acorta.
        with contextlib.suppress(ValueError):
            ruta = ruta.relative_to(raiz)
    return f"{ruta.as_posix()}:{linea}"


def _unicas(ubicaciones: set[str]) -> list[str]:
    return sorted(ubicaciones, key=_clave_ubicacion)


# ---------------------------------------------------------------------------
# Medidores
# ---------------------------------------------------------------------------


def ubicaciones_ruff(
    raiz: Path, objetivo: Path, select: str, config: tuple[str, ...] = ()
) -> list[str]:
    """Ubicaciones únicas ``archivo:línea`` que reporta ``ruff --select <select>``.

    ``--isolated`` ignora el ``pyproject.toml`` del repositorio (sus
    ``per-file-ignores`` silencian justamente la deuda que aquí se mide);
    ``config`` añade ajustes puntuales (``lint.mccabe.max-complexity=19``).
    """
    comando = [*_comando("ruff"), "check", "--isolated", "--no-cache", "--exit-zero"]
    comando += ["--select", select, "--output-format", "json"]
    for ajuste in config:
        comando += ["--config", ajuste]
    comando.append(str(objetivo))
    salida = _correr(comando, raiz)
    if salida.returncode != 0:
        raise ErrorDeMedicion(
            f"ruff falló (código {salida.returncode}) midiendo {select} sobre {objetivo}:\n"
            f"{salida.stderr.strip()}\nSin esa medición no hay trinquete; corrija la causa."
        )
    try:
        diagnosticos = json.loads(salida.stdout)
    except json.JSONDecodeError as exc:
        raise ErrorDeMedicion(
            f"ruff no devolvió JSON midiendo {select}: {salida.stdout[:300]!r}"
        ) from exc
    return _unicas(
        {_ubicacion(raiz, d["filename"], int(d["location"]["row"])) for d in diagnosticos}
    )


def ubicaciones_print(raiz: Path, objetivo: Path) -> list[str]:
    """Llamadas a un nombre ``print`` (aliasado o no), por ``ast``. Ver el docstring del módulo."""
    encontradas: set[str] = set()
    for archivo in sorted((raiz / objetivo).rglob("*.py")):
        try:
            arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            raise ErrorDeMedicion(
                f"No se pudo analizar {archivo}: {exc}. Un archivo que no se puede "
                f"analizar no se puede medir; corríjalo antes de seguir."
            ) from exc
        for nodo in ast.walk(arbol):
            if (
                isinstance(nodo, ast.Call)
                and isinstance(nodo.func, ast.Name)
                and nodo.func.id == "print"
            ):
                encontradas.add(_ubicacion(raiz, str(archivo), nodo.lineno))
    return _unicas(encontradas)


def medir_mypy(raiz: Path, objetivo: Path) -> tuple[int, list[str]]:
    """Errores de mypy (el N de «Found N errors») y sus ubicaciones únicas."""
    comando = [*_comando("mypy"), "--config-file=pyproject.toml", str(objetivo)]
    salida = _correr(comando, raiz)
    if salida.returncode not in (0, 1):
        raise ErrorDeMedicion(
            f"mypy falló (código {salida.returncode}) sobre {objetivo}:\n"
            f"{(salida.stdout + salida.stderr).strip()[-1500:]}\n"
            f"Un fallo de mypy no es «cero errores»; corrija la causa."
        )
    resumen = _PATRON_MYPY_RESUMEN.search(salida.stdout)
    if resumen is not None:
        conteo = int(resumen.group(1))
    elif "Success: no issues found" in salida.stdout:
        conteo = 0
    else:
        raise ErrorDeMedicion(
            f"No se entiende la salida de mypy (ni «Found N errors» ni «Success»):\n"
            f"{salida.stdout.strip()[-800:]}"
        )
    ubicaciones = {
        _ubicacion(raiz, m.group("archivo"), int(m.group("linea")))
        for m in _PATRON_MYPY_ERROR.finditer(salida.stdout)
    }
    return conteo, _unicas(ubicaciones)


def medir(raiz: Path, objetivo: Path = OBJETIVO_POR_DEFECTO, sin_mypy: bool = False) -> Medicion:
    """Mide las cinco métricas (cuatro con ``--sin-mypy``) sobre ``raiz/objetivo``."""
    if not (raiz / objetivo).is_dir():
        raise ErrorDeMedicion(
            f"No existe el directorio a medir: {raiz / objetivo}. "
            f"Indique --raiz/--objetivo correctos."
        )
    conteos: dict[str, int] = {}
    ubicaciones: dict[str, list[str]] = {}

    def registrar(nombre: str, lista: list[str], conteo: int | None = None) -> None:
        conteos[nombre] = len(lista) if conteo is None else conteo
        ubicaciones[nombre] = lista

    registrar(
        "cc_ge_20",
        ubicaciones_ruff(
            raiz, objetivo, "C901", config=(f"lint.mccabe.max-complexity={UMBRAL_CC - 1}",)
        ),
    )
    registrar("except_sin_relanzar", ubicaciones_ruff(raiz, objetivo, "BLE001,E722,S110"))
    registrar("print", ubicaciones_print(raiz, objetivo))
    registrar("os_path", ubicaciones_ruff(raiz, objetivo, "PTH"))
    if not sin_mypy:
        conteo_mypy, ubicaciones_mypy = medir_mypy(raiz, objetivo)
        registrar("mypy", ubicaciones_mypy, conteo_mypy)
    return Medicion(conteos, ubicaciones)


# ---------------------------------------------------------------------------
# Comparación
# ---------------------------------------------------------------------------


def _comparar_metrica(
    nombre: str,
    actual: int,
    referencia: int,
    nuevas: list[str],
    ruta_referencia: str,
) -> tuple[bool, list[str]]:
    """Líneas de informe para una métrica; ``True`` si la deuda subió."""
    delta = actual - referencia
    if delta > 0:
        lineas = [f"SUBE  {nombre}: {referencia} → {actual} (+{delta}). Ubicaciones nuevas:"]
        lineas += [f"   - {u}" for u in nuevas] or [
            "   - (ninguna ubicación nueva: más de un hallazgo en una línea ya listada)"
        ]
        return True, lineas
    if delta < 0:
        return False, [
            f"BAJA  {nombre}: {referencia} → {actual} ({delta}). Fije el nuevo techo: "
            f"python scripts/deuda.py --escribir {ruta_referencia}"
        ]
    sufijo = " (mismo total; ubicaciones movidas)" if nuevas else ""
    return False, [f"IGUAL {nombre}: {actual}{sufijo}"]


def comparar(
    actual: dict[str, Any],
    referencia: dict[str, Any],
    ruta_referencia: str = str(REFERENCIA_POR_DEFECTO),
) -> Veredicto:
    """Compara dos diccionarios ``{"conteos": ..., "ubicaciones": ...}``.

    Código 1 si CUALQUIER conteo medido sube o si a la referencia le falta
    una métrica medida (una referencia incompleta no es un techo). Las
    métricas de la referencia que no se midieron (``--sin-mypy``) se omiten
    y se dice.
    """
    conteos_act = actual.get("conteos", {})
    ubic_act = actual.get("ubicaciones", {})
    conteos_ref = referencia.get("conteos", {})
    ubic_ref = referencia.get("ubicaciones", {})
    subio = False
    lineas: list[str] = []
    orden = [m for m in METRICAS if m in conteos_act] + sorted(set(conteos_act) - set(METRICAS))
    for nombre in orden:
        if nombre not in conteos_ref:
            subio = True
            lineas.append(
                f"FALTA {nombre}: la referencia no la tiene (medido {conteos_act[nombre]}). "
                f"Una referencia incompleta no es un techo; regenérela con "
                f"python scripts/deuda.py --escribir {ruta_referencia}"
            )
            continue
        nuevas = _unicas(set(ubic_act.get(nombre, [])) - set(ubic_ref.get(nombre, [])))
        sube, texto = _comparar_metrica(
            nombre, int(conteos_act[nombre]), int(conteos_ref[nombre]), nuevas, ruta_referencia
        )
        subio = subio or sube
        lineas += texto
    for nombre in sorted(set(conteos_ref) - set(conteos_act)):
        lineas.append(
            f"OMITE {nombre}: no se midió en esta corrida (referencia {conteos_ref[nombre]})"
        )
    if subio:
        lineas.append(
            f"DEUDA: FALLA. La referencia {ruta_referencia} es el techo y no entra código que "
            f"lo supere. Corrija las ubicaciones nuevas; si la deuda es deliberada y "
            f"aprobada, explíquelo en el PR y actualice la referencia."
        )
        return Veredicto(1, lineas)
    lineas.append("DEUDA: PASA. Ningún conteo sube.")
    return Veredicto(0, lineas)


# ---------------------------------------------------------------------------
# Referencia (JSON)
# ---------------------------------------------------------------------------


def _version_paquete(raiz: Path) -> str:
    pyproject = raiz / "pyproject.toml"
    if not pyproject.is_file():
        return "desconocida"
    with pyproject.open("rb") as f:
        return str(tomllib.load(f).get("project", {}).get("version", "desconocida"))


def _commit(raiz: Path) -> str:
    git = shutil.which("git")
    if git is None:
        return "desconocido"
    salida = _correr([git, "rev-parse", "--short", "HEAD"], raiz)
    return salida.stdout.strip() if salida.returncode == 0 else "desconocido"


def escribir_referencia(ruta: Path, medicion: Medicion, raiz: Path, objetivo: Path) -> None:
    documento: dict[str, Any] = {
        "version": _version_paquete(raiz),
        "commit": _commit(raiz),
        "fecha": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "objetivo": objetivo.as_posix(),
        "herramientas": {
            "ruff": _version_herramienta("ruff"),
            "mypy": _version_herramienta("mypy") if "mypy" in medicion.conteos else "no medido",
        },
        **medicion.a_dict(),
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(documento, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def leer_referencia(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        raise ErrorDeMedicion(
            f"No existe la referencia {ruta}. Sin referencia no hay techo que comparar. "
            f"Genérela con: python scripts/deuda.py --escribir {ruta}"
        )
    try:
        documento = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorDeMedicion(f"La referencia {ruta} no es JSON válido: {exc}") from exc
    if not isinstance(documento, dict) or "conteos" not in documento:
        raise ErrorDeMedicion(
            f"La referencia {ruta} no tiene la clave `conteos`; regenérela con --escribir."
        )
    return documento


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trinquete de deuda técnica: mide, escribe la referencia o compara contra ella."
    )
    parser.add_argument("--raiz", type=Path, default=RAIZ, help="raíz del repositorio")
    parser.add_argument(
        "--objetivo",
        type=Path,
        default=OBJETIVO_POR_DEFECTO,
        help="directorio a medir, relativo a --raiz (por defecto src/record_linkage)",
    )
    parser.add_argument("--sin-mypy", action="store_true", help="omite la métrica mypy (lenta)")
    parser.add_argument(
        "--escribir", type=Path, metavar="JSON", help="guarda la medición como referencia"
    )
    parser.add_argument(
        "--referencia", type=Path, metavar="JSON", help="compara contra esta referencia (CI)"
    )
    return parser


def _resolver(raiz: Path, ruta: Path) -> Path:
    return ruta if ruta.is_absolute() else raiz / ruta


def main(argv: list[str] | None = None) -> int:
    args = _construir_parser().parse_args(argv)
    raiz: Path = args.raiz.resolve()
    try:
        medicion = medir(raiz, args.objetivo, sin_mypy=args.sin_mypy)
        print(f"Deuda medida en {args.objetivo.as_posix()} (commit {_commit(raiz)}):")
        for nombre, conteo in medicion.conteos.items():
            print(f"   {nombre:<20} {conteo}")
        codigo = 0
        if args.referencia is not None:
            ruta_ref = _resolver(raiz, args.referencia)
            veredicto = comparar(
                medicion.a_dict(), leer_referencia(ruta_ref), args.referencia.as_posix()
            )
            print(veredicto.texto)
            codigo = veredicto.codigo
        if args.escribir is not None:
            ruta = _resolver(raiz, args.escribir)
            escribir_referencia(ruta, medicion, raiz, args.objetivo)
            print(f"Referencia escrita en {ruta}")
    except ErrorDeMedicion as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return codigo


if __name__ == "__main__":
    sys.exit(main())
