"""Tabla de decisión del umbral de cobertura por estrellas (F2.2, ADR-0011).

«Umbral medido, no adivinado»: el umbral de ``cobertura_sin_identificador``
(ADR-0011) no se fija leyendo una tabla y discutiendo; se fija aplicando una
regla escrita a una tabla medida con los dos instrumentos del repositorio
—``scripts/banco.py`` (30.486 filas, macro-F1 por estrato, FP sobre
negativos) y ``scripts/escala.py --tamanos 139k`` (el grupo mayor sin
identificador)— para cada umbral del barrido. Este módulo contiene la regla,
la lectura de las corridas y la escritura de ``docs/evidencia/cobertura_umbral.json``;
``scripts/cobertura_umbral.py`` lo invoca y ``tests/test_cobertura_umbral_f22.py``
comprueba que el JSON del repositorio reproduce con la regla el umbral que
trae el perfil ``produccion_estandar``.

La regla (:func:`elegir_umbral`)
--------------------------------
1. **Compuerta de F2** (:class:`CompuertaF2`, las cifras del plan): macro-F1
   ≥ 0,880 y FP que tocan un negativo ≤ 287 en el banco; grupo mayor sin
   identificador ≤ 50 filas a 139k. Un umbral que no la pasa queda descartado
   con el motivo escrito.
2. **Regla de Enrique entre los que pasan**: unir dos empresas distintas duele
   más que dejar sin unir, así que gana el que **menos une mal**: menos FP que
   tocan un negativo en el banco; a igual cifra, mayor precisión en el banco;
   luego mayor precisión a 139k; luego el umbral más alto. La macro-F1 es el
   piso (la compuerta), no el objetivo: maximizarla entre los que pasan
   elegiría el umbral más bajo admisible, que es el que más fusiona. Esa
   alternativa se calcula y se deja escrita (``alternativa_mayor_macro_f1``)
   para que quien lea la tabla vea el precio de la decisión.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..pipeline.errores import mensaje_accionable
from .banco import FILAS_GRUPO_GRANDE
from .comparador import cargar_corrida

__all__ = [
    "CompuertaF2",
    "Decision",
    "Descarte",
    "MedidaBanco",
    "MedidaEscala",
    "PuntoUmbral",
    "SinUmbralAdmisibleError",
    "armar_tabla",
    "elegir_umbral",
    "escribir_tabla",
    "medida_banco_desde_corrida",
    "puntos_desde_evidencia",
    "puntos_desde_tabla",
]

#: Clave de la perilla, la misma que interpreta ``ConfigCoberturaSinIdentificador``.
CLAVE_PERILLA = "cobertura_sin_identificador"


class SinUmbralAdmisibleError(ValueError):
    """Ningún umbral del barrido pasa la compuerta de F2."""


@dataclass(frozen=True)
class CompuertaF2:
    """Las tres cifras de la compuerta de F2 que atañen a esta perilla (plan v2).

    Attributes:
        macro_f1_minimo: macro-F1 mínima en el banco institucional.
        fp_negativos_maximo: tope de FP que tocan un registro negativo en el banco.
        grupo_mayor_maximo: tope de filas del grupo mayor sin identificador en
            el sintético de 139k (= ``banco.FILAS_GRUPO_GRANDE``).
    """

    macro_f1_minimo: float = 0.880
    fp_negativos_maximo: int = 287
    grupo_mayor_maximo: int = FILAS_GRUPO_GRANDE


@dataclass(frozen=True)
class MedidaBanco:
    """Lo que el banco institucional dice de un umbral (``corrida_<etiqueta>.json``)."""

    precision: float
    recall: float
    f1: float
    macro_f1: float
    fp_que_tocan_negativo: int
    recall_por_regimen: dict[str, float] = field(default_factory=dict)
    calidad_por_estrato: dict[str, dict[str, float]] = field(default_factory=dict)
    huella: str = ""
    etiqueta: str = ""
    segundos_total: float | None = None


@dataclass(frozen=True)
class MedidaEscala:
    """Lo que la compuerta de escala dice de un umbral a un tamaño (``escala_<etiqueta>.json``)."""

    filas: int
    precision: float
    recall: float
    f1: float
    fp_que_tocan_negativo: int
    recall_por_regimen: dict[str, float] = field(default_factory=dict)
    grupo_mayor_sin_identificador: dict[str, int] = field(default_factory=dict)
    huella: str = ""
    etiqueta: str = ""
    segundos_total: float | None = None


@dataclass(frozen=True)
class PuntoUmbral:
    """Un umbral del barrido con sus dos mediciones (la de escala puede faltar)."""

    umbral: float
    banco: MedidaBanco
    escala: MedidaEscala | None = None

    @property
    def grupo_mayor(self) -> int | None:
        """Filas del grupo mayor sin identificador a escala, o None si no se midió."""
        if self.escala is None:
            return None
        return int(self.escala.grupo_mayor_sin_identificador.get("filas", 0))


@dataclass(frozen=True)
class Descarte:
    """Un umbral que no pasó la compuerta y por qué."""

    umbral: float
    motivo: str


@dataclass(frozen=True)
class Decision:
    """Resultado de :func:`elegir_umbral`.

    Attributes:
        umbral: el elegido.
        motivo: por qué ganó entre los candidatos, con las cifras.
        candidatos: los que pasaron la compuerta, en orden de umbral.
        descartados: los que no, con el motivo.
        alternativa_mayor_macro_f1: el que habría ganado maximizando la macro-F1
            entre los candidatos (igual al elegido cuando no hay conflicto).
    """

    umbral: float
    motivo: str
    candidatos: tuple[PuntoUmbral, ...]
    descartados: tuple[Descarte, ...]
    alternativa_mayor_macro_f1: float


def _motivo_descarte(punto: PuntoUmbral, compuerta: CompuertaF2) -> str | None:
    """None si pasa la compuerta; si no, el motivo con las cifras."""
    fallas: list[str] = []
    b = punto.banco
    if not (b.macro_f1 >= compuerta.macro_f1_minimo):
        fallas.append(f"macro-F1 {b.macro_f1:.4f} < {compuerta.macro_f1_minimo:.3f}")
    if b.fp_que_tocan_negativo > compuerta.fp_negativos_maximo:
        fallas.append(
            f"FP sobre negativos {b.fp_que_tocan_negativo} > {compuerta.fp_negativos_maximo}"
        )
    escala = punto.escala
    if escala is not None:
        mayor = int(escala.grupo_mayor_sin_identificador.get("filas", 0))
        if mayor > compuerta.grupo_mayor_maximo:
            fallas.append(
                f"grupo mayor sin identificador {mayor} filas > {compuerta.grupo_mayor_maximo} "
                f"a {escala.filas:,} filas"
            )
    return "; ".join(fallas) or None


def _clave_menos_une_mal(punto: PuntoUmbral) -> tuple[int, float, float, float]:
    """Orden de la regla de Enrique: menor es mejor en cada componente."""
    precision_escala = punto.escala.precision if punto.escala is not None else 0.0
    return (
        punto.banco.fp_que_tocan_negativo,
        -punto.banco.precision,
        -precision_escala,
        -punto.umbral,
    )


def elegir_umbral(
    puntos: Sequence[PuntoUmbral], compuerta: CompuertaF2 = CompuertaF2()
) -> Decision:
    """Aplica la compuerta y la regla de Enrique a la tabla medida.

    Args:
        puntos: un :class:`PuntoUmbral` por umbral del barrido (sin repetir).
        compuerta: cifras de la compuerta; por defecto las del plan.

    Raises:
        ValueError: ante un umbral repetido o una lista vacía.
        SinUmbralAdmisibleError: si ningún umbral pasa la compuerta.
    """
    if not puntos:
        raise ValueError(
            mensaje_accionable(
                "elegir_umbral necesita al menos un punto del barrido y recibió ninguno.",
                "sin mediciones no hay umbral que elegir y el perfil quedaría adivinado.",
                "pase un PuntoUmbral por umbral medido (scripts/cobertura_umbral.py "
                "--umbrales 0.70 0.75 ...).",
            )
        )
    umbrales = [p.umbral for p in puntos]
    if len(set(umbrales)) != len(umbrales):
        raise ValueError(
            mensaje_accionable(
                f"umbral repetido en el barrido: {sorted(umbrales)}.",
                "dos mediciones del mismo umbral no son un barrido y la regla no sabría "
                "cuál cuenta.",
                "pase un PuntoUmbral por umbral; si repitió un valor en --umbrales o una "
                "etiqueta en --etiquetas, corríjalo.",
            )
        )
    ordenados = sorted(puntos, key=lambda p: p.umbral)
    descartados: list[Descarte] = []
    candidatos: list[PuntoUmbral] = []
    for punto in ordenados:
        motivo = _motivo_descarte(punto, compuerta)
        if motivo is None:
            candidatos.append(punto)
        else:
            descartados.append(Descarte(umbral=punto.umbral, motivo=motivo))
    if not candidatos:
        raise SinUmbralAdmisibleError(
            mensaje_accionable(
                "ningún umbral del barrido pasa la compuerta de F2: "
                + "; ".join(f"{d.umbral:g} ({d.motivo})" for d in descartados)
                + ".",
                "sin un umbral admisible la cobertura por estrellas no tiene valor "
                "por defecto medido y el perfil quedaría adivinado.",
                "amplíe el barrido (scripts/cobertura_umbral.py --umbrales ...) o revise "
                "la perilla (ADR-0011) antes de tocar la compuerta.",
            )
        )
    elegido = min(candidatos, key=_clave_menos_une_mal)
    por_macro = max(candidatos, key=lambda p: (p.banco.macro_f1, p.banco.precision))
    sin_escala = [p.umbral for p in candidatos if p.escala is None]
    motivo = (
        f"entre {', '.join(f'{p.umbral:g}' for p in candidatos)} (pasan la compuerta) gana el que "
        f"menos une mal: {elegido.banco.fp_que_tocan_negativo} FP sobre negativos y precisión "
        f"{elegido.banco.precision:.4f} en el banco"
        + (
            f", precisión {elegido.escala.precision:.4f} a {elegido.escala.filas:,} filas"
            if elegido.escala is not None
            else ""
        )
        + "."
    )
    if por_macro.umbral != elegido.umbral:
        motivo += (
            f" Maximizar la macro-F1 habría elegido {por_macro.umbral:g} "
            f"(macro-F1 {por_macro.banco.macro_f1:.4f} frente a {elegido.banco.macro_f1:.4f}) "
            f"a cambio de {por_macro.banco.fp_que_tocan_negativo - elegido.banco.fp_que_tocan_negativo} "
            f"FP sobre negativos más en el banco"
            + (
                f" y {por_macro.escala.fp_que_tocan_negativo - elegido.escala.fp_que_tocan_negativo} "
                f"más a {elegido.escala.filas:,} filas"
                if por_macro.escala is not None and elegido.escala is not None
                else ""
            )
            + ": la regla de Enrique rompe el empate hacia la precisión."
        )
    if sin_escala:
        motivo += (
            f" Umbrales sin medición a escala (grupo mayor no evaluado): "
            f"{', '.join(f'{u:g}' for u in sin_escala)}."
        )
    return Decision(
        umbral=elegido.umbral,
        motivo=motivo,
        candidatos=tuple(candidatos),
        descartados=tuple(descartados),
        alternativa_mayor_macro_f1=por_macro.umbral,
    )


# ── Lectura de las corridas ───────────────────────────────────────────────


def medida_banco_desde_corrida(crudo: Mapping[str, Any]) -> MedidaBanco:
    """Extrae de ``corrida_<etiqueta>.json`` (ya cargado) lo que la tabla necesita."""
    c = crudo["calidad"]
    return MedidaBanco(
        precision=float(c["precision"]),
        recall=float(c["recall"]),
        f1=float(c["f1"]),
        macro_f1=float(c["macro_f1"]),
        fp_que_tocan_negativo=int(c["fp_que_tocan_negativo"]),
        recall_por_regimen=dict(c.get("recall_por_regimen", {})),
        calidad_por_estrato={k: dict(v) for k, v in c.get("calidad_por_estrato", {}).items()},
        huella=str(crudo.get("huella", "")),
        etiqueta=str(crudo.get("etiqueta", "")),
        segundos_total=crudo.get("recursos", {}).get("segundos_total"),
    )


def _medida_escala(entrada: Mapping[str, Any], etiqueta: str) -> MedidaEscala:
    c = entrada["calidad"]
    grupo_mayor = c.get("grupo_mayor_sin_identificador")
    if not grupo_mayor:
        raise ValueError(
            mensaje_accionable(
                f"la corrida de escala {etiqueta!r} no trae calidad.grupo_mayor_sin_identificador.",
                "sin esa cifra la compuerta «grupo mayor ≤ 50» no se puede evaluar.",
                "vuelva a correr scripts/escala.py con el banco de F2.2 o posterior "
                "(evaluar_calidad la calcula).",
            )
        )
    return MedidaEscala(
        filas=int(entrada["filas"]),
        precision=float(c["precision"]),
        recall=float(c["recall"]),
        f1=float(c["f1"]),
        fp_que_tocan_negativo=int(c["fp_que_tocan_negativo"]),
        recall_por_regimen=dict(c.get("recall_por_regimen", {})),
        grupo_mayor_sin_identificador={k: int(v) for k, v in grupo_mayor.items()},
        huella=str(entrada.get("huella", "")),
        etiqueta=etiqueta,
        segundos_total=entrada.get("recursos", {}).get("segundos_total"),
    )


def puntos_desde_evidencia(
    directorio: Path, etiquetas: Mapping[float, str], *, tamano: str = "139k"
) -> list[PuntoUmbral]:
    """Lee ``corrida_<etiqueta>.json`` (obligatoria) y ``escala_<etiqueta>.json`` (opcional).

    Args:
        directorio: carpeta donde los dos scripts dejaron sus JSON.
        etiquetas: umbral → etiqueta usada en las dos corridas.
        tamano: entrada de ``escala_<etiqueta>.json["tamanos"]`` que se lee.

    Raises:
        FileNotFoundError: si falta la corrida del banco de algún umbral.
        ValueError: si la corrida del banco no se corrió con ese umbral.
    """
    directorio = Path(directorio)
    puntos: list[PuntoUmbral] = []
    for umbral, etiqueta in sorted(etiquetas.items()):
        corrida = cargar_corrida(directorio, etiqueta)
        _verificar_umbral(corrida.get("especificacion", {}).get("ajustes_perfil"), umbral, etiqueta)
        escala: MedidaEscala | None = None
        ruta_escala = directorio / f"escala_{etiqueta}.json"
        if ruta_escala.is_file():
            documento = json.loads(ruta_escala.read_text(encoding="utf-8"))
            _verificar_umbral(documento.get("ajustes_perfil"), umbral, etiqueta)
            entrada = documento.get("tamanos", {}).get(tamano)
            if entrada is not None:
                escala = _medida_escala(entrada, etiqueta)
        puntos.append(
            PuntoUmbral(
                umbral=float(umbral), banco=medida_banco_desde_corrida(corrida), escala=escala
            )
        )
    return puntos


def _verificar_umbral(ajustes: Any, umbral: float, etiqueta: str) -> None:
    """La corrida dice con qué umbral se midió; si no coincide, la tabla mentiría."""
    if ajustes is None:
        return  # corrida sin ajustes: el perfil decide; se confía en la etiqueta
    medido = ajustes.get(CLAVE_PERILLA, {}) if isinstance(ajustes, Mapping) else {}
    if isinstance(medido, Mapping) and "umbral" in medido:
        if not math.isclose(float(medido["umbral"]), umbral, abs_tol=1e-9):
            raise ValueError(
                f"la corrida {etiqueta!r} se midió con umbral {medido['umbral']} y la tabla "
                f"la asigna a {umbral}; corrija las etiquetas del barrido."
            )


def puntos_desde_tabla(tabla: Mapping[str, Any]) -> list[PuntoUmbral]:
    """Reconstruye los puntos desde ``cobertura_umbral.json`` (para re-aplicar la regla)."""
    puntos: list[PuntoUmbral] = []
    for fila in tabla["umbrales"]:
        escala = fila.get("escala")
        puntos.append(
            PuntoUmbral(
                umbral=float(fila["umbral"]),
                banco=MedidaBanco(**fila["banco"]),
                escala=MedidaEscala(**escala) if escala else None,
            )
        )
    return puntos


# ── La tabla ──────────────────────────────────────────────────────────────


def armar_tabla(
    puntos: Sequence[PuntoUmbral],
    *,
    compuerta: CompuertaF2 = CompuertaF2(),
    nota: str = "",
    sin_estrellas: MedidaBanco | None = None,
) -> dict[str, Any]:
    """Compone el documento JSON de decisión: filas medidas + decisión + regla.

    Args:
        puntos: el barrido medido.
        compuerta: cifras de la compuerta aplicada.
        nota: contexto de la medición (máquina, commit, concurrencia).
        sin_estrellas: la corrida con la perilla apagada, como referencia.
    """
    decision = elegir_umbral(puntos, compuerta)
    return {
        "perilla": f"{CLAVE_PERILLA}.umbral",
        "adr": "docs/adr/0011-cobertura-por-estrellas-en-linkage.md",
        "nota": nota,
        "compuerta": asdict(compuerta),
        "regla": (
            "Pasan la compuerta: macro-F1 >= macro_f1_minimo y FP sobre negativos <= "
            "fp_negativos_maximo en el banco institucional, grupo mayor sin identificador <= "
            "grupo_mayor_maximo filas a 139k. Entre los que pasan gana el que menos une mal "
            "(regla de Enrique): menos FP sobre negativos; a igual cifra, mayor precisión en el "
            "banco, luego a 139k, luego el umbral más alto."
        ),
        "sin_estrellas": None if sin_estrellas is None else asdict(sin_estrellas),
        "umbrales": [
            {
                "umbral": p.umbral,
                "pasa_compuerta": all(d.umbral != p.umbral for d in decision.descartados),
                "banco": asdict(p.banco),
                "escala": None if p.escala is None else asdict(p.escala),
            }
            for p in sorted(puntos, key=lambda p: p.umbral)
        ],
        "decision": {
            "umbral": decision.umbral,
            "motivo": decision.motivo,
            "alternativa_mayor_macro_f1": decision.alternativa_mayor_macro_f1,
            "descartados": [asdict(d) for d in decision.descartados],
        },
    }


def escribir_tabla(tabla: Mapping[str, Any], destino: Path) -> Path:
    """Escribe la tabla como JSON estricto (sin NaN) y devuelve la ruta."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(_sin_nan(dict(tabla)), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return destino


def _sin_nan(valor: Any) -> Any:
    """NaN → None recursivamente (un estrato sin pares deja F1 NaN)."""
    if isinstance(valor, float) and math.isnan(valor):
        return None
    if isinstance(valor, dict):
        return {k: _sin_nan(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_sin_nan(v) for v in valor]
    return valor
