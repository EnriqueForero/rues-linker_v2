"""record_linkage.evaluation.comparador — Compara dos corridas del banco.

Una mejora solo queda "en firme" si se puede señalar la cifra que subió y la
que no bajó. Este módulo hace esa comparación explícita y la convierte en un
veredicto binario, para que no dependa de mirar dos JSON en paralelo.

Author: Claude (asesor de Enrique Forero)  ·  Date: 2026-08-29  ·  Version: 0.18.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Comparacion", "Umbrales", "cargar_corrida", "comparar"]


@dataclass(frozen=True)
class Umbrales:
    """Cuánto puede empeorar cada métrica antes de considerarse regresión.

    Los valores por defecto son deliberadamente estrictos en calidad (nada de
    precisión perdida) y tolerantes en tiempo (el ruido de un contenedor
    compartido es real y no debe generar falsas alarmas).

    Attributes:
        caida_maxima_f1: puntos de F1 que se admiten perder.
        caida_maxima_precision: puntos de precisión que se admiten perder.
        caida_maxima_recall: puntos de recall que se admiten perder.
        aumento_maximo_tiempo: fracción de tiempo adicional tolerada.
        aumento_maximo_memoria: fracción de RSS adicional tolerada.
        aumento_maximo_fp_negativos: falsos positivos ADICIONALES sobre casos
            negativos que se admiten frente a la base. El criterio es relativo
            a propósito: un conjunto de referencia real tiene negativos que
            ninguna configuración resuelve —dos empresas distintas con nombres
            casi iguales y sin más evidencia— así que exigir cero en absoluto
            reprobaría toda mejora, incluso una que no empeora nada. Lo que
            interesa vigilar es que un cambio no compre recall a costa de
            fusionar entes distintos.
    """

    caida_maxima_f1: float = 0.0
    caida_maxima_precision: float = 0.0
    caida_maxima_recall: float = 0.0
    aumento_maximo_tiempo: float = 0.20
    aumento_maximo_memoria: float = 0.15
    aumento_maximo_fp_negativos: int = 0

    def __post_init__(self) -> None:
        for campo in ("caida_maxima_f1", "caida_maxima_precision", "caida_maxima_recall"):
            if getattr(self, campo) < 0:
                raise ValueError(f"{campo} no puede ser negativo.")


@dataclass(frozen=True)
class Veredicto:
    """Resultado de comparar una métrica concreta."""

    metrica: str
    base: float
    nueva: float
    delta: float
    pasa: bool
    criterio: str

    def linea(self) -> str:
        marca = "✅" if self.pasa else "❌"
        signo = "+" if self.delta >= 0 else ""
        return (
            f"  {marca} {self.metrica:<26} {self.base:>12,.4f} → {self.nueva:>12,.4f}"
            f"   {signo}{self.delta:,.4f}   {self.criterio}"
        )


@dataclass(frozen=True)
class Comparacion:
    """Informe completo de una comparación entre dos corridas."""

    base: str
    nueva: str
    veredictos: tuple[Veredicto, ...] = field(default_factory=tuple)
    huella_igual: bool = False

    @property
    def pasa(self) -> bool:
        """True solo si ninguna métrica vigilada retrocedió."""
        return all(v.pasa for v in self.veredictos)

    def resumen(self) -> str:
        """Informe legible, apto para pegar en la bitácora."""
        ancho = 92
        linea = "═" * ancho
        cabecera = (
            f"{linea}\n"
            f"  COMPARACIÓN   {self.base}  →  {self.nueva}\n"
            f"{linea}\n"
            f"  {'':2}{'MÉTRICA':<26} {'BASE':>12}   {'NUEVA':>12}   {'DELTA':>9}   CRITERIO\n"
            f"{'─' * ancho}\n"
        )
        cuerpo = "\n".join(v.linea() for v in self.veredictos)
        estado = "PASA" if self.pasa else "FALLA"
        huella = "idéntica" if self.huella_igual else "distinta"
        return (
            f"{cabecera}{cuerpo}\n{'─' * ancho}\n"
            f"  Huella de la partición: {huella}\n"
            f"  VEREDICTO: {estado}\n{linea}"
        )


def cargar_corrida(directorio: Path, etiqueta: str) -> dict[str, Any]:
    """Lee el JSON de una corrida guardada.

    Raises:
        FileNotFoundError: si la corrida no existe, nombrando las disponibles.
    """
    ruta = Path(directorio) / f"corrida_{etiqueta}.json"
    if not ruta.is_file():
        disponibles = sorted(
            p.stem.replace("corrida_", "") for p in Path(directorio).glob("corrida_*.json")
        )
        raise FileNotFoundError(
            f"No existe la corrida {etiqueta!r} en {directorio}. Disponibles: {disponibles}"
        )
    return json.loads(ruta.read_text(encoding="utf-8"))


def _veredicto_mayor_o_igual(
    nombre: str, base: float, nueva: float, caida_maxima: float
) -> Veredicto:
    """La métrica no debe bajar más de ``caida_maxima``."""
    delta = nueva - base
    return Veredicto(
        metrica=nombre,
        base=base,
        nueva=nueva,
        delta=delta,
        pasa=delta >= -caida_maxima - 1e-12,
        criterio=f"≥ base − {caida_maxima:g}",
    )


def _veredicto_menor_o_igual(
    nombre: str, base: float, nueva: float, aumento_maximo: float
) -> Veredicto:
    """La métrica no debe subir más de una fracción del valor base."""
    delta = nueva - base
    tope = base * (1 + aumento_maximo)
    return Veredicto(
        metrica=nombre,
        base=base,
        nueva=nueva,
        delta=delta,
        pasa=nueva <= tope + 1e-9,
        criterio=f"≤ base × {1 + aumento_maximo:g}",
    )


def comparar(
    base: dict[str, Any], nueva: dict[str, Any], umbrales: Umbrales | None = None
) -> Comparacion:
    """Compara dos corridas y emite un veredicto por métrica.

    Args:
        base: corrida de referencia (JSON ya cargado).
        nueva: corrida a evaluar.
        umbrales: tolerancias; si es None se usan las estrictas por defecto.

    Returns:
        Comparacion con un veredicto por métrica vigilada.
    """
    umbrales = umbrales or Umbrales()
    datos_base = (base.get("especificacion") or {}).get("datos")
    datos_nueva = (nueva.get("especificacion") or {}).get("datos")
    if datos_base and datos_nueva and datos_base != datos_nueva:
        raise ValueError(
            f"Qué pasó: las corridas {base['etiqueta']!r} ({datos_base}) y "
            f"{nueva['etiqueta']!r} ({datos_nueva}) son de conjuntos distintos.\n"
            "Por qué importa: comparar métricas de dos conjuntos no mide el cambio del "
            "motor; un PASA así no vale nada.\n"
            "Qué hacer: vuelva a correr la nueva con el mismo --datos de la base "
            f"(python scripts/banco.py --etiqueta {nueva['etiqueta']} --datos {datos_base})."
        )
    cb, cn = base["calidad"], nueva["calidad"]
    rb, rn = base["recursos"], nueva["recursos"]

    veredictos = [
        _veredicto_mayor_o_igual(
            "precision", cb["precision"], cn["precision"], umbrales.caida_maxima_precision
        ),
        _veredicto_mayor_o_igual(
            "recall", cb["recall"], cn["recall"], umbrales.caida_maxima_recall
        ),
        _veredicto_mayor_o_igual("f1", cb["f1"], cn["f1"], umbrales.caida_maxima_f1),
        _veredicto_mayor_o_igual("b3_f1", cb["b3_f1"], cn["b3_f1"], umbrales.caida_maxima_f1),
        _veredicto_menor_o_igual(
            "segundos_total",
            rb["segundos_total"],
            rn["segundos_total"],
            umbrales.aumento_maximo_tiempo,
        ),
        _veredicto_menor_o_igual(
            "rss_pico_mib", rb["rss_pico_mib"], rn["rss_pico_mib"], umbrales.aumento_maximo_memoria
        ),
        Veredicto(
            metrica="fp_que_tocan_negativo",
            base=float(cb["fp_que_tocan_negativo"]),
            nueva=float(cn["fp_que_tocan_negativo"]),
            delta=float(cn["fp_que_tocan_negativo"] - cb["fp_que_tocan_negativo"]),
            pasa=(
                cn["fp_que_tocan_negativo"]
                <= cb["fp_que_tocan_negativo"] + umbrales.aumento_maximo_fp_negativos
            ),
            criterio=(
                f"≤ base + {umbrales.aumento_maximo_fp_negativos}"
                if umbrales.aumento_maximo_fp_negativos
                else "≤ base"
            ),
        ),
    ]
    return Comparacion(
        base=base["etiqueta"],
        nueva=nueva["etiqueta"],
        veredictos=tuple(veredictos),
        huella_igual=base["huella"] == nueva["huella"],
    )
