#!/usr/bin/env python
"""escala.py — Compuerta de escala: tiempo, memoria y candidatos a 139k y 463k.

El banco (``scripts/banco.py``) mide calidad sobre 30.486 registros en un
minuto; no ve lo que pasa cuando el bloqueo produce 38 millones de candidatos.
Esta compuerta corre ``record_linkage.api.linkage()`` exactamente como el
banco y como producción —perfil ``produccion_estandar``, ``skip_reporting=True``,
fuentes partidas por ``FUENTE``— sobre dos conjuntos sintéticos grandes con
verdad conocida, y deja en ``docs/evidencia/escala_<etiqueta>.json`` tiempos
por fase, RSS pico, candidatos, pares puntuados, disco y calidad.

Los conjuntos se generan con ``scripts/generar_ground_truth_grande.py``
(semilla 42) y se cachean por parámetros en ``--dir-datos``:

* ``139k`` → ``--empresas-extra 48000 --importadores-extra 9600`` = 139.028 filas
  y 54.136 grupos. Es exactamente el conjunto de la línea base del plan
  (medida en 2 vCPU: L2 268 s, L3 168 s, RSS 1,14 GiB, 12,7 M candidatos). Los
  parámetros se determinaron generando y contando: la razón 5:1 es la misma
  del 463k; 50000/10000 da 144.578 y 48000/6000 da 139.462.
* ``463k`` → ``--empresas-extra 175000 --importadores-extra 35000`` = 463.473 filas
  y 181.136 grupos (verificado).

Nota sobre el generador: ``fabricar_importadores_sinteticos`` solo puede
producir 6.000 nombres distintos (20 × 20 raíces × 15 sufijos), así que
``--importadores-extra`` por encima de 6.000 solo mueve el flujo aleatorio.
Y los parámetros por defecto (900/180) producen 2.939 filas con el generador
actual, no las 12.427 de ``data/ground_truth/ground_truth_grande.csv``, que
salió de una versión anterior del generador.

Reutiliza ``evaluation.banco.correr_banco`` entero —muestreador de RSS,
tiempos y RSS por fase desde el manifiesto, conteo de candidatos y pares
puntuados en SQLite, calidad contra ``ID_GROUP`` alineada por
``ORIGINAL_INDEX``— en vez de rearmar sus piezas: una regla se escribe una vez.
Del comparador reutiliza ``Comparacion``/``Veredicto`` y la función privada
``_veredicto_menor_o_igual`` (F5 la hará pública).

Criterio de ``--comparar``: FALLA (exit 1) si el tiempo total o el de
cualquier fase sube más de 10 %, si el RSS pico sube más de 10 %, o si los
candidatos cambian más de 10 % en cualquier dirección (menos candidatos
también es un cambio: puede ser recall perdido). Es el contrato del plan y
no distingue fases cortas: L4 a 139k dura 2,1 s y su 10 % (0,2 s) está por
debajo del ruido del reloj en un contenedor compartido, así que existe
``--holgura-segundos N`` para quien quiera perdonar a las fases cortas hasta
N segundos absolutos; por defecto es 0 (se mide con la corrida real: con
2 s de holgura, L4 +50 % pasaba). Si una fase corta hace saltar la compuerta
por ruido, repita la corrida antes que aflojar el criterio.

Qué deja una corrida: solo ``escala_<etiqueta>.json`` en ``--evidencia``. El
``prediccion_<etiqueta>_<tamaño>.parquet`` que escribe ``correr_banco`` cae en
el work_dir temporal de la corrida, que se borra al terminar salvo con
``--conservar-trabajo``; los CSV generados quedan en ``--dir-datos``.

USO
    python scripts/escala.py --etiqueta base_f0                 # 139k y 463k
    python scripts/escala.py --etiqueta prueba --tamanos 139k
    python scripts/escala.py --solo-generar
    python scripts/escala.py --comparar base_f0 despues         # exit 1 si FALLA

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-10-06  ·  Version: F0.5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import psutil

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

import record_linkage as rl
from record_linkage.evaluation.banco import Corrida, EspecificacionBanco, correr_banco
from record_linkage.evaluation.comparador import (
    Comparacion,
    Veredicto,
    _veredicto_menor_o_igual,
)

GENERADOR = RAIZ / "scripts" / "generar_ground_truth_grande.py"
EVIDENCIA = RAIZ / "docs" / "evidencia"
DIR_DATOS = Path("/tmp/escala_datos")
PERFIL = "produccion_estandar"
SEMILLA = 42
#: Fracción de aumento tolerada en tiempo, RSS y candidatos.
TOLERANCIA = 0.10
#: Holgura absoluta en segundos para fases cortas. 0 = el 10 % rige para todas
#: las fases, como pide la especificación; se afloja solo con --holgura-segundos.
HOLGURA_SEGUNDOS = 0.0


class ConjuntoInesperadoError(ValueError):
    """El CSV generado o cacheado no tiene las filas que la tabla declara."""


@dataclass(frozen=True)
class ParametrosConjunto:
    """Con qué se invoca el generador y cuántas filas debe producir.

    Attributes:
        empresas_extra: empresas CON_NIT sintéticas adicionales.
        importadores_extra: importadores SIN_NIT sintéticos adicionales.
        semilla: semilla del generador.
        filas_esperadas: filas que debe tener el CSV, o None si no se verifica.
    """

    empresas_extra: int
    importadores_extra: int
    semilla: int = SEMILLA
    filas_esperadas: int | None = None

    @property
    def nombre_archivo(self) -> str:
        """Nombre del CSV cacheado; lleva los parámetros para no confundir conjuntos."""
        return f"gt_s{self.semilla}_e{self.empresas_extra}_i{self.importadores_extra}.csv"


#: Tamaños medidos de la compuerta. Las filas están verificadas (ver docstring).
TAMANOS: dict[str, ParametrosConjunto] = {
    "139k": ParametrosConjunto(
        empresas_extra=48_000, importadores_extra=9_600, filas_esperadas=139_028
    ),
    "463k": ParametrosConjunto(
        empresas_extra=175_000, importadores_extra=35_000, filas_esperadas=463_473
    ),
}


# ── Conjuntos de datos ────────────────────────────────────────────────────


def parsear_tamanos(texto: str) -> tuple[str, ...]:
    """Convierte ``"139k,463k"`` en una tupla validada contra ``TAMANOS``."""
    nombres = tuple(parte.strip() for parte in texto.split(",") if parte.strip())
    desconocidos = [n for n in nombres if n not in TAMANOS]
    if not nombres or desconocidos:
        raise ValueError(
            f"--tamanos recibió {texto!r}; desconocidos: {desconocidos}. "
            f"Los tamaños disponibles son {', '.join(TAMANOS)}."
        )
    return nombres


def contar_filas_csv(ruta: Path) -> int:
    """Filas de datos del CSV (sin la cabecera), leyendo una sola columna."""
    return len(pd.read_csv(ruta, usecols=[0], dtype=str, keep_default_na=False))


def resolver_conjunto(params: ParametrosConjunto, dir_datos: Path) -> tuple[Path, bool]:
    """Devuelve la ruta del CSV, generándolo si no está cacheado.

    La escritura es atómica: el generador escribe en ``<nombre>.parcial`` y
    solo al terminar se renombra, de modo que una corrida interrumpida no deja
    un archivo a medias que la siguiente reutilice como si fuera completo.

    Returns:
        (ruta, generado): ``generado`` es True si hubo que invocar el generador.

    Raises:
        FileNotFoundError: si el generador no está donde debe.
        RuntimeError: si el generador termina con error.
        ConjuntoInesperadoError: si el conteo de filas no es el declarado.
    """
    if not GENERADOR.is_file():
        raise FileNotFoundError(
            f"No existe el generador {GENERADOR}. La compuerta depende de él para "
            f"producir los conjuntos sintéticos; restaure el archivo del repositorio."
        )
    dir_datos = Path(dir_datos)
    dir_datos.mkdir(parents=True, exist_ok=True)
    destino = dir_datos / params.nombre_archivo
    parcial = destino.with_name(destino.name + ".parcial")
    generado = False
    if not destino.is_file():
        parcial.unlink(missing_ok=True)
        orden = [
            sys.executable,
            str(GENERADOR),
            "--salida",
            str(parcial),
            "--empresas-extra",
            str(params.empresas_extra),
            "--importadores-extra",
            str(params.importadores_extra),
            "--semilla",
            str(params.semilla),
        ]
        resultado = subprocess.run(orden, capture_output=True, text=True, check=False)
        if resultado.returncode != 0:
            parcial.unlink(missing_ok=True)
            raise RuntimeError(
                f"El generador falló (código {resultado.returncode}) con "
                f"{' '.join(orden[2:])}.\n{resultado.stderr.strip()[-2000:]}"
            )
        parcial.replace(destino)
        generado = True
    filas = contar_filas_csv(destino)
    if params.filas_esperadas is not None and filas != params.filas_esperadas:
        raise ConjuntoInesperadoError(
            f"{destino.name} tiene {filas:,} filas y la tabla de tamaños declara "
            f"{params.filas_esperadas:,}. Un conjunto distinto hace incomparables las "
            f"corridas. Si el generador cambió a propósito, actualice TAMANOS en "
            f"scripts/escala.py y vuelva a medir la línea base; si no, borre el archivo "
            f"cacheado y regenere."
        )
    return destino, generado


# ── Corrida ───────────────────────────────────────────────────────────────


def _maquina() -> dict[str, Any]:
    """Lo que ``Corrida.entorno`` no trae: la línea base del plan se midió en 2 vCPU."""
    return {
        "vcpu": os.cpu_count(),
        "memoria_total_mib": round(psutil.virtual_memory().total / 1024**2),
    }


@dataclass(frozen=True)
class OpcionesCorrida:
    """Dónde corre y qué conserva una medición.

    Attributes:
        etiqueta: nombre de la corrida (``escala_<etiqueta>.json``).
        dir_datos: caché de los CSV generados.
        dir_evidencia: carpeta del JSON; no recibe ningún otro archivo.
        dir_trabajo: carpeta base (existente) para el work_dir temporal de cada
            tamaño, o None para el temporal del sistema.
        conservar_trabajo: no borrar el work_dir (incluye el parquet de predicción).
        silencioso: bajar el logging del pipeline.
        nota: qué cambió en esta corrida.
    """

    etiqueta: str
    dir_datos: Path = DIR_DATOS
    dir_evidencia: Path = EVIDENCIA
    dir_trabajo: Path | None = None
    conservar_trabajo: bool = False
    silencioso: bool = True
    nota: str = ""

    def __post_init__(self) -> None:
        if self.dir_trabajo is not None and not Path(self.dir_trabajo).is_dir():
            raise FileNotFoundError(
                f"--dir-trabajo {self.dir_trabajo} no existe. Ahí se crea el work_dir de "
                f"cada tamaño (cientos de MB a 463k) y conviene decidirlo antes de generar "
                f"nada; créelo o use el temporal del sistema omitiendo la opción."
            )


def correr_tamano(nombre: str, opciones: OpcionesCorrida) -> dict[str, Any]:
    """Genera (o reutiliza) el conjunto, corre el banco sobre él y arma su entrada."""
    params = TAMANOS[nombre]
    ruta_datos, generado = resolver_conjunto(params, opciones.dir_datos)
    filas = (
        f"{params.filas_esperadas:,} filas"
        if params.filas_esperadas is not None
        else "filas no verificadas"
    )
    print(f"▶ {nombre}: {ruta_datos.name} ({'generado' if generado else 'reutilizado'}, {filas})")
    trabajo = Path(tempfile.mkdtemp(prefix=f"escala_{nombre}_", dir=opciones.dir_trabajo))
    # dir_evidencia=trabajo: el banco deja ahí prediccion_<etiqueta>.parquet
    # (4 MB a 139k, ≈ 13 MB a 463k) y se va con el work_dir; en --evidencia
    # solo entra el JSON.
    espec = EspecificacionBanco(
        etiqueta=f"{opciones.etiqueta}_{nombre}",
        datos=ruta_datos,
        perfil=PERFIL,
        dir_trabajo=trabajo,
        dir_evidencia=trabajo,
        nota=opciones.nota,
    )
    try:
        corrida: Corrida = correr_banco(espec, silencioso=opciones.silencioso)
    finally:
        if not opciones.conservar_trabajo:
            shutil.rmtree(trabajo, ignore_errors=True)
    print(corrida.resumen())
    crudo = corrida.a_dict()
    return {
        "parametros": asdict(params),
        "datos": str(ruta_datos),
        "filas": corrida.calidad.registros,
        "grupos": corrida.calidad.grupos_verdad,
        "version": corrida.version,
        "perfil": corrida.perfil,
        "marca_tiempo": corrida.marca_tiempo,
        "entorno": {**corrida.entorno, "version": corrida.version, **_maquina()},
        "recursos": crudo["recursos"],
        "calidad": crudo["calidad"],
        "huella": corrida.huella,
        "especificacion": crudo["especificacion"],
        "dir_trabajo": str(trabajo) if opciones.conservar_trabajo else None,
    }


def _sin_nan(valor: Any) -> Any:
    """Reemplaza NaN por None recursivamente: el JSON de evidencia es estricto."""
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if isinstance(valor, dict):
        return {k: _sin_nan(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_sin_nan(v) for v in valor]
    return valor


def guardar_evidencia(documento: Mapping[str, Any], directorio: Path, etiqueta: str) -> Path:
    """Escribe ``escala_<etiqueta>.json`` y devuelve su ruta."""
    directorio = Path(directorio)
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / f"escala_{etiqueta}.json"
    destino.write_text(
        json.dumps(_sin_nan(dict(documento)), indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    return destino


def cargar_evidencia(directorio: Path, etiqueta: str) -> dict[str, Any]:
    """Lee ``escala_<etiqueta>.json``; si no existe, nombra las disponibles."""
    ruta = Path(directorio) / f"escala_{etiqueta}.json"
    if not ruta.is_file():
        disponibles = sorted(
            p.stem.removeprefix("escala_") for p in Path(directorio).glob("escala_*.json")
        )
        raise FileNotFoundError(
            f"No existe la corrida de escala {etiqueta!r} en {directorio}. "
            f"Disponibles: {disponibles}"
        )
    return json.loads(ruta.read_text(encoding="utf-8"))


def correr_escala(tamanos: Sequence[str], opciones: OpcionesCorrida) -> Path:
    """Corre cada tamaño y guarda el JSON tras cada uno (un OOM en 463k no pierde el 139k)."""
    documento: dict[str, Any] = {
        "etiqueta": opciones.etiqueta,
        "perfil": PERFIL,
        "semilla": SEMILLA,
        "marca_tiempo": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": rl.__version__,
        "entorno": _maquina(),
        "nota": opciones.nota,
        "tamanos": {},
    }
    destino = guardar_evidencia(documento, opciones.dir_evidencia, opciones.etiqueta)
    for nombre in tamanos:
        documento["tamanos"][nombre] = correr_tamano(nombre, opciones)
        destino = guardar_evidencia(documento, opciones.dir_evidencia, opciones.etiqueta)
        print(f"💾 Evidencia parcial: {destino}")
    print(tabla_resumen(documento))
    return destino


def tabla_resumen(documento: Mapping[str, Any]) -> str:
    """Una línea por tamaño: filas, tiempos por fase, RSS, candidatos, pares, F1."""
    filas_tabla = []
    fases: list[str] = []
    for entrada in documento["tamanos"].values():
        for fase in entrada["recursos"].get("segundos_por_fase", {}):
            if fase not in fases:
                fases.append(fase)
    cabecera = (
        f"{'tamaño':<7}{'filas':>9}{'total s':>10}"
        + "".join(f"{f.split('_')[0]:>9}" for f in fases)
        + f"{'RSS MiB':>10}{'candidatos':>13}{'pares':>10}{'F1':>8}"
    )
    for nombre, entrada in documento["tamanos"].items():
        r, c = entrada["recursos"], entrada["calidad"]
        por_fase = r.get("segundos_por_fase", {})
        filas_tabla.append(
            f"{nombre:<7}{entrada['filas']:>9,}{r['segundos_total']:>10,.1f}"
            + "".join(f"{por_fase.get(f, float('nan')):>9,.1f}" for f in fases)
            + f"{r['rss_pico_mib']:>10,.0f}{(r.get('candidatos') or 0):>13,}"
            f"{(r.get('pares_scoreados') or 0):>10,}{c['f1']:>8.4f}"
        )
    linea = "═" * len(cabecera)
    return (
        f"{linea}\n  ESCALA · {documento['etiqueta']}\n{linea}\n{cabecera}\n"
        + "\n".join(filas_tabla)
        + f"\n{linea}"
    )


# ── Comparación ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InformeEscala:
    """Veredicto de ``--comparar``: una ``Comparacion`` por tamaño."""

    base: str
    nueva: str
    tolerancia: float
    comparaciones: tuple[Comparacion, ...] = field(default_factory=tuple)
    calidad: tuple[str, ...] = field(default_factory=tuple)

    @property
    def pasa(self) -> bool:
        """True solo si ningún tamaño retrocedió en tiempo, memoria o candidatos."""
        return all(c.pasa for c in self.comparaciones)

    def resumen(self) -> str:
        """Tabla legible por tamaño más el veredicto global."""
        partes = [c.resumen() for c in self.comparaciones]
        partes.append("  Calidad (informativa, no decide):")
        partes.extend(f"    {linea}" for linea in self.calidad)
        estado = "PASA" if self.pasa else "FALLA"
        partes.append(
            f"  VEREDICTO ESCALA {self.base} → {self.nueva} "
            f"(tolerancia {self.tolerancia:.0%}): {estado}"
        )
        return "\n".join(partes)


def _sin_dato(nombre: str, base: float | None, nueva: float | None, criterio: str) -> Veredicto:
    """Un valor ausente no pasa en silencio: se ve en la tabla y hace fallar."""
    b = float("nan") if base is None else float(base)
    n = float("nan") if nueva is None else float(nueva)
    return Veredicto(
        metrica=nombre, base=b, nueva=n, delta=float("nan"), pasa=False, criterio=criterio
    )


def _veredicto_tiempo(
    nombre: str, base: float | None, nueva: float | None, tolerancia: float, holgura: float
) -> Veredicto:
    """El tiempo no debe subir más de ``tolerancia`` del base (o ``holgura`` s si es mayor)."""
    if base is None or nueva is None:
        return _sin_dato(
            nombre, base, nueva, "sin dato" if nueva is None and base is not None else "sin base"
        )
    tope = base + max(base * tolerancia, holgura)
    criterio = f"≤ base × {1 + tolerancia:g}"  # noqa: RUF001  (mismo símbolo que el comparador)
    if holgura > 0:
        criterio += f" (o +{holgura:g} s)"
    return Veredicto(
        metrica=nombre,
        base=base,
        nueva=nueva,
        delta=nueva - base,
        pasa=nueva <= tope + 1e-9,
        criterio=criterio,
    )


def _veredicto_dentro_de(
    nombre: str, base: float | None, nueva: float | None, tolerancia: float
) -> Veredicto:
    """El valor no debe cambiar más de ``tolerancia`` en ninguna dirección."""
    if base is None or nueva is None:
        return _sin_dato(nombre, base, nueva, "sin dato")
    return Veredicto(
        metrica=nombre,
        base=base,
        nueva=nueva,
        delta=nueva - base,
        pasa=abs(nueva - base) <= base * tolerancia + 1e-9,
        criterio=f"base × [{1 - tolerancia:g}, {1 + tolerancia:g}]",  # noqa: RUF001
    )


def _comparar_tamano(
    nombre: str,
    base: Mapping[str, Any],
    nueva: Mapping[str, Any],
    etiquetas: tuple[str, str],
    tolerancia: float,
    holgura: float,
) -> Comparacion:
    rb, rn = base["recursos"], nueva["recursos"]
    fases_b, fases_n = rb.get("segundos_por_fase", {}), rn.get("segundos_por_fase", {})
    veredictos = [
        _veredicto_tiempo(
            "segundos_total",
            rb.get("segundos_total"),
            rn.get("segundos_total"),
            tolerancia,
            holgura,
        )
    ]
    for fase, segundos in fases_b.items():
        if fase in fases_n:
            veredictos.append(_veredicto_tiempo(fase, segundos, fases_n[fase], tolerancia, holgura))
        else:
            veredictos.append(_sin_dato(fase, segundos, None, "ausente en la nueva corrida"))
    for fase, segundos in fases_n.items():
        if fase not in fases_b:
            veredictos.append(
                Veredicto(
                    metrica=fase,
                    base=float("nan"),
                    nueva=float(segundos),
                    delta=float("nan"),
                    pasa=True,
                    criterio="fase nueva sin base: la vigila el total",
                )
            )
    if rb.get("rss_pico_mib") is None or rn.get("rss_pico_mib") is None:
        veredictos.append(
            _sin_dato("rss_pico_mib", rb.get("rss_pico_mib"), rn.get("rss_pico_mib"), "sin dato")
        )
    else:
        veredictos.append(
            _veredicto_menor_o_igual(
                "rss_pico_mib", rb["rss_pico_mib"], rn["rss_pico_mib"], tolerancia
            )
        )
    veredictos.append(
        _veredicto_dentro_de("candidatos", rb.get("candidatos"), rn.get("candidatos"), tolerancia)
    )
    return Comparacion(
        base=f"{etiquetas[0]} [{nombre}]",
        nueva=f"{etiquetas[1]} [{nombre}]",
        veredictos=tuple(veredictos),
        huella_igual=base.get("huella") == nueva.get("huella"),
    )


def comparar_escala(
    base: Mapping[str, Any],
    nueva: Mapping[str, Any],
    tolerancia: float = TOLERANCIA,
    holgura_segundos: float = HOLGURA_SEGUNDOS,
    tamanos: Sequence[str] | None = None,
) -> InformeEscala:
    """Compara dos JSON de escala ya cargados, tamaño por tamaño.

    Args:
        base: corrida de referencia (``escala_<etiqueta>.json`` ya cargado).
        nueva: corrida a evaluar.
        tolerancia: fracción de aumento tolerada (tiempo, RSS, candidatos).
        holgura_segundos: holgura absoluta para tiempos cortos.
        tamanos: qué tamaños comparar; por defecto todos los de ``base``.

    Raises:
        ValueError: si un tamaño pedido falta en alguna de las dos corridas.
    """
    if tolerancia < 0 or holgura_segundos < 0:
        raise ValueError("tolerancia y holgura_segundos no pueden ser negativas.")
    pedidos = tuple(tamanos) if tamanos else tuple(base["tamanos"])
    if not pedidos or not base["tamanos"] or not nueva["tamanos"]:
        # Una comparación sin tamaños «pasaría» en vacío: eso no es una compuerta.
        raise ValueError(
            "No hay tamaños que comparar: la corrida base trae "
            f"{sorted(base['tamanos'])} y la nueva {sorted(nueva['tamanos'])}. "
            "Corra scripts/escala.py --etiqueta <nombre> con --tamanos antes de comparar."
        )
    faltan = [t for t in pedidos if t not in base["tamanos"] or t not in nueva["tamanos"]]
    if faltan:
        raise ValueError(
            f"Los tamaños {faltan} no están en las dos corridas "
            f"(base: {sorted(base['tamanos'])}, nueva: {sorted(nueva['tamanos'])}). "
            f"Corra el tamaño que falta o restrinja --tamanos."
        )
    etiquetas = (str(base.get("etiqueta", "base")), str(nueva.get("etiqueta", "nueva")))
    comparaciones = tuple(
        _comparar_tamano(
            t, base["tamanos"][t], nueva["tamanos"][t], etiquetas, tolerancia, holgura_segundos
        )
        for t in pedidos
    )
    calidad = tuple(
        f"{t}: F1 {base['tamanos'][t]['calidad']['f1']:.4f} → "
        f"{nueva['tamanos'][t]['calidad']['f1']:.4f}   "
        f"precision {base['tamanos'][t]['calidad']['precision']:.4f} → "
        f"{nueva['tamanos'][t]['calidad']['precision']:.4f}   "
        f"recall {base['tamanos'][t]['calidad']['recall']:.4f} → "
        f"{nueva['tamanos'][t]['calidad']['recall']:.4f}"
        for t in pedidos
    )
    return InformeEscala(
        base=etiquetas[0],
        nueva=etiquetas[1],
        tolerancia=tolerancia,
        comparaciones=comparaciones,
        calidad=calidad,
    )


# ── CLI ───────────────────────────────────────────────────────────────────


def construir_parser() -> argparse.ArgumentParser:
    """Define la interfaz de línea de comandos."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--etiqueta", help="nombre corto de la corrida (escala_<etiqueta>.json)")
    p.add_argument(
        "--tamanos",
        default=None,
        help=(
            f"tamaños a correr o comparar, separados por coma (defecto: {','.join(TAMANOS)} "
            f"al correr; al comparar, todos los de la corrida base)"
        ),
    )
    p.add_argument("--dir-datos", type=Path, default=DIR_DATOS, help="caché de CSV generados")
    p.add_argument("--evidencia", type=Path, default=EVIDENCIA, help="carpeta de los JSON")
    p.add_argument(
        "--dir-trabajo",
        type=Path,
        default=None,
        help="carpeta base para el work_dir temporal de cada corrida (defecto: temporal del sistema)",
    )
    p.add_argument(
        "--conservar-trabajo",
        action="store_true",
        help="no borrar el work_dir al terminar (incluye prediccion_<etiqueta>_<tamaño>.parquet)",
    )
    p.add_argument("--solo-generar", action="store_true", help="generar/cachear los CSV y salir")
    p.add_argument("--verboso", action="store_true", help="dejar pasar el logging del pipeline")
    p.add_argument("--nota", default="", help="qué cambió en esta corrida")
    p.add_argument(
        "--comparar", nargs=2, metavar=("BASE", "NUEVA"), help="compara dos corridas guardadas"
    )
    p.add_argument(
        "--tolerancia",
        type=float,
        default=TOLERANCIA,
        help=f"fracción de aumento tolerada al comparar (defecto {TOLERANCIA})",
    )
    p.add_argument(
        "--holgura-segundos",
        type=float,
        default=HOLGURA_SEGUNDOS,
        help=(
            f"holgura absoluta en segundos para fases cortas (defecto {HOLGURA_SEGUNDOS:g}: "
            f"el 10 %% rige para todas las fases)"
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada."""
    args = construir_parser().parse_args(argv)
    tamanos = parsear_tamanos(args.tamanos) if args.tamanos else None

    if args.comparar:
        base, nueva = (cargar_evidencia(args.evidencia, e) for e in args.comparar)
        informe = comparar_escala(
            base,
            nueva,
            tolerancia=args.tolerancia,
            holgura_segundos=args.holgura_segundos,
            tamanos=tamanos,
        )
        print(informe.resumen())
        return 0 if informe.pasa else 1

    if args.solo_generar:
        for nombre in tamanos or tuple(TAMANOS):
            ruta, generado = resolver_conjunto(TAMANOS[nombre], args.dir_datos)
            print(f"{nombre}: {ruta} ({'generado' if generado else 'reutilizado'})")
        return 0

    if not args.etiqueta:
        raise SystemExit("--etiqueta es obligatoria (o use --comparar / --solo-generar)")

    opciones = OpcionesCorrida(
        etiqueta=args.etiqueta,
        dir_datos=args.dir_datos,
        dir_evidencia=args.evidencia,
        dir_trabajo=args.dir_trabajo,
        conservar_trabajo=args.conservar_trabajo,
        silencioso=not args.verboso,
        nota=args.nota,
    )
    destino = correr_escala(tamanos or tuple(TAMANOS), opciones)
    print(f"\n💾 Evidencia: {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
